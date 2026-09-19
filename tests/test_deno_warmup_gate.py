"""
Boundary tests for the startup Deno warm-up gate.

A download that begins before the startup DENO_DIR warm-up finishes races the
bgutil plugin's hard 15s script probe. The probe loses on a cold cache and raises
subprocess.TimeoutExpired straight out of ydl.download(), failing the item -- the
observed failure on 2026-08-08/09/10/11. The gate makes the download worker wait
for the warm-up instead of hoping it won the race.

Coverage map
============
src.pot_provider.start_deno_warmup
    - first call                            -> starts a thread, runs warm_deno_cache
    - second call while first pending       -> returns None, does not run twice
    - second call after first finished      -> still returns None (once per process)
    - warm_deno_cache raises                -> event is still set (no deadlock),
                                               and the traceback is logged
    - server_home/scripts_dir               -> forwarded to warm_deno_cache

src.pot_provider.wait_for_deno_warm
    - no warm-up ever started               -> returns None immediately
    - warm-up finished                      -> returns its DenoWarmResult
    - warm-up still running, timeout elapses -> returns None, does not hang
    - warm-up completes during the wait     -> returns the result
    - failed warm-up (ok=False)             -> result is returned, not swallowed

src.pot_provider.deno_warmup_pending
    - never started                         -> False
    - started and running                   -> True
    - started and finished                  -> False

(src.pot_provider._script_cache_dir has its own dedicated coverage in
tests/test_pot_provider.py, including the intentional -- not a bug --
``XDG_CACHE_HOME == ""`` case, which mirrors the real bgutil plugin's
``os.getenv(...) is not None`` gate rather than the general XDG spec.)

Concurrency (true multi-thread races, not just sequential thread.join)
    - N threads calling start_deno_warmup() simultaneously -> exactly one warm run
    - N threads calling wait_for_deno_warm() while one is in flight -> all get the
      same result, none hang past the warm-up's own completion
"""

import threading
from unittest.mock import patch

import pytest

from src import pot_provider
from src.pot_provider import (
    DenoWarmResult,
    deno_warmup_pending,
    start_deno_warmup,
    wait_for_deno_warm,
)

# Threads in these tests are handed off promptly; keep waits short so a genuine
# deadlock fails the suite fast instead of hanging it.
_JOIN_TIMEOUT_S = 5.0


@pytest.fixture(autouse=True)
def _reset_warm_state():
    """Reset pot_provider's module-level warm-up state around every test."""
    pot_provider._warm = pot_provider._WarmupState()
    yield
    pot_provider._warm = pot_provider._WarmupState()


def _ok_result(elapsed: float = 1.5) -> DenoWarmResult:
    return DenoWarmResult(True, elapsed, "warm")


class TestStartDenoWarmup:
    """Starting the background warm-up."""

    def test_runs_warm_deno_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The thread actually invokes warm_deno_cache."""
        calls: list[object] = []

        def _fake(**kwargs: object) -> DenoWarmResult:
            calls.append(kwargs)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _fake)
        thread = start_deno_warmup()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert len(calls) == 1

    def test_forwards_paths(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """server_home and scripts_dir reach warm_deno_cache unchanged."""
        seen: dict[str, object] = {}

        def _fake(**kwargs: object) -> DenoWarmResult:
            seen.update(kwargs)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _fake)
        home, scripts = tmp_path / "server", tmp_path / "scripts"
        thread = start_deno_warmup(server_home=home, scripts_dir=scripts)
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert seen == {"server_home": home, "scripts_dir": scripts}

    def test_second_call_while_pending_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A concurrent second start must not launch a second warm-up."""
        release = threading.Event()
        calls: list[int] = []

        def _fake(**_kwargs: object) -> DenoWarmResult:
            calls.append(1)
            release.wait(_JOIN_TIMEOUT_S)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _fake)
        first = start_deno_warmup()
        assert start_deno_warmup() is None
        release.set()
        assert first is not None
        first.join(_JOIN_TIMEOUT_S)
        assert len(calls) == 1

    def test_second_call_after_completion_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warming is once per process, not once per idle period."""
        monkeypatch.setattr(pot_provider, "warm_deno_cache", lambda **_k: _ok_result())
        thread = start_deno_warmup()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert start_deno_warmup() is None

    def test_exception_still_releases_waiters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A warm-up that raises must not leave every download blocked."""

        def _boom(**_kwargs: object) -> DenoWarmResult:
            raise RuntimeError("deno exploded")

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _boom)
        with patch.object(pot_provider.logger, "exception") as logged:
            thread = start_deno_warmup()
            assert thread is not None
            thread.join(_JOIN_TIMEOUT_S)
        assert not deno_warmup_pending()
        assert wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S) is None
        # A bare thread's stderr is discarded under pythonw; the traceback has to
        # be captured here or the failure is invisible.
        assert logged.call_count == 1


class TestWaitForDenoWarm:
    """Blocking until the warm-up is done."""

    def test_returns_none_when_never_started(self) -> None:
        """Callers that never started a warm-up must not block at all."""
        assert wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S) is None

    def test_returns_result_after_completion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The completed warm-up's result is handed back."""
        result = _ok_result(elapsed=2.25)
        monkeypatch.setattr(pot_provider, "warm_deno_cache", lambda **_k: result)
        thread = start_deno_warmup()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S) is result

    def test_returns_failed_result_rather_than_hiding_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed warm-up is reported, not silently converted to None."""
        failed = DenoWarmResult(False, 300.0, "timed out after 300s")
        monkeypatch.setattr(pot_provider, "warm_deno_cache", lambda **_k: failed)
        thread = start_deno_warmup()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S) is failed

    def test_gives_up_after_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stuck warm-up must not block downloads forever."""
        release = threading.Event()

        def _slow(**_kwargs: object) -> DenoWarmResult:
            release.wait(_JOIN_TIMEOUT_S)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _slow)
        thread = start_deno_warmup()
        assert wait_for_deno_warm(timeout=0.05) is None
        release.set()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)

    def test_waits_for_a_warm_up_in_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point: a wait that starts mid-warm-up returns the result."""
        release = threading.Event()
        result = _ok_result(elapsed=26.3)

        def _slow(**_kwargs: object) -> DenoWarmResult:
            release.wait(_JOIN_TIMEOUT_S)
            return result

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _slow)
        thread = start_deno_warmup()
        threading.Timer(0.05, release.set).start()
        assert wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S) is result
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)


class TestDenoWarmupPending:
    """The cheap, non-blocking 'would a waiter block?' probe."""

    def test_false_when_never_started(self) -> None:
        """Nothing started, nothing pending."""
        assert deno_warmup_pending() is False

    def test_true_while_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pending while the warm-up thread is still in flight."""
        release = threading.Event()

        def _slow(**_kwargs: object) -> DenoWarmResult:
            release.wait(_JOIN_TIMEOUT_S)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _slow)
        thread = start_deno_warmup()
        assert deno_warmup_pending() is True
        release.set()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)

    def test_false_after_completion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not pending once the warm-up has finished."""
        monkeypatch.setattr(pot_provider, "warm_deno_cache", lambda **_k: _ok_result())
        thread = start_deno_warmup()
        assert thread is not None
        thread.join(_JOIN_TIMEOUT_S)
        assert deno_warmup_pending() is False


class TestWarmupConcurrencyStress:
    """
    True concurrent races (multiple live threads), not sequential thread.join calls.

    The sequential tests above call start_deno_warmup()/wait_for_deno_warm() one
    after another from the main thread, which never actually contends the lock or
    the Event from two threads at once. These stress the real production shape:
    an app-startup thread and one or more download-worker threads touching
    ``_warm`` at (as close to) the same instant as the GIL allows.
    """

    def test_many_threads_starting_at_once_run_warmup_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N threads hammering start_deno_warmup() concurrently must not double-run it."""
        n_threads = 16
        barrier = threading.Barrier(n_threads, timeout=_JOIN_TIMEOUT_S)
        calls: list[int] = []
        release = threading.Event()

        def _fake(**_kwargs: object) -> DenoWarmResult:
            calls.append(1)
            release.wait(_JOIN_TIMEOUT_S)
            return _ok_result()

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _fake)

        results: list[threading.Thread | None] = [None] * n_threads

        def _worker(slot: int) -> None:
            barrier.wait()
            results[slot] = start_deno_warmup()

        workers = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(_JOIN_TIMEOUT_S)

        winners = [t for t in results if t is not None]
        assert len(winners) == 1
        release.set()
        winners[0].join(_JOIN_TIMEOUT_S)
        assert len(calls) == 1

    def test_many_waiters_all_receive_the_same_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A warm-up finishing mid-flight must release every concurrent waiter."""
        release = threading.Event()
        result = _ok_result(elapsed=3.3)

        def _slow(**_kwargs: object) -> DenoWarmResult:
            release.wait(_JOIN_TIMEOUT_S)
            return result

        monkeypatch.setattr(pot_provider, "warm_deno_cache", _slow)
        thread = start_deno_warmup()
        assert thread is not None

        n_waiters = 8
        outcomes: list[DenoWarmResult | None] = [None] * n_waiters

        def _wait(slot: int) -> None:
            outcomes[slot] = wait_for_deno_warm(timeout=_JOIN_TIMEOUT_S)

        waiters = [threading.Thread(target=_wait, args=(i,)) for i in range(n_waiters)]
        for w in waiters:
            w.start()
        # Let every waiter actually reach Event.wait() before releasing, so this
        # exercises the "still running" path rather than the "already done" one.
        release.set()
        for w in waiters:
            w.join(_JOIN_TIMEOUT_S)
        thread.join(_JOIN_TIMEOUT_S)

        assert outcomes == [result] * n_waiters

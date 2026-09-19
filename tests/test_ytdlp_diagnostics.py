"""
Boundary tests for the yt-dlp diagnostic capture and absolute path resolution.

Two defects motivated these. (1) yt-dlp talks to the app only through the
``logger`` option, so its debug stream never reached disk and a media-stage
HTTP 403 could not be diagnosed -- error_log.txt held the exception string and
nothing about which player client served the format or whether its URL carried a
``pot=`` token. (2) The Deno path and cookie jar were handed to yt-dlp as
relative paths, so they resolved only while the process CWD was the repo root.

Coverage map
============
src.logging_utils.get_ytdlp_debug_logger
    - flag off                              -> None, no file created
    - flag on                               -> logger writing to the configured path
    - called twice                          -> same logger, handler attached once
    - propagate is False                    -> capture cannot pollute error_log.txt
    - log dir missing                       -> created
    - handler cannot be opened              -> None, no exception escapes
    - concurrent first calls (race)         -> exactly one handler attached

src.ydl_options.build_base_ydl_opts
    - VID_DL_YTDLP_VERBOSE off              -> no "verbose" key
    - VID_DL_YTDLP_VERBOSE on               -> "verbose" is True

src.ydl_options.resolve_cookiefile
    - relative configured path              -> absolute
    - setting overrides the config default  -> absolute form of the override
    - non-string setting (bool/None)        -> falls back to the config default
    - "~" prefix                            -> expanded
    - path need not exist                   -> still resolved
    - UNC path                              -> resolved without raising
    - trailing separator                    -> normalized away
    - symlink                               -> resolved through to its real target
    - directory instead of a file           -> returned unchanged (no file-type check)

src.ydl_options.JS_RUNTIMES_CONFIG
    - deno key always present               -> yt-dlp still enables the runtime
    - path, when present                    -> absolute and ends in deno.exe
"""

import importlib
import logging
import threading
from pathlib import Path
from typing import Self
from unittest.mock import MagicMock, patch

import pytest

from src import config, logging_utils
from src.ydl_options import JS_RUNTIMES_CONFIG, resolve_cookiefile

# Threads in the race test are handed off promptly; a genuine deadlock/regression
# should fail fast rather than hang the suite.
_JOIN_TIMEOUT_S = 5.0


@pytest.fixture(autouse=True)
def _clean_debug_logger():
    """Detach handlers from the capture logger around every test."""

    def _reset() -> None:
        lg = logging.getLogger(logging_utils._YTDLP_DEBUG_LOGGER_NAME)
        for handler in list(lg.handlers):
            handler.close()
            lg.removeHandler(handler)

    _reset()
    yield
    _reset()


class TestGetYtdlpDebugLogger:
    """The opt-in capture logger."""

    def test_returns_none_when_disabled(self, tmp_path: Path) -> None:
        """Off by default: no logger, and nothing written to disk."""
        target = tmp_path / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", False),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
        ):
            assert logging_utils.get_ytdlp_debug_logger() is None
        assert not target.exists()

    def test_writes_to_configured_path(self, tmp_path: Path) -> None:
        """Enabled: messages land in the configured file."""
        target = tmp_path / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
        ):
            debug_logger = logging_utils.get_ytdlp_debug_logger()
            assert debug_logger is not None
            debug_logger.debug("[debug] Invoking http downloader pot=abc123")
            for handler in debug_logger.handlers:
                handler.flush()
        assert "pot=abc123" in target.read_text(encoding="utf-8")

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        """A fresh checkout has no log dir yet; make one rather than failing."""
        target = tmp_path / "nested" / "deeper" / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
        ):
            assert logging_utils.get_ytdlp_debug_logger() is not None
        assert target.parent.is_dir()

    def test_repeated_calls_attach_one_handler(self, tmp_path: Path) -> None:
        """Every QLogger construction calls this; handlers must not accumulate."""
        target = tmp_path / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
        ):
            first = logging_utils.get_ytdlp_debug_logger()
            second = logging_utils.get_ytdlp_debug_logger()
        assert first is second
        assert first is not None
        assert len(first.handlers) == 1

    def test_does_not_propagate_to_root(self, tmp_path: Path) -> None:
        """The capture must never leak into error_log.txt."""
        target = tmp_path / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
        ):
            debug_logger = logging_utils.get_ytdlp_debug_logger()
        assert debug_logger is not None
        assert debug_logger.propagate is False

    def test_unopenable_log_returns_none(self, tmp_path: Path) -> None:
        """Diagnostics must never take the app down with them."""
        target = tmp_path / "ytdlp_debug.log"
        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
            patch.object(
                logging_utils,
                "RotatingFileHandler",
                side_effect=OSError("permission denied"),
            ),
            patch.object(logging_utils, "log_exception") as logged,
        ):
            assert logging_utils.get_ytdlp_debug_logger() is None
        assert logged.call_count == 1

    def test_concurrent_first_calls_attach_one_handler(self, tmp_path: Path) -> None:
        """
        Two QLogger constructions racing the cold path must not double-attach.

        Both threads pass the unlocked ``if debug_logger.handlers:`` fast-path
        check as empty (neither has added one yet) before either reaches the
        lock -- widened here by making lock acquisition itself wait on a
        barrier, so both threads are guaranteed to arrive at
        ``with _debug_logger_lock:`` at the same instant, exactly reproducing
        the window between "check" and "act". The double-checked re-read
        inside the lock is what must resolve this: one thread creates the
        handler, the other re-checks, finds it already attached, and returns
        without creating a second one. Without that inner re-check, both
        threads would fall through to ``addHandler``, doubling every
        subsequent yt-dlp log line and leaving two open file handles on the
        same RotatingFileHandler target.
        """
        target = tmp_path / "ytdlp_debug.log"
        barrier = threading.Barrier(2, timeout=_JOIN_TIMEOUT_S)
        real_lock = logging_utils._debug_logger_lock

        class RacingLock:
            """Forces both threads to reach the lock before either acquires it."""

            def __enter__(self) -> Self:
                barrier.wait()
                real_lock.acquire()
                return self

            def __exit__(self, *exc_info: object) -> bool:
                real_lock.release()
                return False

        results: list[logging.Logger | None] = [None, None]

        def _call(slot: int) -> None:
            results[slot] = logging_utils.get_ytdlp_debug_logger()

        with (
            patch.object(logging_utils, "YTDLP_VERBOSE", True),
            patch.object(logging_utils, "YTDLP_DEBUG_LOG_PATH", target),
            patch.object(logging_utils, "_debug_logger_lock", RacingLock()),
        ):
            t1 = threading.Thread(target=_call, args=(0,))
            t2 = threading.Thread(target=_call, args=(1,))
            t1.start()
            t2.start()
            t1.join(_JOIN_TIMEOUT_S)
            t2.join(_JOIN_TIMEOUT_S)

        assert results[0] is not None
        assert results[0] is results[1]
        assert len(results[0].handlers) == 1


class TestVerboseOption:
    """The yt-dlp ``verbose`` option follows the flag."""

    def _build(self, verbose: bool) -> dict:
        from src import ydl_options

        with patch.object(ydl_options, "YTDLP_VERBOSE", verbose):
            return ydl_options.build_base_ydl_opts(MagicMock(), MagicMock())

    def test_absent_when_flag_off(self) -> None:
        """Default builds carry no verbose key at all."""
        assert "verbose" not in self._build(False)

    def test_true_when_flag_on(self) -> None:
        """Flag on makes yt-dlp emit its debug stream through the logger."""
        assert self._build(True)["verbose"] is True


class TestResolveCookiefile:
    """Cookie jar path resolution."""

    def test_returns_absolute_path(self) -> None:
        """The configured default is relative; callers must get an absolute path."""
        assert Path(resolve_cookiefile()).is_absolute()

    def test_setting_override_is_honoured(self, tmp_path: Path) -> None:
        """An explicit setting wins over the config default."""
        override = tmp_path / "mine" / "cookies.txt"
        with patch("src.ydl_options.get_setting", return_value=str(override)):
            assert resolve_cookiefile() == str(override.resolve())

    def test_expands_user_home(self) -> None:
        """A '~' path is expanded rather than taken literally."""
        with patch("src.ydl_options.get_setting", return_value="~/cookies.txt"):
            resolved = resolve_cookiefile()
        assert "~" not in resolved
        assert resolved == str((Path.home() / "cookies.txt").resolve())

    @pytest.mark.parametrize("value", [True, False, None, "", 0, Path("x")])
    def test_non_string_setting_falls_back_to_default(self, value: object) -> None:
        """A settings store that returns a non-path must not break option building."""
        with patch("src.ydl_options.get_setting", return_value=value):
            resolved = resolve_cookiefile()
        assert resolved == str(Path(config.COOKIES_FILE).expanduser().resolve())

    def test_missing_file_still_resolves(self, tmp_path: Path) -> None:
        """The jar is created on first write; a missing path is not an error."""
        missing = tmp_path / "not-there-yet.txt"
        with patch("src.ydl_options.get_setting", return_value=str(missing)):
            assert resolve_cookiefile() == str(missing.resolve())

    def test_unc_path_resolves_without_raising(self) -> None:
        """A UNC path must not trip Path.resolve() up on Windows."""
        unc = r"\\fileserver\share\cookies.txt"
        with patch("src.ydl_options.get_setting", return_value=unc):
            resolved = resolve_cookiefile()
        assert resolved.startswith("\\\\fileserver\\share")
        assert resolved.endswith("cookies.txt")

    def test_trailing_separator_is_normalized(self, tmp_path: Path) -> None:
        """A trailing separator on the configured dir must not survive resolution."""
        configured = str(tmp_path) + "\\"
        with patch("src.ydl_options.get_setting", return_value=configured):
            resolved = resolve_cookiefile()
        assert not resolved.endswith("\\")
        assert resolved == str(tmp_path.resolve())

    def test_symlink_resolves_to_real_target(self, tmp_path: Path) -> None:
        """A symlinked cookie jar resolves through to its real target, not the link."""
        real = tmp_path / "real" / "cookies.txt"
        real.parent.mkdir()
        real.write_text("", encoding="utf-8")
        link = tmp_path / "cookies_link.txt"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("symlinks require elevated privileges on this host")
        with patch("src.ydl_options.get_setting", return_value=str(link)):
            resolved = resolve_cookiefile()
        assert resolved == str(real.resolve())

    def test_directory_instead_of_file_is_returned_unchanged(self, tmp_path: Path) -> None:
        """
        No file-type validation: a directory resolves like any other path.

        Documents current behavior rather than asserting a guard that does not
        exist -- resolve_cookiefile's docstring only promises the path "need
        not exist yet", not that it names a file. Handing this straight to
        yt-dlp's ``cookiefile`` option would fail at open() time, one layer up.
        """
        directory = tmp_path / "not_a_file"
        directory.mkdir()
        with patch("src.ydl_options.get_setting", return_value=str(directory)):
            resolved = resolve_cookiefile()
        assert resolved == str(directory.resolve())


class TestJsRuntimesConfig:
    """The runtime handed to yt-dlp must be an absolute executable path."""

    def test_deno_key_present(self) -> None:
        """Deno stays enabled whether or not the bundled binary was found."""
        assert "deno" in JS_RUNTIMES_CONFIG

    def test_path_is_absolute_executable_when_present(self) -> None:
        """yt-dlp documents ``path`` as the executable, not its directory."""
        path = JS_RUNTIMES_CONFIG["deno"].get("path")
        if path is None:
            pytest.skip("bundled deno.exe not installed in this environment")
        assert Path(path).is_absolute()
        assert Path(path).name == "deno.exe"

    def test_path_omitted_when_binary_missing(self) -> None:
        """Without the binary, omit ``path`` so yt-dlp falls back to PATH search."""
        with patch.object(config, "DENO_EXECUTABLE", None):
            from src import ydl_options

            reloaded = importlib.reload(ydl_options)
            try:
                assert reloaded.JS_RUNTIMES_CONFIG["deno"] == {}
            finally:
                importlib.reload(reloaded)

"""
A PO-token script probe that overruns its budget must disable the provider, not fail the item.

The bgutil plugin's ``_check_script_impl`` runs ``generate_once.ts --version`` under a
hard 15s timeout and lets ``subprocess.TimeoutExpired`` escape ``is_available()``.
yt-dlp's PO-token director calls ``is_available()`` without a try, so one slow probe
aborted whole downloads and podcast checks -- even when the first player client
(tv_samsung) needs no PO token at all. ``install_probe_timeout_guard`` restores the
provider contract: a timed-out probe means "unavailable", and extraction carries on.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from yt_dlp.utils import Popen

from src import pot_provider, ydl_options

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_PROBE = "_check_script_impl"


def _registered_probe_providers() -> Iterator[type]:
    """Yield every PO-token provider class yt-dlp registered that runs a script probe."""
    from yt_dlp.extractor.youtube.pot._registry import (
        _pot_providers,  # pyright: ignore[reportPrivateUsage]
    )

    for provider in _pot_providers.value.values():
        if any(_PROBE in vars(k) for k in provider.__mro__):
            yield provider


def _deno_provider() -> Any:
    """Build a bare script-deno provider instance with just what the probe reads."""
    pot_provider.install_probe_timeout_guard()
    cls = next(p for p in _registered_probe_providers() if p.__name__ == "BgUtilScriptDenoPTP")
    inst = object.__new__(cls)
    # cached_property values live in the instance dict; seed them so no Deno lookup runs.
    inst.__dict__.update(
        _jsrt_path="deno",
        _server_home="server_home",
        _script_cache_dir="cache_dir",
    )
    return inst


@pytest.fixture
def script(tmp_path: Path) -> str:
    path = tmp_path / "generate_once.ts"
    path.write_text("", encoding="utf-8")
    return str(path)


def test_probe_timeout_reports_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    def _timeout(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["deno"], timeout=15.0)

    monkeypatch.setattr(Popen, "run", _timeout)
    assert getattr(_deno_provider(), _PROBE)(script) is False


def test_successful_probe_passes_through(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    monkeypatch.setattr(Popen, "run", lambda *_a, **_k: ("2.0.2\n", None, 0))
    inst = _deno_provider()
    monkeypatch.setattr(inst, "_check_version", lambda *_a, **_k: None, raising=False)
    assert getattr(inst, _PROBE)(script) is True


def test_guard_covers_every_registered_script_provider() -> None:
    """Fails loudly if a plugin upgrade renames the probe and the guard stops applying."""
    covered = pot_provider.install_probe_timeout_guard()
    providers = list(_registered_probe_providers())
    assert providers, "no registered PO-token provider defines _check_script_impl"
    assert covered == len(providers)
    for provider in providers:
        method = getattr(provider, _PROBE)
        assert getattr(method, pot_provider.PROBE_GUARD_ATTR, False), provider.__name__


def test_install_is_idempotent() -> None:
    pot_provider.install_probe_timeout_guard()
    pot_provider.install_probe_timeout_guard()
    for provider in _registered_probe_providers():
        inner = getattr(getattr(provider, _PROBE), "__wrapped__", None)
        assert inner is not None
        assert not getattr(inner, pot_provider.PROBE_GUARD_ATTR, False), "guard wrapped twice"


def test_shared_extraction_opts_install_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[None] = []
    monkeypatch.setattr(ydl_options, "install_probe_timeout_guard", lambda: calls.append(None) or 0)
    ydl_options.build_shared_extraction_opts()
    assert calls == [None]


def test_probe_non_timeout_error_still_propagates(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """The guard maps only TimeoutExpired; any other probe failure must stay loud."""

    def _missing(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("deno")

    monkeypatch.setattr(Popen, "run", _missing)
    with pytest.raises(FileNotFoundError):
        getattr(_deno_provider(), _PROBE)(script)


def test_probe_nonzero_exit_stays_false(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    monkeypatch.setattr(Popen, "run", lambda *_a, **_k: ("boom\n", None, 1))
    inst = _deno_provider()
    inst.logger = MagicMock()
    assert getattr(inst, _PROBE)(script) is False


def test_probe_timeout_logs_warning_with_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, script: str
) -> None:
    def _timeout(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["deno"], timeout=15.0)

    monkeypatch.setattr(Popen, "run", _timeout)
    with caplog.at_level("WARNING", logger=pot_provider.logger.name):
        getattr(_deno_provider(), _PROBE)(script)
    assert "overran its 15s budget" in caplog.text


def test_provider_built_after_install_binds_guarded_probe(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """Providers cache the probe at __init__; an instance built after install must be guarded."""
    pot_provider.install_probe_timeout_guard()
    cls = next(p for p in _registered_probe_providers() if p.__name__ == "BgUtilScriptDenoPTP")
    inst = cls(MagicMock(), MagicMock(), {})
    inst.__dict__.update(_jsrt_path="deno")

    def _timeout(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["deno"], timeout=15.0)

    monkeypatch.setattr(Popen, "run", _timeout)
    assert inst._check_script(script) is False  # pyright: ignore[reportPrivateUsage]

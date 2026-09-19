"""
Tests for the setup_pot_provider script.

Coverage map
============
parse_args
    - --skip-warm omitted                  -> args.skip_warm is False
    - --skip-warm passed                   -> args.skip_warm is True

resolve_server_dir
    - always                               -> returns the vendored server dir constant

resolve_deno
    - venv deno.exe present                -> returns str(venv deno.exe), PATH not consulted
    - venv deno.exe absent, PATH has deno   -> returns shutil.which result
    - venv deno.exe absent, PATH lacks deno -> returns None

build_deno_install_cmd
    - always                               -> [deno, "install", "--allow-scripts=npm:canvas",
                                                 "--frozen"]

main — exit-code contract (highest-risk area per handoff)
    - server dir missing                          -> returns 2, message on stderr, nothing else runs
    - node_modules present, no --force            -> skips install, still funnels into warm-up
    - node_modules present, --force                -> re-runs deno install even though present
    - node_modules missing, deno unresolvable      -> returns 2, install never attempted
    - node_modules missing, deno install fails     -> returns install's own returncode, warm-up
                                                       is NOT invoked (failure short-circuits)
    - node_modules missing, deno install succeeds  -> proceeds into the same warm-up funnel as
                                                       the "already installed" path
    - --skip-warm                                  -> returns 0, warm_deno_cache never called
    - warm-up succeeds                             -> returns 0, success message on stdout
    - warm-up fails                                -> returns 0 anyway (non-fatal), warning +
                                                       detail on stderr
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from src.pot_provider import DenoWarmResult

if TYPE_CHECKING:
    import types

    import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup_pot_provider.py"


def _load_setup() -> types.ModuleType:
    """Import the standalone script as a module (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("setup_pot_provider", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# parse_args — flag parsing
# ---------------------------------------------------------------------------


def test_setup_script_skip_warm_flag_defaults_false() -> None:
    setup_mod = _load_setup()
    args = setup_mod.parse_args([])
    assert args.skip_warm is False


def test_setup_script_skip_warm_flag_parses() -> None:
    setup_mod = _load_setup()
    args = setup_mod.parse_args(["--skip-warm"])
    assert args.skip_warm is True


# ---------------------------------------------------------------------------
# resolve_server_dir
# ---------------------------------------------------------------------------


def test_resolve_server_dir_matches_vendored_constant() -> None:
    setup_mod = _load_setup()
    result = setup_mod.resolve_server_dir()
    assert result == setup_mod._VENDOR_SERVER_DIR
    assert result.parts[-3:] == ("vendor", "bgutil-pot-provider", "server")


# ---------------------------------------------------------------------------
# resolve_deno — venv-first, PATH-fallback resolution
# ---------------------------------------------------------------------------


def test_resolve_deno_prefers_venv_deno_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    venv_deno = tmp_path / "deno.exe"
    venv_deno.touch()
    monkeypatch.setattr(setup_mod, "_VENV_DENO", venv_deno)
    mock_which = MagicMock(return_value="/should/not/be/used")
    monkeypatch.setattr(setup_mod.shutil, "which", mock_which)

    result = setup_mod.resolve_deno()

    assert result == str(venv_deno)
    mock_which.assert_not_called()


def test_resolve_deno_falls_back_to_path_when_venv_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    monkeypatch.setattr(setup_mod, "_VENV_DENO", tmp_path / "missing" / "deno.exe")
    monkeypatch.setattr(setup_mod.shutil, "which", MagicMock(return_value="/usr/bin/deno"))

    assert setup_mod.resolve_deno() == "/usr/bin/deno"


def test_resolve_deno_returns_none_when_neither_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    monkeypatch.setattr(setup_mod, "_VENV_DENO", tmp_path / "missing" / "deno.exe")
    monkeypatch.setattr(setup_mod.shutil, "which", MagicMock(return_value=None))

    assert setup_mod.resolve_deno() is None


# ---------------------------------------------------------------------------
# build_deno_install_cmd
# ---------------------------------------------------------------------------


def test_build_deno_install_cmd_argv_shape() -> None:
    setup_mod = _load_setup()
    cmd = setup_mod.build_deno_install_cmd("deno")
    assert cmd == ["deno", "install", "--allow-scripts=npm:canvas", "--frozen"]


# ---------------------------------------------------------------------------
# main — exit-code contract
# ---------------------------------------------------------------------------


def test_main_returns_2_when_server_dir_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: missing)
    mock_run = MagicMock()
    monkeypatch.setattr(setup_mod.subprocess, "run", mock_run)
    mock_warm = MagicMock()
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main([])

    assert rc == 2
    assert "not found" in capsys.readouterr().err
    mock_run.assert_not_called()
    mock_warm.assert_not_called()


def test_main_already_installed_skips_install_but_still_warms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    (server_dir / "node_modules").mkdir(parents=True)
    deno = tmp_path / "denohome" / "deno.exe"
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: str(deno))
    mock_run = MagicMock()
    monkeypatch.setattr(setup_mod.subprocess, "run", mock_run)
    mock_warm = MagicMock(return_value=DenoWarmResult(ok=True, elapsed_s=1.2, detail="1.3.1"))
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main([])

    assert rc == 0
    mock_run.assert_not_called()  # no reinstall when node_modules exists and --force absent
    mock_warm.assert_called_once_with(server_home=server_dir, scripts_dir=deno.parent)
    assert "already installed" in capsys.readouterr().out


def test_main_force_reinstalls_even_when_node_modules_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    (server_dir / "node_modules").mkdir(parents=True)
    deno = tmp_path / "denohome" / "deno.exe"
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: str(deno))
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(setup_mod.subprocess, "run", mock_run)
    mock_warm = MagicMock(return_value=DenoWarmResult(ok=True, elapsed_s=0.5, detail=""))
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main(["--force"])

    assert rc == 0
    mock_run.assert_called_once()
    mock_warm.assert_called_once_with(server_home=server_dir, scripts_dir=deno.parent)


def test_main_returns_2_when_deno_unresolvable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    server_dir.mkdir()  # node_modules absent -> install path taken
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: None)
    mock_run = MagicMock()
    monkeypatch.setattr(setup_mod.subprocess, "run", mock_run)
    mock_warm = MagicMock()
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main([])

    assert rc == 2
    assert "could not locate Deno" in capsys.readouterr().err
    mock_run.assert_not_called()
    mock_warm.assert_not_called()


def test_main_returns_install_returncode_and_skips_warm_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    server_dir.mkdir()  # node_modules absent -> install path taken
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: "deno")
    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        MagicMock(return_value=MagicMock(returncode=7)),
    )
    mock_warm = MagicMock()
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main([])

    assert rc == 7
    mock_warm.assert_not_called()  # install failure must short-circuit before warm-up


def test_main_fresh_install_success_funnels_into_same_warm_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    server_dir.mkdir()  # node_modules absent -> install path taken
    deno = tmp_path / "denohome" / "deno.exe"
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: str(deno))
    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    mock_warm = MagicMock(return_value=DenoWarmResult(ok=True, elapsed_s=2.0, detail=""))
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main([])

    assert rc == 0
    mock_warm.assert_called_once_with(server_home=server_dir, scripts_dir=deno.parent)


def test_main_warms_with_the_deno_that_was_actually_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: the warm-up must follow resolve_deno(), not VENV_SCRIPTS_DIR.

    resolve_deno() falls back to a Deno on PATH when .venv/Scripts/deno.exe is
    absent. Warming against the VENV_SCRIPTS_DIR default there would find no
    deno.exe and silently no-op -- an install that "succeeds" while the cache stays
    cold, which is exactly the 403 this feature exists to prevent.
    """
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    server_dir.mkdir()  # node_modules absent -> install path taken
    path_deno = tmp_path / "elsewhere" / "on-path" / "deno.exe"  # NOT under .venv
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(setup_mod, "resolve_deno", lambda: str(path_deno))
    monkeypatch.setattr(
        setup_mod.subprocess,
        "run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    mock_warm = MagicMock(return_value=DenoWarmResult(ok=True, elapsed_s=2.0, detail=""))
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    assert setup_mod.main([]) == 0
    assert mock_warm.call_args.kwargs["scripts_dir"] == path_deno.parent


def test_main_skip_warm_returns_0_without_calling_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    (server_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    mock_warm = MagicMock()
    monkeypatch.setattr(setup_mod, "warm_deno_cache", mock_warm)

    rc = setup_mod.main(["--skip-warm"])

    assert rc == 0
    mock_warm.assert_not_called()
    assert "skipping" in capsys.readouterr().out.lower()


def test_main_warm_success_prints_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    (server_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(
        setup_mod,
        "warm_deno_cache",
        MagicMock(return_value=DenoWarmResult(ok=True, elapsed_s=1.7, detail="1.3.1")),
    )

    rc = setup_mod.main([])

    assert rc == 0
    assert "warm" in capsys.readouterr().out.lower()


def test_main_warm_failure_is_nonfatal_but_warns_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_mod = _load_setup()
    server_dir = tmp_path / "server"
    (server_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(setup_mod, "resolve_server_dir", lambda: server_dir)
    monkeypatch.setattr(
        setup_mod,
        "warm_deno_cache",
        MagicMock(
            return_value=DenoWarmResult(ok=False, elapsed_s=0.0, detail="timed out after 300s"),
        ),
    )

    rc = setup_mod.main([])

    assert rc == 0  # non-fatal: node_modules is installed regardless of warm outcome
    err = capsys.readouterr().err
    assert "timed out after 300s" in err
    assert "403" in err

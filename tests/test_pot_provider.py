"""
Boundary tests for the bgutil PO-token provider healthcheck.

Coverage map
============
src.pot_provider.check_pot_provider
    - all components present               -> ok is True, summary() == ""
    - missing generate_once.ts             -> not ok, summary names the script
    - missing node_modules                 -> not ok, summary names node_modules
    - provider plugin pruned               -> not ok, summary names the plugin
    - deno runtime missing/old             -> not ok, summary names Deno
    - everything missing                   -> summary lists every piece

src.pot_provider._deno_ok
    - deno.exe present, recent version     -> True
    - deno.exe present, pre-2.0 version    -> False
    - deno.exe absent                      -> False without invoking subprocess

src.pot_provider.PotProviderStatus
    - frozen dataclass                     -> field assignment raises FrozenInstanceError
    - frozen dataclass                     -> attribute delete raises FrozenInstanceError
    - ok/summary() invariant holds across all 16 boolean combinations
    - summary() component order is stable when everything is missing

src.pot_provider._plugin_importable (exercised directly, not mocked away)
    - find_spec returns a spec             -> True
    - find_spec returns None               -> False
    - find_spec raises ImportError/ValueError -> False

src.pot_provider._deno_ok (additional boundaries)
    - exact min version (2.4.3)            -> True
    - just below min patch (2.4.2)         -> False
    - just below min minor (2.3.9)         -> False
    - just below min major (1.99.99)       -> False
    - multiline stdout, version on line 1  -> True
    - extra internal whitespace            -> True
    - empty / None stdout                  -> False
    - unparseable / incomplete version     -> False
    - prerelease suffix (2.4.3-rc.1)       -> True (documents permissive parsing)
    - non-zero returncode                  -> False, even with valid-looking stdout
    - subprocess timeout / OSError         -> False (swallowed)
    - deno.exe path is a directory         -> False, subprocess never invoked
    - numeric (not lexical) version compare -> 2.10.0 >= 2.9.0 is True

src.pot_provider.check_pot_provider (path resolution)
    - str server_home/scripts_dir          -> resolved like Path
    - relative server_home                 -> resolved against cwd
    - omitted args                         -> falls back to config module defaults
    - script path exists but is a directory -> treated as not found
    - node_modules path exists but is a file -> treated as not found

src.pot_provider._escpath
    - single path                          -> str(path)
    - multiple paths                       -> comma-joined
    - literal commas in path names         -> doubled

src.pot_provider._script_cache_dir
    - XDG_CACHE_HOME set                   -> <xdg>/bgutil-ytdlp-pot-provider
    - XDG_CACHE_HOME unset, HOME set       -> <home>/.cache/bgutil-ytdlp-pot-provider
    - both unset, USERPROFILE set          -> <userprofile>/.cache/bgutil-ytdlp-pot-provider
    - all three unset                      -> server_home unchanged

src.pot_provider.build_warm_cmd
    - argv order and flags                 -> matches BgUtilScriptDenoPTP._jsrt_args
    - paths use _escpath                   -> literal commas doubled in flags

src.pot_provider.build_warm_env
    - os.environ copied                    -> modifications don't leak
    - Deno flags set                       -> DENO_NO_PROMPT, DENO_NO_UPDATE_CHECK, FORCE_COLOR

src.pot_provider.warm_deno_cache
    - all prerequisites present, rc=0     -> ok is True, detail stripped stdout
    - deno.exe missing                     -> ok is False, subprocess never invoked
    - generate_once.ts missing             -> ok is False, subprocess never invoked
    - node_modules missing                 -> ok is False, subprocess never invoked
    - subprocess.TimeoutExpired            -> ok is False, no exception escapes
    - subprocess.OSError                   -> ok is False, no exception escapes
    - subprocess rc != 0                   -> ok is False, detail is stripped stderr
    - creationflags=_NO_WINDOW on Windows  -> passed to subprocess.run

src.pot_provider.DenoWarmResult
    - frozen dataclass                     -> field assignment raises FrozenInstanceError

src.pot_provider._deno_ok (creationflags)
    - creationflags=_NO_WINDOW passed      -> to subprocess.run
"""

import itertools
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import pot_provider

# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _valid_tree(root: Path) -> Path:
    """Build a fully wired server_home under ``root`` and return it."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "generate_once.ts").touch()
    (root / "node_modules").mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# check_pot_provider — filesystem components (deno/plugin patched out)
# ---------------------------------------------------------------------------


def test_status_ok_when_all_present(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=home, scripts_dir=tmp_path)
    assert status.ok is True
    assert status.summary() == ""


def test_missing_script_not_ok(tmp_path: Path) -> None:
    # node_modules present, but no src/generate_once.ts.
    (tmp_path / "node_modules").mkdir()
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=tmp_path, scripts_dir=tmp_path)
    assert status.ok is False
    assert "generate_once.ts" in status.summary()


def test_missing_node_modules_not_ok(tmp_path: Path) -> None:
    # script present, but no node_modules dir.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "generate_once.ts").touch()
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=tmp_path, scripts_dir=tmp_path)
    assert status.ok is False
    assert "node_modules" in status.summary()


def test_plugin_pruned_not_ok(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    with (
        patch("src.pot_provider._plugin_importable", return_value=False),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=home, scripts_dir=tmp_path)
    assert status.ok is False
    assert "provider plugin" in status.summary()


def test_deno_missing_not_ok(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=False),
    ):
        status = pot_provider.check_pot_provider(server_home=home, scripts_dir=tmp_path)
    assert status.ok is False
    assert "Deno" in status.summary()


def test_summary_lists_multiple_missing(tmp_path: Path) -> None:
    # Empty server_home + both probes False -> every component is missing.
    with (
        patch("src.pot_provider._plugin_importable", return_value=False),
        patch("src.pot_provider._deno_ok", return_value=False),
    ):
        status = pot_provider.check_pot_provider(server_home=tmp_path, scripts_dir=tmp_path)
    summary = status.summary()
    assert "provider plugin" in summary
    assert "Deno" in summary


# ---------------------------------------------------------------------------
# _deno_ok — version parsing / gating
# ---------------------------------------------------------------------------


def test_deno_ok_parses_recent_version(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="deno 2.5.4 (stable)"),
    ):
        assert pot_provider._deno_ok(tmp_path) is True


def test_deno_ok_rejects_old_version(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="deno 1.46.3"),
    ):
        assert pot_provider._deno_ok(tmp_path) is False


def test_deno_ok_false_when_exe_absent(tmp_path: Path) -> None:
    with patch("src.pot_provider.subprocess.run") as mock_run:
        assert pot_provider._deno_ok(tmp_path) is False
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# PotProviderStatus — dataclass invariants
# ---------------------------------------------------------------------------


def test_status_is_frozen_dataclass() -> None:
    status = pot_provider.PotProviderStatus(
        plugin_installed=True,
        deno_ok=True,
        script_found=True,
        node_modules_found=True,
    )
    with pytest.raises(FrozenInstanceError):
        status.plugin_installed = False


def test_status_frozen_dataclass_blocks_attribute_delete() -> None:
    status = pot_provider.PotProviderStatus(
        plugin_installed=True,
        deno_ok=True,
        script_found=True,
        node_modules_found=True,
    )
    with pytest.raises(FrozenInstanceError):
        del status.deno_ok


# ---------------------------------------------------------------------------
# PotProviderStatus — ok/summary() combinatorial invariants
# ---------------------------------------------------------------------------

_ALL_MISSING_LABELS = {
    "plugin_installed": "provider plugin (bgutil-ytdlp-pot-provider)",
    "deno_ok": "Deno runtime (>= 2.4.3)",
    "script_found": "generate_once.ts script",
    "node_modules_found": "script dependencies (node_modules)",
}


@pytest.mark.parametrize("flags", list(itertools.product([True, False], repeat=4)))
def test_status_ok_and_summary_invariants_across_all_combinations(
    flags: tuple[bool, bool, bool, bool],
) -> None:
    plugin, deno, script, node_modules = flags
    status = pot_provider.PotProviderStatus(
        plugin_installed=plugin,
        deno_ok=deno,
        script_found=script,
        node_modules_found=node_modules,
    )
    expected_ok = plugin and deno and script and node_modules
    assert status.ok is expected_ok
    summary = status.summary()
    assert (summary == "") is expected_ok
    field_names = ("plugin_installed", "deno_ok", "script_found", "node_modules_found")
    for field, present in zip(field_names, flags, strict=True):
        label = _ALL_MISSING_LABELS[field]
        if present:
            assert label not in summary
        else:
            assert label in summary


def test_summary_component_order_is_stable_when_all_missing() -> None:
    status = pot_provider.PotProviderStatus(
        plugin_installed=False,
        deno_ok=False,
        script_found=False,
        node_modules_found=False,
    )
    assert status.summary() == (
        "provider plugin (bgutil-ytdlp-pot-provider), "
        "Deno runtime (>= 2.4.3), "
        "generate_once.ts script, "
        "script dependencies (node_modules)"
    )


# ---------------------------------------------------------------------------
# _plugin_importable — exercised directly (not mocked away)
# ---------------------------------------------------------------------------


def test_plugin_importable_true_when_spec_found() -> None:
    with patch(
        "src.pot_provider.importlib.util.find_spec",
        return_value=MagicMock(),
    ):
        assert pot_provider._plugin_importable() is True


def test_plugin_importable_false_when_spec_none() -> None:
    with patch("src.pot_provider.importlib.util.find_spec", return_value=None):
        assert pot_provider._plugin_importable() is False


def test_plugin_importable_false_on_import_error() -> None:
    with patch(
        "src.pot_provider.importlib.util.find_spec",
        side_effect=ImportError,
    ):
        assert pot_provider._plugin_importable() is False


def test_plugin_importable_false_on_value_error() -> None:
    with patch(
        "src.pot_provider.importlib.util.find_spec",
        side_effect=ValueError,
    ):
        assert pot_provider._plugin_importable() is False


# ---------------------------------------------------------------------------
# _deno_ok — additional version-parsing / subprocess boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("deno 2.4.3 (release, x86_64-pc-windows-msvc)", True),
        ("deno 2.4.2 (release)", False),
        ("deno 2.3.9 (release)", False),
        ("deno 1.99.99 (release)", False),
        ("deno 2.5.4 (stable)\nv8 12.4.254.21\ntypescript 5.6.2\n", True),
        ("deno    2.5.4   (release)", True),
        ("", False),
        ("deno version unknown", False),
        ("deno 2.5", False),
        ("deno 2.4.3-rc.1 (canary)", True),
    ],
    ids=[
        "exact-min-boundary",
        "just-below-min-patch",
        "just-below-min-minor",
        "just-below-min-major",
        "multiline-first-line",
        "extra-whitespace",
        "empty-stdout",
        "unparseable",
        "incomplete-semver",
        "prerelease-suffix-accepted",
    ],
)
def test_deno_ok_version_string_boundaries(
    tmp_path: Path,
    stdout: str,
    *,
    expected: bool,
) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=0, stdout=stdout),
    ):
        assert pot_provider._deno_ok(tmp_path) is expected


def test_deno_ok_none_stdout_does_not_crash(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=0, stdout=None),
    ):
        assert pot_provider._deno_ok(tmp_path) is False


def test_deno_ok_nonzero_returncode_short_circuits(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=1, stdout="deno 2.5.4 (release)"),
    ):
        assert pot_provider._deno_ok(tmp_path) is False


def test_deno_ok_timeout_expired_swallowed(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="deno --version", timeout=5.0),
    ):
        assert pot_provider._deno_ok(tmp_path) is False


def test_deno_ok_oserror_swallowed(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch(
        "src.pot_provider.subprocess.run",
        side_effect=FileNotFoundError("deno.exe vanished"),
    ):
        assert pot_provider._deno_ok(tmp_path) is False


def test_deno_ok_exe_path_is_directory_not_file(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").mkdir()
    with patch("src.pot_provider.subprocess.run") as mock_run:
        assert pot_provider._deno_ok(tmp_path) is False
    mock_run.assert_not_called()


def test_deno_ok_numeric_not_lexical_comparison(tmp_path: Path) -> None:
    """
    Guard against a regression to string comparison.

    "2.10.0" < "2.9.0" lexically even though it is numerically newer; the
    implementation must compare version tuples as integers.
    """
    (tmp_path / "deno.exe").touch()
    with (
        patch("src.pot_provider._DENO_MIN_VERSION", (2, 9, 0)),
        patch(
            "src.pot_provider.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="deno 2.10.0 (release)"),
        ),
    ):
        assert pot_provider._deno_ok(tmp_path) is True


# ---------------------------------------------------------------------------
# check_pot_provider — path resolution boundaries
# ---------------------------------------------------------------------------


def test_check_pot_provider_accepts_str_paths(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path / "home")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(
            server_home=str(home),
            scripts_dir=str(scripts),
        )
    assert status.ok is True
    assert status.summary() == ""


def test_check_pot_provider_relative_server_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _valid_tree(tmp_path / "relhome")
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(
            server_home=Path("relhome"),
            scripts_dir=tmp_path,
        )
    assert status.ok is True


def test_check_pot_provider_uses_config_defaults_when_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_home = _valid_tree(tmp_path / "default_home")
    monkeypatch.setattr(pot_provider, "POT_PROVIDER_SERVER_HOME", default_home)
    monkeypatch.setattr(pot_provider, "VENV_SCRIPTS_DIR", tmp_path)
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider()
    assert status.ok is True
    assert status.summary() == ""


# ---------------------------------------------------------------------------
# check_pot_provider — file-vs-directory type confusion
# ---------------------------------------------------------------------------


def test_script_path_is_directory_not_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "generate_once.ts").mkdir()  # wrong type: dir, not file
    (tmp_path / "node_modules").mkdir()
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=tmp_path, scripts_dir=tmp_path)
    assert status.script_found is False
    assert status.ok is False
    assert "generate_once.ts" in status.summary()


def test_node_modules_path_is_file_not_dir(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    (home / "node_modules").rmdir()
    (home / "node_modules").touch()  # wrong type: file, not dir
    with (
        patch("src.pot_provider._plugin_importable", return_value=True),
        patch("src.pot_provider._deno_ok", return_value=True),
    ):
        status = pot_provider.check_pot_provider(server_home=home, scripts_dir=tmp_path)
    assert status.node_modules_found is False
    assert status.ok is False
    assert "node_modules" in status.summary()


# ---------------------------------------------------------------------------
# _escpath — comma escaping
# ---------------------------------------------------------------------------


def test_escpath_single_path() -> None:
    p = Path("foo/bar")
    assert pot_provider._escpath(p) == str(p)


def test_escpath_multiple_paths(tmp_path: Path) -> None:
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    result = pot_provider._escpath(p1, p2)
    assert f"{p1},{p2}" == result


def test_escpath_doubles_literal_commas(tmp_path: Path) -> None:
    p = Path("a,b")
    result = pot_provider._escpath(p)
    assert result == "a,,b"


def test_escpath_in_multiple_paths_with_commas(tmp_path: Path) -> None:
    p1 = Path("a,b")
    p2 = Path("c,d")
    result = pot_provider._escpath(p1, p2)
    assert result == "a,,b,c,,d"


# ---------------------------------------------------------------------------
# _script_cache_dir — environment resolution
# ---------------------------------------------------------------------------


def test_script_cache_dir_prefers_xdg_cache_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xdg = tmp_path / "xdg_cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    result = pot_provider._script_cache_dir(tmp_path)
    expected = xdg / "bgutil-ytdlp-pot-provider"
    assert result == expected


def test_script_cache_dir_falls_back_to_userprofile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    userprofile = tmp_path / "user"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    result = pot_provider._script_cache_dir(tmp_path)
    expected = userprofile / ".cache" / "bgutil-ytdlp-pot-provider"
    assert result == expected


def test_script_cache_dir_falls_back_to_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    result = pot_provider._script_cache_dir(tmp_path)
    expected = home / ".cache" / "bgutil-ytdlp-pot-provider"
    assert result == expected


def test_script_cache_dir_empty_string_xdg_is_honoured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Empty-string XDG_CACHE_HOME (set but blank) must be honoured, not treated as unset.

    The plugin gates on ``os.getenv('XDG_CACHE_HOME') is not None`` -- an empty string
    passes that check and is used as-is. If ours instead falls through to HOME/
    USERPROFILE on empty string, the warm-up caches a different directory than the
    real probe reads from and the whole fix silently stops working.
    """
    userprofile = tmp_path / "user"
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.chdir(tmp_path)

    result = pot_provider._script_cache_dir(tmp_path)

    expected = Path().absolute() / "bgutil-ytdlp-pot-provider"
    assert result == expected
    assert result != userprofile / ".cache" / "bgutil-ytdlp-pot-provider"


def test_script_cache_dir_falls_back_to_server_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_home = tmp_path / "server"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    result = pot_provider._script_cache_dir(server_home)
    assert result == server_home


# ---------------------------------------------------------------------------
# build_warm_cmd — argv construction and flag validation
# ---------------------------------------------------------------------------


def test_build_warm_cmd_flags_match_plugin_jsrt_args(tmp_path: Path) -> None:
    from yt_dlp_plugins.extractor.getpot_bgutil_script import BgUtilScriptDenoPTP

    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    cmd = pot_provider.build_warm_cmd(deno, home)

    assert cmd[1] == "run"
    # Extract flag names (everything before first = or as-is if no =) from --allow-* args.
    flag_names = set()
    for arg in cmd[2:]:
        if arg.startswith("--allow-"):
            flag_names.add(arg.split("=")[0])

    expected_flags = {
        "--allow-env",
        "--allow-net",
        "--allow-ffi",
        "--allow-write",
        "--allow-read",
    }
    assert flag_names == expected_flags
    assert BgUtilScriptDenoPTP._SCRIPT_BASENAME in cmd[-2]
    assert cmd[-1] == "--version"


def test_build_warm_cmd_points_at_server_home_script(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    cmd = pot_provider.build_warm_cmd(deno, home)

    script_path = home / "src" / "generate_once.ts"
    assert str(script_path) in cmd
    node_modules = home / "node_modules"
    assert f"--allow-ffi={node_modules}" in " ".join(cmd)


def test_build_warm_cmd_escapes_literal_commas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_home = tmp_path / "a,b"
    home = _valid_tree(server_home)
    deno = tmp_path / "deno.exe"
    deno.touch()
    xdg = tmp_path / "xdg"  # comma-free, so only the server_home comma is under test
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    cmd = pot_provider.build_warm_cmd(deno, home)

    # Deno's permission flags use "," as the path separator, so a literal comma in
    # the path must be doubled -- but only inside the flags. The script path is a
    # plain argv entry and stays raw, exactly as the plugin passes it.
    ffi = next(a for a in cmd if a.startswith("--allow-ffi="))
    read = next(a for a in cmd if a.startswith("--allow-read="))
    assert "a,,b" in ffi
    assert "a,,b" in read
    assert "a,b" in cmd[-2]
    assert "a,,b" not in cmd[-2]


def test_build_warm_cmd_realpaths_server_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin 2.x realpath-resolves server_home; the warm-up argv must follow suit."""
    link = tmp_path / "link"
    real = tmp_path / "real"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        "src.pot_provider.os.path.realpath",
        lambda p: str(real) if Path(p) == link else str(p),
    )
    cmd = pot_provider.build_warm_cmd("deno", link)

    ffi = next(a for a in cmd if a.startswith("--allow-ffi="))
    read = next(a for a in cmd if a.startswith("--allow-read="))
    assert ffi == f"--allow-ffi={real / 'node_modules'}"
    assert str(real / "node_modules") in read
    assert cmd[-2] == str(real / "src" / "generate_once.ts")
    assert str(link) not in " ".join(cmd)


# ---------------------------------------------------------------------------
# build_warm_env — environment construction
# ---------------------------------------------------------------------------


def test_build_warm_env_sets_deno_flags() -> None:
    env = pot_provider.build_warm_env()
    assert env["DENO_NO_PROMPT"] == "1"
    assert env["DENO_NO_UPDATE_CHECK"] == "1"
    assert env["FORCE_COLOR"] == "false"


def test_build_warm_env_preserves_existing_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_TEST_VAR", "my_value")
    env = pot_provider.build_warm_env()
    assert env["MY_TEST_VAR"] == "my_value"
    assert env["DENO_NO_PROMPT"] == "1"


def test_build_warm_env_copies_environ() -> None:
    env = pot_provider.build_warm_env()
    # Modifying returned env should not affect os.environ.
    env["NEW_KEY"] = "new_value"
    assert "NEW_KEY" not in os.environ


# ---------------------------------------------------------------------------
# warm_deno_cache — full integration
# ---------------------------------------------------------------------------


def test_warm_deno_cache_ok_on_returncode_zero(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="1.3.1\n", stderr=""),
    ):
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    assert result.ok is True
    assert result.elapsed_s >= 0.0
    assert result.detail == "1.3.1"


def test_warm_deno_cache_skips_when_deno_absent(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    with patch("src.pot_provider.subprocess.run") as mock_run:
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    mock_run.assert_not_called()
    assert result.ok is False
    assert "deno.exe" in result.detail


def test_warm_deno_cache_skips_when_script_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "node_modules").mkdir()
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch("src.pot_provider.subprocess.run") as mock_run:
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    mock_run.assert_not_called()
    assert result.ok is False
    assert "generate_once.ts" in result.detail


def test_warm_deno_cache_skips_when_node_modules_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "src").mkdir(parents=True)
    (home / "src" / "generate_once.ts").touch()
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch("src.pot_provider.subprocess.run") as mock_run:
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    mock_run.assert_not_called()
    assert result.ok is False
    assert "node_modules" in result.detail


def test_warm_deno_cache_returns_not_ok_on_timeout(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch(
        "src.pot_provider.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="deno", timeout=300),
    ):
        result = pot_provider.warm_deno_cache(
            server_home=home,
            scripts_dir=tmp_path,
            timeout=300.0,
        )
    assert result.ok is False
    assert "timed out" in result.detail.lower()


def test_warm_deno_cache_returns_not_ok_on_oserror(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch(
        "src.pot_provider.subprocess.run",
        side_effect=OSError("boom"),
    ):
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    assert result.ok is False
    # No exception should escape.


def test_warm_deno_cache_reports_stderr_on_failure(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=1, stdout="", stderr="boom"),
    ):
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    assert result.ok is False
    assert result.detail == "boom"


def test_warm_deno_cache_reports_stdout_on_failure_no_stderr(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch(
        "src.pot_provider.subprocess.run",
        return_value=MagicMock(returncode=1, stdout="fallback", stderr=""),
    ):
        result = pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    assert result.ok is False
    assert result.detail == "fallback"


def test_warm_deno_cache_passes_no_window_creationflag(tmp_path: Path) -> None:
    home = _valid_tree(tmp_path)
    deno = tmp_path / "deno.exe"
    deno.touch()
    with patch("src.pot_provider.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1.3.1",
            stderr="",
        )
        pot_provider.warm_deno_cache(server_home=home, scripts_dir=tmp_path)
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args[1]
    assert "creationflags" in call_kwargs
    assert call_kwargs["creationflags"] == pot_provider._NO_WINDOW
    if sys.platform == "win32":
        assert pot_provider._NO_WINDOW == subprocess.CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# _deno_ok — creationflags validation
# ---------------------------------------------------------------------------


def test_deno_ok_passes_no_window_creationflag(tmp_path: Path) -> None:
    (tmp_path / "deno.exe").touch()
    with patch("src.pot_provider.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="deno 2.5.4")
        pot_provider._deno_ok(tmp_path)
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args[1]
    assert "creationflags" in call_kwargs
    assert call_kwargs["creationflags"] == pot_provider._NO_WINDOW
    if sys.platform == "win32":
        assert pot_provider._NO_WINDOW == subprocess.CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# DenoWarmResult — dataclass invariants
# ---------------------------------------------------------------------------


def test_deno_warm_result_is_frozen_dataclass() -> None:
    result = pot_provider.DenoWarmResult(ok=True, elapsed_s=1.5, detail="version 1.3.1")
    with pytest.raises(FrozenInstanceError):
        result.ok = False

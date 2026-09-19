"""
Wiring tests for Phases 2-3 of the PO-token pipeline.

Covers the vendored provider, setup script, PyInstaller bundling, and
release CI ordering.

Coverage map
============
src.ydl_options.build_base_ydl_opts
    - extractor_args["youtubepot-bgutilscript"]["server_home"]
                                       -> [str(POT_PROVIDER_SERVER_HOME)] (list form)
    - extractor_args["youtube"]["player_client"]
                                       -> still ["web_safari", "tv"] (coexists)
    - the socket-timeout key is now the yt-dlp-valid "socket_timeout"
      ("socket-timeout" hyphenated key was silently ignored)

src.config.POT_PROVIDER_SERVER_HOME
    - non-frozen default resolves to the vendored vendor/bgutil-pot-provider/server

scripts/setup_pot_provider.py
    - build_deno_install_cmd -> deno install --allow-scripts=npm:canvas --frozen
    - main() skips deno install when node_modules already exists
    - main() runs deno install (cwd=server dir) when node_modules is absent

meadowlark.spec (Phase 3, text-based assertions -- Analysis() isn't
importable outside a real PyInstaller build)
    - bundles the server dir as "bgutil-server" and the external plugin dir
      as "yt-dlp-plugins/extractor"
    - copy_metadata("bgutil-ytdlp-pot-provider") is present (namespace-package
      metadata needed for yt-dlp's entry-point plugin discovery when frozen)
    - all 3 getpot_bgutil* modules are explicit hiddenimports

.github/workflows/release.yml (Phase 3, substring-position ordering --
no YAML parser available in this venv)
    - the PO-token setup step runs after the editable install and before
      the PyInstaller build
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from src import config as _config_mod
from src.pot_provider import DenoWarmResult

_SETUP_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup_pot_provider.py"


def _load_setup() -> types.ModuleType:
    """Import the standalone setup script as a module (not an importable package)."""
    spec = importlib.util.spec_from_file_location("setup_pot_provider", _SETUP_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# ydl_options.build_base_ydl_opts — extractor arg + socket key
# ---------------------------------------------------------------------------


class TestBuildBaseYdlOpts:
    """The base opts must point the bgutil script provider at our server copy."""

    def test_extractor_arg_sets_server_home(self) -> None:
        from src.ydl_options import build_base_ydl_opts

        fake = Path("X") / "server"
        with (
            patch("src.ydl_options.get_setting", return_value=None),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", fake),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        assert opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [str(fake)]

    def test_player_client_still_present(self) -> None:
        from src.ydl_options import build_base_ydl_opts

        with (
            patch("src.ydl_options.get_setting", return_value=None),
            patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", "web_safari,tv"),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", Path("X") / "server"),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        assert opts["extractor_args"]["youtube"]["player_client"] == [
            "web_safari",
            "tv",
        ]

    def test_socket_timeout_key_is_underscored(self) -> None:
        from src.ydl_options import build_base_ydl_opts

        with (
            patch("src.ydl_options.get_setting", return_value=None),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", Path("X") / "server"),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        assert "socket_timeout" in opts
        assert "socket-timeout" not in opts

    def test_server_home_is_single_element_str_list_not_bare_string(self) -> None:
        """A bare str would iterate char-by-char instead of indexing at [0]."""
        from src.ydl_options import build_base_ydl_opts

        fake = Path("X") / "server"
        with (
            patch("src.ydl_options.get_setting", return_value=None),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", fake),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        server_home = opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"]
        assert isinstance(server_home, list)
        assert len(server_home) == 1
        assert isinstance(server_home[0], str)

    def test_mark_watched_coexists_with_pot_extractor_args(self) -> None:
        """mark_watched must not clobber, or be clobbered by, extractor_args."""
        from src.ydl_options import build_base_ydl_opts

        fake = Path("X") / "server"

        def fake_get_setting(key: str) -> bool | None:
            return True if key == "VID_DL_MARK_WATCHED" else None

        with (
            patch("src.ydl_options.get_setting", side_effect=fake_get_setting),
            patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", "web_safari,tv"),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", fake),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        assert opts["mark_watched"] is True
        assert opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [str(fake)]
        assert opts["extractor_args"]["youtube"]["player_client"] == [
            "web_safari",
            "tv",
        ]

    def test_mark_watched_absent_when_setting_falsy(self) -> None:
        """No VID_DL_MARK_WATCHED setting must omit the key, not set it False."""
        from src.ydl_options import build_base_ydl_opts

        with (
            patch("src.ydl_options.get_setting", return_value=None),
            patch("src.ydl_options.POT_PROVIDER_SERVER_HOME", Path("X") / "server"),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())

        assert "mark_watched" not in opts


# ---------------------------------------------------------------------------
# config.POT_PROVIDER_SERVER_HOME — non-frozen default
# ---------------------------------------------------------------------------


class TestConfigDefaultHome:
    """Outside a frozen build the default is the vendored server dir."""

    def test_config_default_dev_points_at_vendor(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("VID_DL_POT_SERVER_HOME", None)
            had_frozen = hasattr(sys, "frozen")
            saved = getattr(sys, "frozen", None)
            if had_frozen:
                delattr(sys, "frozen")
            try:
                importlib.reload(_config_mod)
                assert _config_mod.POT_PROVIDER_SERVER_HOME.parts[-3:] == (
                    "vendor",
                    "bgutil-pot-provider",
                    "server",
                )
            finally:
                if had_frozen:
                    sys.frozen = saved  # type: ignore[attr-defined]
                importlib.reload(_config_mod)


class TestConfigEnvVarPrecedence:
    """VID_DL_POT_SERVER_HOME set/unset/empty/whitespace, and the frozen branch."""

    def test_env_var_override_used_when_set(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom-pot-server"
        with mock.patch.dict("os.environ", {"VID_DL_POT_SERVER_HOME": str(custom)}, clear=False):
            try:
                importlib.reload(_config_mod)
                assert custom == _config_mod.POT_PROVIDER_SERVER_HOME
            finally:
                importlib.reload(_config_mod)

    def test_env_var_empty_string_falls_back_to_default(self) -> None:
        with mock.patch.dict("os.environ", {"VID_DL_POT_SERVER_HOME": ""}, clear=False):
            try:
                importlib.reload(_config_mod)
                assert _config_mod.POT_PROVIDER_SERVER_HOME.parts[-3:] == (
                    "vendor",
                    "bgutil-pot-provider",
                    "server",
                )
            finally:
                importlib.reload(_config_mod)

    def test_env_var_whitespace_only_is_not_normalized_to_default(self) -> None:
        """_resolve_path only checks truthiness, so whitespace passes through verbatim."""
        with mock.patch.dict("os.environ", {"VID_DL_POT_SERVER_HOME": "   "}, clear=False):
            try:
                importlib.reload(_config_mod)
                assert Path("   ") == _config_mod.POT_PROVIDER_SERVER_HOME
            finally:
                importlib.reload(_config_mod)

    def test_frozen_build_points_at_meipass_bgutil_server(self, tmp_path: Path) -> None:
        import os

        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("VID_DL_POT_SERVER_HOME", None)
            had_frozen = hasattr(sys, "frozen")
            saved_frozen = getattr(sys, "frozen", None)
            had_meipass = hasattr(sys, "_MEIPASS")
            saved_meipass = getattr(sys, "_MEIPASS", None)
            sys.frozen = True  # type: ignore[attr-defined]
            sys._MEIPASS = str(tmp_path)  # type: ignore[attr-defined]
            try:
                importlib.reload(_config_mod)
                assert Path(str(tmp_path)) / "bgutil-server" == _config_mod.POT_PROVIDER_SERVER_HOME
            finally:
                if had_frozen:
                    sys.frozen = saved_frozen  # type: ignore[attr-defined]
                else:
                    delattr(sys, "frozen")
                if had_meipass:
                    sys._MEIPASS = saved_meipass  # type: ignore[attr-defined]
                else:
                    delattr(sys, "_MEIPASS")
                importlib.reload(_config_mod)


# ---------------------------------------------------------------------------
# scripts/setup_pot_provider.py -- resolve_deno()
# ---------------------------------------------------------------------------


class TestResolveDeno:
    """resolve_deno prefers the pinned .venv runtime, then PATH, then None."""

    def test_prefers_venv_deno_when_present(self, tmp_path: Path) -> None:
        mod = _load_setup()
        venv_deno = tmp_path / "deno.exe"
        venv_deno.write_text("")

        with (
            patch.object(mod, "_VENV_DENO", venv_deno),
            patch.object(mod.shutil, "which") as which,
        ):
            result = mod.resolve_deno()

        assert result == str(venv_deno)
        which.assert_not_called()

    def test_falls_back_to_path_when_venv_deno_absent(self, tmp_path: Path) -> None:
        mod = _load_setup()
        missing = tmp_path / "nope" / "deno.exe"

        with (
            patch.object(mod, "_VENV_DENO", missing),
            patch.object(mod.shutil, "which", return_value="/usr/bin/deno") as which,
        ):
            result = mod.resolve_deno()

        assert result == "/usr/bin/deno"
        which.assert_called_once_with("deno")

    def test_returns_none_when_neither_found(self, tmp_path: Path) -> None:
        mod = _load_setup()
        missing = tmp_path / "nope" / "deno.exe"

        with (
            patch.object(mod, "_VENV_DENO", missing),
            patch.object(mod.shutil, "which", return_value=None),
        ):
            result = mod.resolve_deno()

        assert result is None


# ---------------------------------------------------------------------------
# scripts/setup_pot_provider.py -- parse_args()
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults_to_no_force(self) -> None:
        mod = _load_setup()
        assert mod.parse_args([]).force is False

    def test_force_flag_sets_true(self) -> None:
        mod = _load_setup()
        assert mod.parse_args(["--force"]).force is True

    def test_unknown_flag_exits_nonzero(self) -> None:
        mod = _load_setup()
        with pytest.raises(SystemExit) as exc_info:
            mod.parse_args(["--bogus"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# scripts/setup_pot_provider.py -- main() branch matrix
# ---------------------------------------------------------------------------


class TestSetupMainBranchMatrix:
    """rc==2 error paths, --force override, non-zero passthrough, stray-file node_modules."""

    def test_server_dir_missing_returns_2_and_skips_deno_lookup(self, tmp_path: Path) -> None:
        mod = _load_setup()
        missing_server = tmp_path / "does-not-exist"

        with (
            patch.object(mod, "resolve_server_dir", return_value=missing_server),
            patch.object(mod, "resolve_deno") as resolve_deno,
            patch.object(mod.subprocess, "run") as run,
        ):
            rc = mod.main([])

        assert rc == 2
        resolve_deno.assert_not_called()
        run.assert_not_called()

    def test_deno_missing_returns_2(self, tmp_path: Path) -> None:
        mod = _load_setup()
        server = tmp_path / "server"
        server.mkdir()

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value=None),
            patch.object(mod.subprocess, "run") as run,
        ):
            rc = mod.main([])

        assert rc == 2
        run.assert_not_called()

    def test_already_installed_path_does_not_require_deno(self, tmp_path: Path) -> None:
        """
        main() must not *require* Deno on the already-installed skip path.

        main() does now look Deno up on this path -- the warm-up has to know where it
        is -- but an unresolvable Deno here is non-fatal: node_modules is present, so
        the install still counts as done and the exit code stays 0. Only the cache
        warm-up is forgone.
        """
        mod = _load_setup()
        server = tmp_path / "server"
        (server / "node_modules").mkdir(parents=True)

        warm_result = DenoWarmResult(ok=False, elapsed_s=0.0, detail="deno.exe not found")
        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value=None),
            patch.object(mod, "warm_deno_cache", return_value=warm_result) as warm,
            patch.object(mod.subprocess, "run") as run,
        ):
            rc = mod.main([])

        assert rc == 0
        run.assert_not_called()
        # Warmed with no scripts_dir -> falls back to VENV_SCRIPTS_DIR and reports a
        # non-fatal miss, rather than blowing up on a None Deno path.
        warm.assert_called_once_with(server_home=server, scripts_dir=None)

    def test_force_reinstalls_when_node_modules_already_present(self, tmp_path: Path) -> None:
        mod = _load_setup()
        server = tmp_path / "server"
        (server / "node_modules").mkdir(parents=True)

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value="/fake/deno"),
            patch.object(mod.subprocess, "run", return_value=MagicMock(returncode=0)) as run,
        ):
            rc = mod.main(["--force"])

        assert rc == 0
        run.assert_called_once()
        assert run.call_args.kwargs["cwd"] == server

    def test_nonzero_deno_returncode_propagated_verbatim(self, tmp_path: Path) -> None:
        mod = _load_setup()
        server = tmp_path / "server"
        server.mkdir()

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value="/fake/deno"),
            patch.object(mod.subprocess, "run", return_value=MagicMock(returncode=17)),
        ):
            rc = mod.main([])

        assert rc == 17

    def test_node_modules_as_stray_file_is_treated_as_absent(self, tmp_path: Path) -> None:
        """A stray node_modules file (not dir) fails is_dir(), so main() reinstalls."""
        mod = _load_setup()
        server = tmp_path / "server"
        server.mkdir()
        (server / "node_modules").write_text("")

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value="/fake/deno"),
            patch.object(mod.subprocess, "run", return_value=MagicMock(returncode=0)) as run,
        ):
            rc = mod.main([])

        assert rc == 0
        run.assert_called_once()


# ---------------------------------------------------------------------------
# scripts/setup_pot_provider.py — deno install orchestration
# ---------------------------------------------------------------------------


class TestSetupScript:
    """The setup CLI builds the right deno command and is idempotent."""

    def test_setup_builds_expected_deno_cmd(self) -> None:
        mod = _load_setup()
        assert mod.build_deno_install_cmd("/x/deno.exe") == [
            "/x/deno.exe",
            "install",
            "--allow-scripts=npm:canvas",
            "--frozen",
        ]

    def test_setup_skips_when_node_modules_present(self, tmp_path: Path) -> None:
        mod = _load_setup()
        server = tmp_path / "server"
        (server / "node_modules").mkdir(parents=True)

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod.subprocess, "run") as run,
        ):
            rc = mod.main([])

        assert rc == 0
        run.assert_not_called()

    def test_setup_runs_install_when_absent(self, tmp_path: Path) -> None:
        mod = _load_setup()
        server = tmp_path / "server"
        server.mkdir()

        with (
            patch.object(mod, "resolve_server_dir", return_value=server),
            patch.object(mod, "resolve_deno", return_value="/fake/deno"),
            patch.object(mod.subprocess, "run", return_value=MagicMock(returncode=0)) as run,
        ):
            rc = mod.main([])

        assert rc == 0
        run.assert_called_once()
        assert run.call_args.kwargs["cwd"] == server


# ---------------------------------------------------------------------------
# meadowlark.spec — Phase 3 bundling of the provider plugin + server dir
# ---------------------------------------------------------------------------

_SPEC_PATH = Path(__file__).resolve().parent.parent / "meadowlark.spec"


class TestSpecBundlesProviderAndServer:
    """The PyInstaller spec must ship the provider plugin and its server dir."""

    def test_spec_bundles_provider_and_server(self) -> None:
        spec_text = _SPEC_PATH.read_text(encoding="utf-8")

        # Server-dir dest name must equal the frozen POT_PROVIDER_SERVER_HOME
        # default (sys._MEIPASS/bgutil-server in src.config), or the healthcheck
        # and script provider look in the wrong place.
        assert "bgutil-server" in spec_text
        # yt-dlp's documented external-plugin layout beside the exe (fallback
        # discovery path alongside the hiddenimports).
        assert "yt-dlp-plugins/extractor" in spec_text
        # The script-mode provider module is force-imported so PyInstaller's
        # namespace-package pruning cannot drop it.
        assert "getpot_bgutil_script" in spec_text

    def test_spec_copies_provider_package_metadata(self) -> None:
        """
        Assert the spec re-exposes the plugin's package metadata.

        copy_metadata is required so importlib.metadata reports the plugin
        as installed inside the frozen bundle; without it yt-dlp's entry-point
        plugin discovery silently skips it (metadata-less packages are
        invisible to importlib.metadata.distributions()).
        """
        spec_text = _SPEC_PATH.read_text(encoding="utf-8")
        assert 'copy_metadata("bgutil-ytdlp-pot-provider")' in spec_text

    def test_spec_declares_all_three_bgutil_hiddenimports(self) -> None:
        """
        Assert all 3 getpot_bgutil* submodules are explicit hiddenimports.

        yt_dlp_plugins is a namespace package, so collect_submodules() alone
        is not guaranteed to discover them under PyInstaller's static
        analysis.
        """
        spec_text = _SPEC_PATH.read_text(encoding="utf-8")
        for module in (
            "yt_dlp_plugins.extractor.getpot_bgutil",
            "yt_dlp_plugins.extractor.getpot_bgutil_http",
            "yt_dlp_plugins.extractor.getpot_bgutil_script",
        ):
            assert f'"{module}"' in spec_text

    def test_spec_collects_yt_dlp_plugins_submodules(self) -> None:
        spec_text = _SPEC_PATH.read_text(encoding="utf-8")
        assert 'collect_submodules("yt_dlp_plugins")' in spec_text


# ---------------------------------------------------------------------------
# .github/workflows/release.yml -- PO-token setup step ordering
# ---------------------------------------------------------------------------

_WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


class TestReleaseWorkflowStepOrder:
    """
    Verify step order in the release workflow.

    deno install must run after the editable install (copy_metadata in the
    spec needs the plugin package registered in site-packages metadata) and
    before PyInstaller runs (the build needs node_modules on disk to bundle
    it into the frozen server dir). No YAML parser is available in this venv,
    so ordering is checked via substring position rather than a real parse.
    """

    def test_pot_provider_setup_step_present(self) -> None:
        workflow_text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "scripts/setup_pot_provider.py" in workflow_text

    def test_pot_provider_setup_runs_after_editable_install_and_before_build(
        self,
    ) -> None:
        workflow_text = _WORKFLOW_PATH.read_text(encoding="utf-8")

        editable_install_idx = workflow_text.index("uv pip install -e .")
        pot_setup_idx = workflow_text.index("scripts/setup_pot_provider.py")
        build_idx = workflow_text.index("pyinstaller meadowlark.spec")

        assert editable_install_idx < pot_setup_idx < build_idx

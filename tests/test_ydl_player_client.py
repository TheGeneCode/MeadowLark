"""
Boundary tests for the YouTube player-client override (yt-dlp #14680 fix).

Coverage map
============
config.YOUTUBE_PLAYER_CLIENTS
    - default (absent env var)        -> "web_embedded" (the client measured
                                         to serve complete files; mweb's URLs
                                         carry a valid pot= but 403 after ~1 MB.
                                         tv_embedded served complete files too
                                         but yt-dlp dropped it from its client
                                         registry -- see yt-dlp#17389)
    - default must not contain mweb   -> regression guard for that 1 MB cutoff
    - custom VID_DL_YT_PLAYER_CLIENT  -> verbatim string

ydl_options.build_base_ydl_opts -> extractor_args["youtube"]["player_client"]
    - default "web_embedded"          -> ["web_embedded"]
    - single "tv"                     -> ["tv"]
    - trailing comma                  -> no empty entries
    - internal / surrounding spaces   -> stripped
    - consecutive commas              -> no empty entries
    - empty / whitespace-only string  -> [] (no crash)
    - many clients                    -> order preserved
    - result is always a list
    - coexists with cookiefile/js_runtimes/remote_components/mark_watched

scripts/yt_dlp_channel.py
    - read_pinned_nightly: pin present / absent / unreadable file
    - fetch_latest_stable: parses info.version / missing field raises
    - main exit codes + nightly-vs-stable version ordering
"""

import importlib
import importlib.util
import json
import os
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from src import config as _config_mod

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "yt_dlp_channel.py"


# ---------------------------------------------------------------------------
# config.YOUTUBE_PLAYER_CLIENTS — env-var resolution
# ---------------------------------------------------------------------------


class TestConfigPlayerClients:
    """The config constant must default safely and honour the env override."""

    def test_default_absent_env_var(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_YT_PLAYER_CLIENT", None)
            importlib.reload(_config_mod)
            assert _config_mod.YOUTUBE_PLAYER_CLIENTS == "web_embedded"
        importlib.reload(_config_mod)

    def test_default_excludes_clients_cut_off_after_one_megabyte(self) -> None:
        """
        Regression (2026-08): the default must not fall back to 'mweb'.

        A valid GVS PO token stopped being sufficient. YouTube now enforces SABR
        on mweb: its media URLs carry a correct pot=, the transfer starts, and
        the server 403s it after ~1 MB (measured 1.00-1.07 MB across 10 KB,
        256 KB and 1 MB chunk sizes). Because the first client supplying a
        format id wins, listing mweb at all lets it take rungs web_embedded
        could have served, reintroducing the cutoff. A short-range probe
        (--test, 10 KB) succeeds against mweb, so only a full download detects
        this.
        """
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_YT_PLAYER_CLIENT", None)
            importlib.reload(_config_mod)
            clients = [c.strip() for c in _config_mod.YOUTUBE_PLAYER_CLIENTS.split(",")]
            assert "mweb" not in clients
            assert clients[0] == "web_embedded"
        importlib.reload(_config_mod)

    def test_custom_env_value(self) -> None:
        with mock.patch.dict(os.environ, {"VID_DL_YT_PLAYER_CLIENT": "android_vr,web"}):
            importlib.reload(_config_mod)
            assert _config_mod.YOUTUBE_PLAYER_CLIENTS == "android_vr,web"
        importlib.reload(_config_mod)


# ---------------------------------------------------------------------------
# build_base_ydl_opts — player_client parsing
# ---------------------------------------------------------------------------


def _player_clients(raw: str) -> list[str]:
    """Run build_base_ydl_opts with YOUTUBE_PLAYER_CLIENTS=raw and return the list."""
    from src.ydl_options import build_base_ydl_opts

    with (
        patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", raw),
        patch("src.ydl_options.get_setting", return_value=None),
    ):
        opts = build_base_ydl_opts(MagicMock(), MagicMock())
    return opts["extractor_args"]["youtube"]["player_client"]


class TestPlayerClientParsing:
    """Boundary matrix for the comma-split that feeds extractor_args."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("web_safari,tv", ["web_safari", "tv"]),
            ("tv", ["tv"]),
            ("web_safari,tv,", ["web_safari", "tv"]),
            ("web_safari, tv", ["web_safari", "tv"]),
            ("web_safari,,tv", ["web_safari", "tv"]),
            (" web_safari , tv ", ["web_safari", "tv"]),
            ("web_safari,tv,android_vr,mweb", ["web_safari", "tv", "android_vr", "mweb"]),
            (",", []),
            ("", []),
            ("   ", []),
        ],
    )
    def test_parsing(self, raw: str, expected: list[str]) -> None:
        assert _player_clients(raw) == expected

    def test_result_is_list(self) -> None:
        assert isinstance(_player_clients("web_safari,tv"), list)

    def test_no_empty_or_whitespace_entries(self) -> None:
        result = _player_clients(" web_safari ,, tv , ")
        assert all(c and c == c.strip() for c in result)

    def test_coexists_with_other_base_keys(self) -> None:
        from src.ydl_options import build_base_ydl_opts

        with (
            patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", "web_safari,tv"),
            patch("src.ydl_options.get_setting", return_value=None),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())
        for key in ("logger", "progress_hooks", "cookiefile", "js_runtimes", "remote_components"):
            assert key in opts, f"base key '{key}' was dropped"
        assert opts["extractor_args"]["youtube"]["player_client"] == ["web_safari", "tv"]

    def test_mark_watched_still_applies_with_extractor_args(self) -> None:
        from src.ydl_options import build_base_ydl_opts

        with (
            patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", "tv"),
            patch("src.ydl_options.get_setting", return_value=True),
        ):
            opts = build_base_ydl_opts(MagicMock(), MagicMock())
        assert opts.get("mark_watched") is True
        assert "extractor_args" in opts


# ---------------------------------------------------------------------------
# scripts/yt_dlp_channel.py — revert checker
# ---------------------------------------------------------------------------


def _load_channel() -> types.ModuleType:
    """Import the standalone script as a module (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("yt_dlp_channel", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_urlopen(payload: str) -> MagicMock:
    """Build a context-manager mock whose response.read() json.load can consume."""
    resp = MagicMock()
    resp.__enter__.return_value.read.return_value = payload.encode()
    return resp


_PIN_LINE = (
    'yt-dlp = { url = "https://github.com/yt-dlp/yt-dlp-nightly-builds/'
    'releases/download/2026.06.21.235142/yt-dlp.tar.gz" }'
)


class TestChannelChecker:
    """Pin parsing, PyPI parsing, and the nightly-vs-stable ordering decision."""

    def test_read_pin_present(self, tmp_path: Path) -> None:
        mod = _load_channel()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(f"[tool.uv.sources]\n{_PIN_LINE}\n", encoding="utf-8")
        assert mod.read_pinned_nightly(pyproject) == "2026.06.21.235142"

    def test_read_pin_absent(self, tmp_path: Path) -> None:
        mod = _load_channel()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\ndependencies = ["yt-dlp>=2025.7.21"]\n', encoding="utf-8")
        assert mod.read_pinned_nightly(pyproject) is None

    def test_read_pin_unreadable_raises(self, tmp_path: Path) -> None:
        mod = _load_channel()
        with pytest.raises(SystemExit):
            mod.read_pinned_nightly(tmp_path / "does_not_exist.toml")

    def test_fetch_latest_stable_parses_version(self) -> None:
        mod = _load_channel()
        payload = json.dumps({"info": {"version": "2026.6.9"}})
        with patch("urllib.request.urlopen", return_value=_fake_urlopen(payload)):
            assert mod.fetch_latest_stable("https://example.test") == "2026.6.9"

    def test_fetch_latest_stable_missing_version_raises(self) -> None:
        mod = _load_channel()
        with (
            patch("urllib.request.urlopen", return_value=_fake_urlopen("{}")),
            pytest.raises(SystemExit),
        ):
            mod.fetch_latest_stable("https://example.test")

    def test_main_no_pin_returns_3(self) -> None:
        mod = _load_channel()
        with patch.object(mod, "read_pinned_nightly", return_value=None):
            assert mod.main() == 3

    def test_main_stable_older_returns_1(self) -> None:
        mod = _load_channel()
        with (
            patch.object(mod, "read_pinned_nightly", return_value="2026.06.21.235142"),
            patch.object(mod, "fetch_latest_stable", return_value="2026.6.9"),
        ):
            assert mod.main() == 1

    def test_main_stable_newer_returns_0(self) -> None:
        mod = _load_channel()
        with (
            patch.object(mod, "read_pinned_nightly", return_value="2026.06.21.235142"),
            patch.object(mod, "fetch_latest_stable", return_value="2026.7.1"),
        ):
            assert mod.main() == 0

    def test_main_same_day_stable_stays_on_nightly(self) -> None:
        # A stable cut on the same day as the nightly sorts LOWER (nightly has an
        # extra .HHMMSS segment), so we must remain on the nightly.
        mod = _load_channel()
        with (
            patch.object(mod, "read_pinned_nightly", return_value="2026.06.21.235142"),
            patch.object(mod, "fetch_latest_stable", return_value="2026.6.21"),
        ):
            assert mod.main() == 1

    def test_main_equal_version_stays_on_nightly(self) -> None:
        mod = _load_channel()
        with (
            patch.object(mod, "read_pinned_nightly", return_value="2026.06.21.235142"),
            patch.object(mod, "fetch_latest_stable", return_value="2026.06.21.235142"),
        ):
            assert mod.main() == 1

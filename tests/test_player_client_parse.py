"""
Boundary tests for the YOUTUBE_PLAYER_CLIENTS → player_client list transformation.

The list comprehension in build_base_ydl_opts reads the module-level constant
``src.ydl_options.YOUTUBE_PLAYER_CLIENTS`` (imported at module load time from
src.config).  Tests must patch that name in the *ydl_options* namespace, NOT
the environment variable, because the module is already loaded.

Coverage matrix
===============
A1  two clients "web_safari,tv"            → ["web_safari", "tv"]
    (the shipped default is "mweb,tv" — see test_ydl_player_client.py; these
    raw strings only exercise the comma-split, not the default)
A2  empty string ""                        → []
A3  whitespace-only "   "                  → []
A4  single client "web_safari"             → ["web_safari"]
A5  leading/trailing commas ",web_safari," → ["web_safari"]
A6  internal double comma "web_safari,,tv" → ["web_safari", "tv"]
A7  spaces around entries " web_safari , tv " → ["web_safari", "tv"]
A8  comma-only ","                         → []
A9  multiple commas ",,"                   → []
A10 many clients "web_safari,tv,mweb,android" → ["web_safari","tv","mweb","android"]
A11 whitespace + commas "  ,  ,web_safari,  "  → ["web_safari"]
A12 uppercase entry "WEB_SAFARI"           → ["WEB_SAFARI"] (case preserved)

B1  extractor_args present when mark_watched=False
B2  extractor_args AND mark_watched both present when mark_watched=True
B3  cookiefile always present
B4  js_runtimes always present
B5  remote_components always present
B6  player_client value is a list (not a set/tuple)
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_logger() -> MagicMock:
    return MagicMock()


def _mock_hook() -> MagicMock:
    return MagicMock()


def _build_opts(player_clients: str, mark_watched_setting: Any = False) -> dict[str, Any]:
    """
    Call build_base_ydl_opts with patched module globals.

    - src.ydl_options.YOUTUBE_PLAYER_CLIENTS patched to *player_clients*
    - src.ydl_options.get_setting returning *mark_watched_setting*
    """
    from src.ydl_options import build_base_ydl_opts

    with (
        patch("src.ydl_options.YOUTUBE_PLAYER_CLIENTS", player_clients),
        patch("src.ydl_options.get_setting", return_value=mark_watched_setting),
    ):
        return build_base_ydl_opts(_mock_logger(), _mock_hook())


# ---------------------------------------------------------------------------
# A-series: player_client list parsing (Dimension 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("web_safari,tv", ["web_safari", "tv"]),  # A1 two clients
        ("", []),  # A2 empty string
        ("   ", []),  # A3 whitespace-only
        ("web_safari", ["web_safari"]),  # A4 single client
        (",web_safari,", ["web_safari"]),  # A5 leading/trailing commas
        ("web_safari,,tv", ["web_safari", "tv"]),  # A6 internal double comma
        (" web_safari , tv ", ["web_safari", "tv"]),  # A7 spaces around entries
        (",", []),  # A8 comma-only
        (",,", []),  # A9 multiple commas only
        (
            "web_safari,tv,mweb,android",  # A10 many clients
            ["web_safari", "tv", "mweb", "android"],
        ),
        ("  ,  ,web_safari,  ", ["web_safari"]),  # A11 whitespace + commas
        ("WEB_SAFARI", ["WEB_SAFARI"]),  # A12 uppercase preserved
    ],
)
def test_player_client_parse_produces_correct_list(raw: str, expected: list[str]) -> None:
    """extractor_args['youtube']['player_client'] must match expected list for each input."""
    opts = _build_opts(raw)
    result = opts["extractor_args"]["youtube"]["player_client"]
    assert result == expected


def test_player_client_list_contains_no_empty_strings_for_default() -> None:
    """Default value must never produce empty-string entries in the list."""
    opts = _build_opts("web_safari,tv")
    clients = opts["extractor_args"]["youtube"]["player_client"]
    assert all(c != "" for c in clients)


def test_player_client_list_contains_no_whitespace_only_entries_for_padded_input() -> None:
    """Whitespace entries must be filtered out, not stripped-to-empty and kept."""
    opts = _build_opts("  ,  ,web_safari,  ")
    clients = opts["extractor_args"]["youtube"]["player_client"]
    assert all(c.strip() != "" for c in clients)
    assert "" not in clients


def test_player_client_is_list_not_tuple_or_set() -> None:
    """yt-dlp expects a list, not a tuple or set."""
    opts = _build_opts("web_safari,tv")
    clients = opts["extractor_args"]["youtube"]["player_client"]
    assert isinstance(clients, list)


def test_player_client_empty_string_input_produces_empty_list_not_list_with_empty_str() -> None:
    """Empty env var must yield [] not [''] — the falsy-filter catches the empty token."""
    opts = _build_opts("")
    clients = opts["extractor_args"]["youtube"]["player_client"]
    assert clients == []
    assert "" not in clients


# ---------------------------------------------------------------------------
# B-series: extractor_args coexistence with other base opts (Dimension 2)
# ---------------------------------------------------------------------------


def test_extractor_args_present_when_mark_watched_false() -> None:
    """extractor_args must be in opts even when mark_watched is disabled (B1)."""
    opts = _build_opts("web_safari,tv", mark_watched_setting=False)
    assert "extractor_args" in opts


def test_extractor_args_and_mark_watched_both_present_when_setting_true() -> None:
    """Both extractor_args and mark_watched must coexist when mark_watched enabled (B2)."""
    opts = _build_opts("web_safari,tv", mark_watched_setting=True)
    assert "extractor_args" in opts
    assert "mark_watched" in opts
    assert opts["mark_watched"] is True


def test_cookiefile_always_present(tmp_path: Any) -> None:
    """Cookiefile key must always be set regardless of player_client value (B3)."""
    opts = _build_opts("web_safari,tv")
    assert "cookiefile" in opts
    assert opts["cookiefile"] is not None


def test_js_runtimes_always_present() -> None:
    """js_runtimes key must always be set (B4)."""
    opts = _build_opts("web_safari,tv")
    assert "js_runtimes" in opts


def test_remote_components_always_present() -> None:
    """remote_components key must always be set (B5)."""
    opts = _build_opts("web_safari,tv")
    assert "remote_components" in opts
    assert opts["remote_components"] == ["ejs:github"]


def test_extractor_args_youtube_key_is_dict() -> None:
    """extractor_args['youtube'] must be a dict with a 'player_client' key (B6)."""
    opts = _build_opts("web_safari,tv")
    youtube_args = opts["extractor_args"]["youtube"]
    assert isinstance(youtube_args, dict)
    assert "player_client" in youtube_args


def test_extractor_args_structure_is_nested_correctly() -> None:
    """extractor_args must be {youtube: {player_client: [...]}} — depth matters for yt-dlp."""
    opts = _build_opts("web_safari,tv")
    assert isinstance(opts.get("extractor_args"), dict)
    assert isinstance(opts["extractor_args"].get("youtube"), dict)
    assert isinstance(opts["extractor_args"]["youtube"].get("player_client"), list)


def test_empty_player_client_env_extractor_args_still_present() -> None:
    """Even when the parsed list is empty (bad env var), extractor_args key must still exist."""
    opts = _build_opts("")
    # The key must still be present — yt-dlp handles an empty list gracefully
    assert "extractor_args" in opts
    assert "youtube" in opts["extractor_args"]


def test_all_required_base_keys_present_with_default_clients() -> None:
    """Smoke test: all expected base keys must be present after the extractor_args addition."""
    opts = _build_opts("web_safari,tv")
    required = {
        "logger",
        "progress_hooks",
        "windowsfilenames",
        "cookiefile",
        "postprocessors",
        "js_runtimes",
        "remote_components",
        "extractor_args",
    }
    missing = required - opts.keys()
    assert not missing, f"Missing keys: {missing}"

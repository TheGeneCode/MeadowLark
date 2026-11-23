"""Unit tests for src.ydl_utils."""

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from src.ydl_utils import (
    extract_playlist_info,
    extract_release_info,
    extract_video_entries,
)

_PLAYLISTEND_SENTINEL = 5


def _make_ydl_factory(
    info: dict,
    captured_opts: list[dict] | None = None,
) -> Callable[[dict], MagicMock]:
    """Return a callable that acts as a YoutubeDL class, capturing opts if provided."""

    def factory(opts: dict) -> MagicMock:
        if captured_opts is not None:
            captured_opts.append(dict(opts))
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.extract_info.return_value = info
        return mock

    return factory


def test_extract_playlist_info_returns_info() -> None:
    factory = _make_ydl_factory({"title": "test playlist"})
    result = extract_playlist_info("https://example.com", ydl_class=factory)
    assert result == {"title": "test playlist"}


def test_extract_playlist_info_without_playlistend_does_not_set_key() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info("https://example.com", playlistend=None, ydl_class=factory)
    assert "playlistend" not in captured[0]


def test_extract_playlist_info_with_playlistend_sets_key() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        playlistend=_PLAYLISTEND_SENTINEL,
        ydl_class=factory,
    )
    assert captured[0]["playlistend"] == _PLAYLISTEND_SENTINEL


def test_extract_video_entries_returns_entries_list() -> None:
    factory = _make_ydl_factory({"entries": [{"id": "v1"}, {"id": "v2"}]})
    entries = extract_video_entries("https://example.com", ydl_class=factory)
    assert entries == [{"id": "v1"}, {"id": "v2"}]


def test_extract_video_entries_wraps_single_video_in_list() -> None:
    info = {"id": "single_video", "title": "A Video"}
    factory = _make_ydl_factory(info)
    entries = extract_video_entries("https://example.com", ydl_class=factory)
    assert entries == [info]


def test_extract_playlist_info_uses_default_ydl_class_when_none() -> None:
    from unittest.mock import patch

    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.extract_info.return_value = {"title": "default class"}

    with patch("src.ydl_utils.yt_dlp.YoutubeDL", return_value=mock_instance):
        result = extract_playlist_info("https://example.com")
    assert result == {"title": "default class"}


def test_extract_playlist_info_with_extra_opts_merges_into_opts() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        ydl_class=factory,
        extra_opts={"cookiefile": "/cookies.txt", "custom_key": "custom_val"},
    )
    assert captured[0]["cookiefile"] == "/cookies.txt"
    assert captured[0]["custom_key"] == "custom_val"
    assert captured[0]["quiet"] is True


def test_extract_playlist_info_extra_opts_none_leaves_baseline_intact() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info("https://example.com", ydl_class=factory, extra_opts=None)
    assert set(captured[0].keys()) == {
        "quiet",
        "no_warnings",
        "ignore_no_formats_error",
        "js_runtimes",
        "extractor_args",
    }


def test_extract_video_entries_uses_default_ydl_class_when_none() -> None:
    from unittest.mock import patch

    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.extract_info.return_value = {"entries": [{"id": "v1"}]}

    with patch("src.ydl_utils.yt_dlp.YoutubeDL", return_value=mock_instance):
        entries = extract_video_entries("https://example.com")
    assert entries == [{"id": "v1"}]


# ---------------------------------------------------------------------------
# New boundary tests for extra_opts and playlistend edge cases.
# ---------------------------------------------------------------------------


def test_extract_playlist_info_extra_opts_empty_dict_does_not_merge() -> None:
    """
    extra_opts={} is falsy — the `if extra_opts:` guard skips the update.

    Consequence: an empty dict passed by a caller is silently ignored, which
    is consistent with the documented behaviour ("when provided, its keys are
    merged"). Verify the baseline opts remain unchanged.
    """
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info("https://example.com", ydl_class=factory, extra_opts={})
    assert set(captured[0].keys()) == {
        "quiet",
        "no_warnings",
        "ignore_no_formats_error",
        "js_runtimes",
        "extractor_args",
    }


def test_extract_playlist_info_extra_opts_overrides_quiet_baseline() -> None:
    """extra_opts applied after baseline — a conflicting key wins over _METADATA_PROBE_OPTS."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        ydl_class=factory,
        extra_opts={"quiet": False},
    )
    # extra_opts must win (applied after baseline via opts.update())
    assert captured[0]["quiet"] is False
    # no_warnings from baseline still present
    assert captured[0]["no_warnings"] is True


def test_extract_playlist_info_extra_opts_playlistend_overridden_by_explicit_arg() -> None:
    """Explicit playlistend arg wins over any playlistend key in extra_opts (applied last)."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        playlistend=3,
        ydl_class=factory,
        extra_opts={"playlistend": 99},
    )
    # Explicit arg is applied after extra_opts, so it wins.
    assert captured[0]["playlistend"] == 3


def test_extract_playlist_info_playlistend_zero_does_not_set_key() -> None:
    """
    playlistend=0 is falsy — `if playlistend:` skips the assignment.

    This is a latent boundary: callers passing 0 to mean 'no limit' get the
    expected behaviour (no key set), but callers intending to pass 0 as a
    real limit would be silently ignored. Document this boundary.
    """
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info("https://example.com", playlistend=0, ydl_class=factory)
    assert "playlistend" not in captured[0]


def test_extract_playlist_info_extra_opts_adds_no_warnings_override() -> None:
    """extra_opts can override no_warnings from the baseline."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        ydl_class=factory,
        extra_opts={"no_warnings": False},
    )
    assert captured[0]["no_warnings"] is False
    assert captured[0]["quiet"] is True


# ---------------------------------------------------------------------------
# PO-token provider wiring regression coverage.
#
# extract_playlist_info/extract_video_entries build their own YoutubeDL opts
# independently of ydl_options.build_base_ydl_opts. Both are reached from
# metadata-only call sites (meadowlark.pyw's on-demand "open latest episode"
# resolution via extract_playlist_info, and DownloadExecutor._extract_title's
# error-path title lookup) that must not fall back to the bgutil provider's
# stale default server_home -- the same cold-cache Deno-probe timeout that
# motivated build_shared_extraction_opts() in the first place.
# ---------------------------------------------------------------------------


def test_extract_playlist_info_carries_pot_provider_wiring() -> None:
    from src.config import POT_PROVIDER_SERVER_HOME

    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info("https://example.com", ydl_class=factory)

    assert captured[0]["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
        str(POT_PROVIDER_SERVER_HOME)
    ]
    assert captured[0]["extractor_args"]["youtube"]["player_client"]
    assert "js_runtimes" in captured[0]
    # baseline quiet options must survive the merge
    assert captured[0]["quiet"] is True
    assert captured[0]["no_warnings"] is True


def test_extract_playlist_info_extra_opts_still_win_over_pot_wiring() -> None:
    """extra_opts (applied last) can still override a shared-wiring key if needed."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_playlist_info(
        "https://example.com",
        ydl_class=factory,
        extra_opts={"extractor_args": {}},
    )
    assert captured[0]["extractor_args"] == {}


def test_extract_video_entries_carries_pot_provider_wiring() -> None:
    from src.config import POT_PROVIDER_SERVER_HOME

    captured: list[dict] = []
    factory = _make_ydl_factory({"entries": [{"id": "v1"}]}, captured_opts=captured)
    extract_video_entries("https://example.com", ydl_class=factory)

    assert captured[0]["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
        str(POT_PROVIDER_SERVER_HOME)
    ]
    # per-call key must survive the merge
    assert captured[0]["extract_flat"] is True


# ---------------------------------------------------------------------------
# Tests for extract_release_info: metadata-only probe tolerating no formats.
# ---------------------------------------------------------------------------


def test_extract_release_info_sets_ignore_no_formats_error() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", ydl_class=factory)

    assert captured[0]["ignore_no_formats_error"] is True
    assert captured[0]["skip_download"] is True
    assert captured[0]["noplaylist"] is True


def test_extract_release_info_carries_pot_provider_wiring() -> None:
    from src.config import POT_PROVIDER_SERVER_HOME

    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", ydl_class=factory)

    assert captured[0]["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
        str(POT_PROVIDER_SERVER_HOME)
    ]
    assert captured[0]["extractor_args"]["youtube"]["player_client"]
    assert "js_runtimes" in captured[0]


def test_extract_release_info_none_result_returns_empty_dict() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory(None, captured_opts=captured)
    result = extract_release_info("https://example.com", ydl_class=factory)

    assert result == {}


def test_extract_release_info_omits_cookiefile_when_not_given() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", ydl_class=factory)

    assert "cookiefile" not in captured[0]


def test_extract_release_info_cookiefile_provided_is_set_as_string() -> None:
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", cookiefile="/cookies.txt", ydl_class=factory)

    assert captured[0]["cookiefile"] == "/cookies.txt"


def test_extract_release_info_cookiefile_empty_string_is_omitted() -> None:
    """cookiefile="" is falsy -- the `if cookiefile:` guard skips it, same as None."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", cookiefile="", ydl_class=factory)

    assert "cookiefile" not in captured[0]


def test_extract_release_info_uses_default_ydl_class_when_none() -> None:
    from unittest.mock import patch

    mock_instance = MagicMock()
    mock_instance.__enter__ = MagicMock(return_value=mock_instance)
    mock_instance.__exit__ = MagicMock(return_value=False)
    mock_instance.extract_info.return_value = {"live_status": "is_upcoming"}

    with patch("src.ydl_utils.yt_dlp.YoutubeDL", return_value=mock_instance):
        result = extract_release_info("https://example.com")
    assert result == {"live_status": "is_upcoming"}


def test_extract_release_info_propagates_exception_from_extract_info() -> None:
    """
    A genuinely different extraction failure must still propagate.

    ignore_no_formats_error only downgrades the *no-formats* failure to a
    warning -- e.g. a private video or network error must still propagate to
    the caller, not be swallowed here.
    """

    def factory(opts: dict) -> MagicMock:
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.extract_info.side_effect = ValueError("boom")
        return mock

    with pytest.raises(ValueError, match="boom"):
        extract_release_info("https://example.com", ydl_class=factory)


def test_extract_release_info_full_opts_key_set() -> None:
    """The three new keys must survive alongside the shared PO-token wiring intact."""
    captured: list[dict] = []
    factory = _make_ydl_factory({}, captured_opts=captured)
    extract_release_info("https://example.com", cookiefile="/c.txt", ydl_class=factory)

    assert captured[0]["skip_download"] is True
    assert captured[0]["ignore_no_formats_error"] is True
    assert captured[0]["noplaylist"] is True
    assert captured[0]["cookiefile"] == "/c.txt"
    assert captured[0]["quiet"] is True
    assert captured[0]["no_warnings"] is True
    assert "js_runtimes" in captured[0]
    assert "extractor_args" in captured[0]

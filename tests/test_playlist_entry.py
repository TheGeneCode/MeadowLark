"""Tests for src.playlist_entry."""

from unittest.mock import Mock

import pytest

from src.playlist_entry import (
    EntryTarget,
    playlist_entry_target,
    playlist_id_of,
    restrict_to_entry,
)

PL = "PL" + "a" * 32


def test_playlist_id_of_playlist_url() -> None:
    assert playlist_id_of(f"https://www.youtube.com/playlist?list={PL}") == PL


def test_playlist_id_of_watch_url_with_list() -> None:
    assert playlist_id_of(f"https://www.youtube.com/watch?v=abc&list={PL}") == PL


@pytest.mark.parametrize("value", [PL, f"  {PL}  "])
def test_playlist_id_of_bare_id(value: str) -> None:
    assert playlist_id_of(value) == PL


@pytest.mark.parametrize(
    "value",
    ["https://www.youtube.com/watch?v=abc", "RUh8D2Hau2o", "", "   ", None, 123],
)
def test_playlist_id_of_rejects_non_playlists(value: object) -> None:
    assert playlist_id_of(value) is None


VID = "RUh8D2Hau2o"
WATCH = f"https://www.youtube.com/watch?v={VID}"
PL_URL = f"https://www.youtube.com/playlist?list={PL}"


def test_target_for_video_playlist_entry() -> None:
    assert playlist_entry_target(WATCH, "720playlists", PL) == EntryTarget(PL_URL, VID)


@pytest.mark.parametrize(
    ("url", "source", "playlist_id"),
    [
        (WATCH, "720playlists", None),
        (WATCH, "720playlists", ""),
        (WATCH, "720", PL),
        (WATCH, "audio_playlists", PL),
        (WATCH, "Update", PL),
        (WATCH, "999playlists", PL),
        ("https://example.com/v", "720playlists", PL),
        (PL_URL, "720playlists", PL),
    ],
)
def test_no_target_cases(url: str, source: str, playlist_id: str | None) -> None:
    assert playlist_entry_target(url, source, playlist_id) is None


@pytest.mark.parametrize(
    ("url", "source", "playlist_id"),
    [
        (WATCH, "720playlists", "   "),
        (WATCH, "720playlists", 123),
        (WATCH, 720, PL),
        (None, "720playlists", PL),
    ],
)
def test_no_target_for_malformed_record_values(
    url: object, source: object, playlist_id: object
) -> None:
    assert playlist_entry_target(url, source, playlist_id) is None


def test_target_strips_padded_playlist_id() -> None:
    assert playlist_entry_target(WATCH, "720playlists", f"  {PL}\n") == EntryTarget(PL_URL, VID)


def test_restrict_passes_info_with_null_id() -> None:
    assert restrict_to_entry(VID)({"id": None}, incomplete=True) is None


def test_restrict_rejects_other_entry() -> None:
    result = restrict_to_entry(VID)({"id": "other"}, incomplete=True)

    assert isinstance(result, str)
    assert "other" in result


@pytest.mark.parametrize("info", [{"id": VID}, {"playlist_id": PL}])
def test_restrict_passes_target_and_playlist_level(info: dict) -> None:
    assert restrict_to_entry(VID)(info, incomplete=True) is None


def test_restrict_delegates_to_inner_for_target_only() -> None:
    inner = Mock(return_value="live")
    mf = restrict_to_entry(VID, inner)

    assert mf({"id": VID}, incomplete=True) == "live"
    inner.assert_called_once_with({"id": VID}, True)
    inner.reset_mock()
    assert isinstance(mf({"id": "other"}, incomplete=True), str)
    inner.assert_not_called()

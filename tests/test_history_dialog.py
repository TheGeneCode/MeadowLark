"""Tests for history dialog filter logic."""

from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from src.history_dialog import (
    _ARCHIVED_FG,
    HistoryDialog,
    _archive_line_matches,
    _result_matches,
)

_app = QApplication.instance() or QApplication([])

_YT_URL_1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_YT_URL_2 = "https://www.youtube.com/watch?v=abc123XYZ01"
_VIDEO_ID_1 = "dQw4w9WgXcQ"
_VIDEO_ID_2 = "abc123XYZ01"


def _record(url: str | None, result: str = "SUCCESS", title: str = "Test") -> dict:
    return {
        "dt": "2025-01-01 12:00:00",
        "site": "youtube",
        "dtype": "video",
        "title": title,
        "result": result,
        "url": url,
    }


def _make_dialog(
    records: list[dict],
    archive_ids: set[str] | None = None,
) -> HistoryDialog:
    ids: set[str] = archive_ids if archive_ids is not None else set()
    with (
        patch("src.history_dialog.parse_history_log", return_value=records),
        patch("src.history_dialog.load_downloaded_video_ids", return_value=ids),
    ):
        return HistoryDialog()


def _is_row_blue(dialog: HistoryDialog, row: int) -> bool:
    item = dialog._table.item(row, 0)
    return item is not None and item.foreground().color() == _ARCHIVED_FG


class TestResultMatches:
    """Tests for _result_matches()."""

    def test_all_always_matches(self) -> None:
        assert _result_matches("SUCCESS", "All") is True
        assert _result_matches("FAIL", "All") is True
        assert _result_matches("SKIPPED (Short duration (<3 min))", "All") is True

    def test_success_exact_match(self) -> None:
        assert _result_matches("SUCCESS", "SUCCESS") is True

    def test_success_no_match(self) -> None:
        assert _result_matches("FAIL", "SUCCESS") is False
        assert _result_matches("SKIPPED (reason)", "SUCCESS") is False

    def test_fail_exact_match(self) -> None:
        assert _result_matches("FAIL", "FAIL") is True

    def test_fail_no_match(self) -> None:
        assert _result_matches("SUCCESS", "FAIL") is False

    def test_skipped_prefix_matches_any_reason(self) -> None:
        assert _result_matches("SKIPPED (Short duration (<3 min))", "SKIPPED") is True
        assert _result_matches("SKIPPED (Already downloaded)", "SKIPPED") is True
        assert _result_matches("SKIPPED", "SKIPPED") is True

    def test_skipped_does_not_match_success_or_fail(self) -> None:
        assert _result_matches("SUCCESS", "SKIPPED") is False
        assert _result_matches("FAIL", "SKIPPED") is False

    def test_skipped_prefix_only_not_substring(self) -> None:
        # A result that contains "SKIPPED" mid-string should NOT match
        assert _result_matches("NOT SKIPPED", "SKIPPED") is False


class TestArchiveLineMatches:
    """Tests for _archive_line_matches()."""

    @pytest.mark.parametrize(
        "line",
        [
            "youtube dQw4w9WgXcQ\n",
            "youtube dQw4w9WgXcQ",
            "youtube  dQw4w9WgXcQ\n",  # double space
            "youtube\tdQw4w9WgXcQ\n",  # tab separator
        ],
    )
    def test_matches_standard_and_whitespace_variants(self, line: str) -> None:
        assert _archive_line_matches(line, "dQw4w9WgXcQ") is True

    def test_no_match_different_id(self) -> None:
        assert _archive_line_matches("youtube otherId\n", "dQw4w9WgXcQ") is False

    def test_no_match_id_is_prefix(self) -> None:
        assert _archive_line_matches("youtube XdQw4w9WgXcQ\n", "dQw4w9WgXcQ") is False

    def test_no_match_id_is_suffix(self) -> None:
        assert _archive_line_matches("youtube dQw4w9WgXcQextra\n", "dQw4w9WgXcQ") is False

    def test_blank_line_never_matches(self) -> None:
        assert _archive_line_matches("", "dQw4w9WgXcQ") is False
        assert _archive_line_matches("   \n", "dQw4w9WgXcQ") is False

    def test_comment_line_never_matches(self) -> None:
        # Guard against false positive on comment lines in hand-edited archives.
        assert _archive_line_matches("# dQw4w9WgXcQ\n", "dQw4w9WgXcQ") is False
        assert _archive_line_matches("# some note dQw4w9WgXcQ\n", "dQw4w9WgXcQ") is False


class TestRefreshArchiveStylesFor:
    """Tests for _refresh_archive_styles_for."""

    def test_colors_all_matching_rows_blue(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_1), _record(_YT_URL_1)], archive_ids=set())
        dialog._archive_ids.add(_VIDEO_ID_1)
        dialog._refresh_archive_styles_for(_VIDEO_ID_1)
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)

    def test_clears_blue_from_all_matching_rows(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_1), _record(_YT_URL_1)], archive_ids={_VIDEO_ID_1})
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)
        dialog._archive_ids.discard(_VIDEO_ID_1)
        dialog._refresh_archive_styles_for(_VIDEO_ID_1)
        assert not _is_row_blue(dialog, 0)
        assert not _is_row_blue(dialog, 1)

    def test_rows_with_different_video_id_unaffected(self) -> None:
        dialog = _make_dialog(
            [_record(_YT_URL_1), _record(_YT_URL_2)],
            archive_ids={_VIDEO_ID_1, _VIDEO_ID_2},
        )
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)
        # Remove video1 from archive and refresh only video1
        dialog._archive_ids.discard(_VIDEO_ID_1)
        dialog._refresh_archive_styles_for(_VIDEO_ID_1)
        assert not _is_row_blue(dialog, 0)  # video1 cleared
        assert _is_row_blue(dialog, 1)  # video2 unaffected


class TestPrependRowArchive:
    """Tests archive coloring when rows are prepended dynamically."""

    def test_success_new_row_is_blue_despite_archive_not_yet_flushed(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_2, result="FAIL")], archive_ids=set())
        # Archive file is still empty (yt-dlp hasn't written it yet)
        with patch("src.history_dialog.load_downloaded_video_ids", return_value=set()):
            dialog.prepend_row(_record(_YT_URL_1, result="SUCCESS"))
        assert _is_row_blue(dialog, 0)

    def test_fail_new_row_is_not_blue(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_2)], archive_ids=set())
        with patch("src.history_dialog.load_downloaded_video_ids", return_value=set()):
            dialog.prepend_row(_record(_YT_URL_1, result="FAIL"))
        assert not _is_row_blue(dialog, 0)

    def test_skipped_new_row_is_blue_because_already_in_archive(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_2, result="FAIL")], archive_ids=set())
        # SKIPPED means it was already in the archive file
        with patch("src.history_dialog.load_downloaded_video_ids", return_value={_VIDEO_ID_1}):
            dialog.prepend_row(_record(_YT_URL_1, result="SKIPPED (Already downloaded)"))
        assert _is_row_blue(dialog, 0)

    def test_success_prepend_turns_existing_matching_rows_blue(self) -> None:
        dialog = _make_dialog([_record(_YT_URL_1, result="FAIL")], archive_ids=set())
        with patch("src.history_dialog.load_downloaded_video_ids", return_value=set()):
            dialog.prepend_row(_record(_YT_URL_1, result="SUCCESS"))
        # row 0 = new success, row 1 = existing fail row — same video, both should be blue
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)


class TestDeleteFromArchiveMultiRow:
    """Tests that deleting from archive removes blue from all rows for that video."""

    def test_all_rows_lose_blue_on_delete(self, tmp_path: Path) -> None:
        archive_file = tmp_path / "archive.txt"
        archive_file.write_text(f"youtube {_VIDEO_ID_1}\n", encoding="utf-8")

        dialog = _make_dialog([_record(_YT_URL_1), _record(_YT_URL_1)], archive_ids={_VIDEO_ID_1})
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)

        with patch("src.history_dialog.ARCHIVE_PATH", archive_file):
            dialog._delete_from_archive(_VIDEO_ID_1)

        assert not _is_row_blue(dialog, 0)
        assert not _is_row_blue(dialog, 1)

    def test_other_video_rows_stay_blue_after_delete(self, tmp_path: Path) -> None:
        archive_file = tmp_path / "archive.txt"
        archive_file.write_text(f"youtube {_VIDEO_ID_1}\nyoutube {_VIDEO_ID_2}\n", encoding="utf-8")

        dialog = _make_dialog(
            [_record(_YT_URL_1), _record(_YT_URL_2)],
            archive_ids={_VIDEO_ID_1, _VIDEO_ID_2},
        )
        assert _is_row_blue(dialog, 0)
        assert _is_row_blue(dialog, 1)

        with patch("src.history_dialog.ARCHIVE_PATH", archive_file):
            dialog._delete_from_archive(_VIDEO_ID_1)

        assert not _is_row_blue(dialog, 0)  # video1 deleted
        assert _is_row_blue(dialog, 1)  # video2 untouched

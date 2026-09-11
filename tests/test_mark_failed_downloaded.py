"""
Tests for MyWindow._mark_failed_downloaded.

Binds the target method to a MagicMock rather than constructing a real
MyWindow, mirroring tests/test_failed_downloads_reopen.py -- constructing
MyWindow spins up timers and keyring access as an import/construction side
effect, which no test in this suite does.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.podcast_filtering import load_downloaded_video_ids


def _make_window() -> MagicMock:
    import meadowlark

    win = MagicMock()
    win._mark_failed_downloaded = meadowlark.MyWindow._mark_failed_downloaded.__get__(win)
    return win


def _record(**overrides: object) -> dict:
    record = {
        "key": "https://www.youtube.com/watch?v=abc123",
        "urls": ["https://www.youtube.com/watch?v=abc123"],
        "source": "1080",
        "site": "youtube",
        "title": "Test Video",
        "failed_at": "2025-01-01 12:00:00",
        "error": "Private video",
    }
    record.update(overrides)
    return record


def test_appends_video_id_to_archive_and_deletes_record(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    record = _record()

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded(record)

    assert "abc123" in load_downloaded_video_ids(str(archive_file))
    win._delete_failed_download.assert_called_once_with(record["key"])
    win.handle_log_entry.assert_called_once()


def test_does_not_duplicate_id_already_in_archive(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc123\n", encoding="utf-8")
    win = _make_window()
    record = _record()

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded(record)

    assert archive_file.read_text(encoding="utf-8").count("abc123") == 1
    win._delete_failed_download.assert_called_once_with(record["key"])


def test_no_op_when_video_id_not_extractable(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    record = _record(urls=["https://www.youtube.com/playlist?list=PLx"])

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded(record)

    assert not archive_file.exists()
    win._delete_failed_download.assert_not_called()
    win.handle_log_entry.assert_not_called()


def test_oserror_on_archive_write_logs_and_does_not_delete(tmp_path: Path) -> None:
    """
    An unwritable archive file must log and return, never deleting the record.

    Deleting the failed-download entry here would silently lose the user's
    only record of the failure while the id never actually made it into the
    archive -- a future playlist scan would neither retry it (deleted) nor
    skip it (not archived).
    """
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    record = _record()

    with (
        patch("meadowlark.ARCHIVE_PATH", archive_file),
        patch("meadowlark.utils.log_exception") as mock_log_exception,
        patch.object(type(archive_file), "open", side_effect=OSError("disk full")),
    ):
        win._mark_failed_downloaded(record)

    mock_log_exception.assert_called_once()
    win._delete_failed_download.assert_not_called()
    win.handle_log_entry.assert_not_called()

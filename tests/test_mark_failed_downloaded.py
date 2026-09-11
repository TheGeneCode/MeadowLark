"""
Tests for MyWindow's failed-download batch handlers.

Binds the target methods to a MagicMock rather than constructing a real
MyWindow, mirroring tests/test_failed_downloads_reopen.py -- constructing
MyWindow spins up timers and keyring access as an import/construction side
effect, which no test in this suite does.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.podcast_filtering import load_downloaded_video_ids


def _make_window(*bind: str) -> MagicMock:
    import meadowlark

    if not bind:
        bind = ("_mark_failed_downloaded",)
    win = MagicMock()
    for name in bind:
        setattr(win, name, getattr(meadowlark.MyWindow, name).__get__(win))
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
        win._mark_failed_downloaded([record])

    assert "abc123" in load_downloaded_video_ids(str(archive_file))
    win._delete_failed_downloads.assert_called_once_with([record["key"]])
    win.handle_log_entry.assert_called_once()


def test_does_not_duplicate_id_already_in_archive(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc123\n", encoding="utf-8")
    win = _make_window()
    record = _record()

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded([record])

    assert archive_file.read_text(encoding="utf-8").count("abc123") == 1
    win._delete_failed_downloads.assert_called_once_with([record["key"]])


def test_no_op_when_video_id_not_extractable(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    record = _record(urls=["https://www.youtube.com/playlist?list=PLx"])

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded([record])

    assert not archive_file.exists()
    win._delete_failed_downloads.assert_not_called()
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
        win._mark_failed_downloaded([record])

    mock_log_exception.assert_called_once()
    win._delete_failed_downloads.assert_not_called()
    win.handle_log_entry.assert_not_called()


def test_mark_batch_appends_each_new_id_once(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    record_1 = _record(key="k1", urls=["https://www.youtube.com/watch?v=abc123"])
    record_2 = _record(key="k2", urls=["https://www.youtube.com/watch?v=def456"])
    record_3 = _record(key="k3", urls=["https://youtu.be/abc123"])

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded([record_1, record_2, record_3])

    ids = load_downloaded_video_ids(str(archive_file))
    assert "abc123" in ids
    assert "def456" in ids
    assert archive_file.read_text(encoding="utf-8").count("abc123") == 1
    assert archive_file.read_text(encoding="utf-8").count("def456") == 1
    win._delete_failed_downloads.assert_called_once_with(["k1", "k2", "k3"])
    assert win.handle_log_entry.call_count == 3


def test_mark_batch_skips_ineligible_records(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    win = _make_window()
    playlist_record = _record(
        key="playlist-k", urls=["https://www.youtube.com/playlist?list=PLx"]
    )
    watch_record = _record(key="watch-k", urls=["https://www.youtube.com/watch?v=abc123"])

    with patch("meadowlark.ARCHIVE_PATH", archive_file):
        win._mark_failed_downloaded([playlist_record, watch_record])

    win._delete_failed_downloads.assert_called_once_with(["watch-k"])


def test_retry_batch_deletes_once_then_requeues_each() -> None:
    win = _make_window("_retry_failed_downloads")
    record_a = _record(key="a", urls=["https://www.youtube.com/watch?v=a1"], source="1080")
    record_b = _record(key="b", urls=["https://www.youtube.com/watch?v=b1"], source="audio")

    win._retry_failed_downloads([record_a, record_b])

    delete_call_index = next(
        i for i, call in enumerate(win.mock_calls) if call[0] == "_delete_failed_downloads"
    )
    request_call_indices = [
        i for i, call in enumerate(win.mock_calls) if call[0] == "request_detected"
    ]
    win._delete_failed_downloads.assert_called_once_with(["a", "b"])
    assert all(delete_call_index < i for i in request_call_indices)
    assert win.request_detected.call_args_list == [
        ((list(record_a["urls"]), record_a["source"]),),
        ((list(record_b["urls"]), record_b["source"]),),
    ]


def test_retry_never_sends_empty_urls() -> None:
    win = _make_window("_retry_failed_downloads")
    records = [
        _record(key="empty-urls", urls=[]),
        _record(key="str-urls", urls="https://www.youtube.com/watch?v=abc"),  # type: ignore[arg-type]
        _record(key="no-source", source=None),  # type: ignore[arg-type]
    ]

    win._retry_failed_downloads(records)

    win.request_detected.assert_not_called()
    win._delete_failed_downloads.assert_not_called()


def test_delete_batch_one_write_one_refresh(tmp_path: Path) -> None:
    from src.failed_downloads import add_failed_download

    store = tmp_path / "fd.json"
    add_failed_download(store, _record(key="k1"))
    add_failed_download(store, _record(key="k2"))
    add_failed_download(store, _record(key="k3"))

    win = _make_window("_delete_failed_downloads")

    with patch("meadowlark.FAILED_DOWNLOADS_FILE", store):
        win._delete_failed_downloads(["k1", "k2"])

    from src.failed_downloads import load_failed_downloads

    remaining = load_failed_downloads(store)
    assert [r["key"] for r in remaining] == ["k3"]
    win._refresh_failed_button.assert_called_once()
    win._failed_dialog.set_records.assert_called_once_with(remaining)

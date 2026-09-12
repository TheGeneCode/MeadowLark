"""
Unit tests for MyWindow._on_download_failed / _park_not_yet_released (meadowlark.pyw).

These two methods had zero existing test coverage despite being flagged as the
highest-risk area of the premiere/not-yet-released Phase 2 work: a failed-download
signal handler that (a) has to correctly distinguish "announced but unaired" from a
genuine failure, and (b) synchronously re-enters the shared pending-queue poll loop
(``check_pending_queue``) from inside a GUI-thread slot. A regression here either
misfiles a real failure as "parked forever" or loses a not-yet-released item to the
failed-downloads store.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.pending_queue import KIND_PREMIERE, load_pending_queue
from tests._vd_loader import import_vid_module


def _make_win(
    vd,
    pending_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_downloads_path: Path | None = None,
):
    """Build a minimal MyWindow stand-in wired to the real bound methods under test."""

    class DummyWin:
        pending_queue_path = pending_path
        logEdit = SimpleNamespace(appendPlainText=lambda _msg: None)
        barProgress = SimpleNamespace(setRange=lambda _a, _b: None)
        buttonFailed = SimpleNamespace(setText=lambda _t: None, setVisible=lambda _v: None)
        buttonPending = SimpleNamespace(setText=lambda _t: None, setVisible=lambda _v: None)
        _failed_dialog = None
        _pending_dialog = None

        _pending_deps = vd.MyWindow._pending_deps
        check_pending_queue = vd.MyWindow.check_pending_queue
        _park_not_yet_released = vd.MyWindow._park_not_yet_released
        _on_download_failed = vd.MyWindow._on_download_failed
        _refresh_failed_button = vd.MyWindow._refresh_failed_button
        _refresh_pending_button = vd.MyWindow._refresh_pending_button

        def handle_log_entry(self, msg: str) -> None:
            self.logs.append(msg)

        def get_options(self, urls, source, skip_playlist_dialog=False):
            return {"format": "x"}

        def append_properties(self, ydl_opts, properties):
            ydl_opts.update(properties)
            return ydl_opts

        def _create_download_context(self):
            return (
                SimpleNamespace(info_changed=None),
                SimpleNamespace(message_changed=None),
                {},
            )

        def _wire_download_signals(self, _qhook, _qlogger) -> None:
            pass

    win = DummyWin()
    win.logs = []
    win._queued = []
    win.downloadQueue = SimpleNamespace(put=win._queued.append)
    if failed_downloads_path is not None:
        monkeypatch.setattr(vd, "FAILED_DOWNLOADS_FILE", failed_downloads_path)
    return win


def _not_yet_released_failed_record(url: str = "https://youtube.com/watch?v=x", **overrides):
    record = {
        "urls": [url],
        "key": url,
        "source": "1080playlists",
        "site": "youtube",
        "title": "Some Premiere",
        "error": "ERROR: [youtube] x: Premieres in 6 hours",
    }
    record.update(overrides)
    return record


def test_park_not_yet_released_creates_premiere_record_with_parsed_release_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    win = _make_win(vd, path, monkeypatch)

    with patch.object(vd.yt_dlp, "YoutubeDL") as mock_ydl:
        ydl_instance = mock_ydl.return_value.__enter__.return_value
        ydl_instance.extract_info.return_value = {"live_status": "is_upcoming"}
        win._park_not_yet_released(_not_yet_released_failed_record())

    records = load_pending_queue(path)
    assert len(records) == 1
    assert records[0]["kind"] == KIND_PREMIERE
    assert records[0]["release_at"] is not None
    assert any("parked" in msg for msg in win.logs)


def test_park_not_yet_released_no_url_and_no_key_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed failure record (no urls, no key) must not crash or write anything."""
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    win = _make_win(vd, path, monkeypatch)
    win.check_pending_queue = Mock()

    win._park_not_yet_released({"urls": [], "error": "Premieres in 6 hours"})

    assert load_pending_queue(path) == []
    assert win.logs == []
    win.check_pending_queue.assert_not_called()


def test_park_not_yet_released_called_twice_for_same_url_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Simulate a second failure signal for the same premiere.

    This could arrive before the QTimer-driven poll clears it (e.g. a retry);
    upsert-by-url must merge, not duplicate the record.
    """
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    win = _make_win(vd, path, monkeypatch)

    with patch.object(vd.yt_dlp, "YoutubeDL") as mock_ydl:
        ydl_instance = mock_ydl.return_value.__enter__.return_value
        ydl_instance.extract_info.return_value = {"live_status": "is_upcoming"}
        record = _not_yet_released_failed_record()
        win._park_not_yet_released(record)
        win._park_not_yet_released(record)

    records = load_pending_queue(path)
    assert len(records) == 1


def test_park_not_yet_released_immediately_enqueues_when_already_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Confirm the synchronous check_pending_queue() call can enqueue right away.

    The exact-release-time probe run immediately after parking may show the
    item already aired -- it must be enqueued straight away, not just left
    parked for the next QTimer tick.
    """
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    win = _make_win(vd, path, monkeypatch)

    with patch.object(vd.yt_dlp, "YoutubeDL") as mock_ydl:
        ydl_instance = mock_ydl.return_value.__enter__.return_value
        ydl_instance.extract_info.return_value = {"live_status": "was_live"}
        win._park_not_yet_released(_not_yet_released_failed_record())

    assert load_pending_queue(path) == []
    assert len(win._queued) == 1


def test_park_not_yet_released_upsert_oserror_does_not_crash_and_skips_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError from upsert_pending must be swallowed; the follow-up check must not run."""
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    win = _make_win(vd, path, monkeypatch)
    win.check_pending_queue = Mock()

    with patch.object(vd, "upsert_pending", side_effect=OSError("disk full")):
        win._park_not_yet_released(_not_yet_released_failed_record())

    assert win.logs == []
    win.check_pending_queue.assert_not_called()


def test_on_download_failed_dispatches_not_yet_released_to_park_not_failed_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The not-yet-released branch must win before anything touches the failed-downloads store."""
    vd = import_vid_module()
    pending_path = tmp_path / "pending_queue.json"
    failed_path = tmp_path / "failed_downloads.json"
    win = _make_win(vd, pending_path, monkeypatch, failed_downloads_path=failed_path)

    with patch.object(vd.yt_dlp, "YoutubeDL") as mock_ydl:
        ydl_instance = mock_ydl.return_value.__enter__.return_value
        ydl_instance.extract_info.return_value = {"live_status": "is_upcoming"}
        win._on_download_failed(_not_yet_released_failed_record())

    assert len(load_pending_queue(pending_path)) == 1
    assert not failed_path.exists()


def test_on_download_failed_genuine_error_goes_to_failed_store_not_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real failure (not a not-yet-released marker) must not be parked."""
    vd = import_vid_module()
    pending_path = tmp_path / "pending_queue.json"
    failed_path = tmp_path / "failed_downloads.json"
    win = _make_win(vd, pending_path, monkeypatch, failed_downloads_path=failed_path)

    record = _not_yet_released_failed_record(
        error="ERROR: unable to download video data: HTTP Error 403"
    )
    win._on_download_failed(record)

    assert load_pending_queue(pending_path) == []
    assert failed_path.exists()


def test_window_pending_deps_ydl_class_is_explicitly_wired_not_frozen_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Mirror the same regression guard added for DownloadService._pending_deps.

    MyWindow._pending_deps must pass ydl_class explicitly rather than relying
    on PendingCheckDeps' import-time-frozen default.
    """
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json", monkeypatch)

    sentinel_class = type("SentinelYDL", (), {})
    with patch.object(vd.yt_dlp, "YoutubeDL", sentinel_class):
        deps = win._pending_deps()

    assert deps.ydl_class is sentinel_class

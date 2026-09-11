"""
Unit tests for MyWindow's pending-downloads glue in meadowlark.pyw.

Covers _refresh_pending_button, _show_pending_downloads, _on_pending_dialog_destroyed,
_remove_pending_downloads, _download_pending_now.

These methods had zero existing test coverage (tests/test_pending_downloads_dialog.py
only exercises PendingDownloadsDialog itself, which is deliberately a dumb view; all
store mutation and dialog lifecycle management lives in MyWindow). Highest-risk areas
per the implementer's own handoff notes: button-enablement edge cases already covered
in the dialog-level suite, but the remove-then-redownload reentrancy chain and the
cached-dialog-instance RuntimeError recovery path are not.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.pending_queue import save_pending_queue
from tests.test_cache_early_exit import import_vid_module


class _FakeSignal:
    """Stand-in for a pyqtSignal instance -- just needs .connect() to be a no-op sink."""

    def connect(self, *_args, **_kwargs) -> None:
        return None


class _FakeDialog:
    """Stand-in for PendingDownloadsDialog that never touches real Qt widgets."""

    def __init__(self, records: list, parent=None) -> None:
        self.records = records
        self.parent = parent
        self.destroyed = _FakeSignal()
        self.download_now_requested = _FakeSignal()
        self.remove_requested = _FakeSignal()
        self.shown = False
        self.set_records_calls: list = []

    def show(self) -> None:
        self.shown = True

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:
        pass

    def set_records(self, records: list) -> None:
        self.set_records_calls.append(records)


class _RaisingDialog:
    """Simulates a cached dialog whose underlying C++ object has been deleted by Qt."""

    def show(self) -> None:
        raise RuntimeError("wrapped C/C++ object of type PendingDownloadsDialog has been deleted")


def _make_win(vd, pending_path: Path):
    class DummyWin:
        pending_queue_path = pending_path
        buttonPending = SimpleNamespace(setText=lambda _t: None, setVisible=lambda _v: None)
        _pending_dialog = None

        _refresh_pending_button = vd.MyWindow._refresh_pending_button
        _show_pending_downloads = vd.MyWindow._show_pending_downloads
        _on_pending_dialog_destroyed = vd.MyWindow._on_pending_dialog_destroyed
        _remove_pending_downloads = vd.MyWindow._remove_pending_downloads
        _download_pending_now = vd.MyWindow._download_pending_now

        def handle_log_entry(self, msg: str) -> None:
            self.logs.append(msg)

        def request_detected(self, urls: list, source: str) -> None:
            self.requested.append((urls, source))

    win = DummyWin()
    win.logs = []
    win.requested = []
    return win


def _pending_record(url: str = "https://example.com/video", **overrides) -> dict:
    record = {
        "url": url,
        "source": "1080",
        "playlist_id": None,
        "label": None,
        "kind": "live",
        "title": "Some Stream",
        "release_at": None,
        "first_seen": None,
        "last_checked": None,
        "last_error": None,
    }
    record.update(overrides)
    return record


# --- _refresh_pending_button ------------------------------------------------


def test_refresh_pending_button_with_explicit_records_updates_text_and_visibility(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    texts: list[str] = []
    visibilities: list[bool] = []
    win.buttonPending = SimpleNamespace(
        setText=texts.append, setVisible=visibilities.append
    )

    win._refresh_pending_button([_pending_record(), _pending_record(url="u2")])

    assert texts[-1] == "⏳ 2"
    assert visibilities[-1] is True


def test_refresh_pending_button_empty_list_hides_button(tmp_path: Path) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    visibilities: list[bool] = []
    win.buttonPending = SimpleNamespace(setText=lambda _t: None, setVisible=visibilities.append)

    win._refresh_pending_button([])

    assert visibilities[-1] is False


def test_refresh_pending_button_none_loads_from_disk(tmp_path: Path) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record()])
    win = _make_win(vd, path)
    texts: list[str] = []
    win.buttonPending = SimpleNamespace(setText=texts.append, setVisible=lambda _v: None)

    win._refresh_pending_button(None)

    assert texts[-1] == "⏳ 1"


def test_refresh_pending_button_forwards_to_open_dialog(tmp_path: Path) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    dialog = _FakeDialog([])
    win._pending_dialog = dialog

    records = [_pending_record()]
    win._refresh_pending_button(records)

    assert dialog.set_records_calls == [records]


def test_refresh_pending_button_no_dialog_does_not_raise(tmp_path: Path) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    assert win._pending_dialog is None

    win._refresh_pending_button([])  # must not raise on None._pending_dialog


# --- _remove_pending_downloads -----------------------------------------------


def test_remove_pending_downloads_removes_from_store_and_refreshes_button(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record(url="keep"), _pending_record(url="drop")])
    win = _make_win(vd, path)
    texts: list[str] = []
    win.buttonPending = SimpleNamespace(setText=texts.append, setVisible=lambda _v: None)

    win._remove_pending_downloads(["drop"])

    from src.pending_queue import load_pending_queue

    remaining = load_pending_queue(path)
    assert [r["url"] for r in remaining] == ["keep"]
    assert texts[-1] == "⏳ 1"


def test_remove_pending_downloads_removes_every_given_url_in_one_write(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(
        path,
        [_pending_record(url="keep"), _pending_record(url="a"), _pending_record(url="b")],
    )
    win = _make_win(vd, path)

    win._remove_pending_downloads(["a", "b"])

    from src.pending_queue import load_pending_queue

    assert [r["url"] for r in load_pending_queue(path)] == ["keep"]


def test_remove_pending_downloads_missing_url_is_noop(tmp_path: Path) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record(url="keep")])
    win = _make_win(vd, path)

    win._remove_pending_downloads(["does-not-exist"])

    from src.pending_queue import load_pending_queue

    assert [r["url"] for r in load_pending_queue(path)] == ["keep"]


# --- _download_pending_now ---------------------------------------------------


def test_download_pending_now_with_no_url_is_noop(tmp_path: Path) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record(url="untouched")])
    win = _make_win(vd, path)

    win._download_pending_now([{"url": None, "title": "no url"}])

    assert win.requested == []
    assert win.logs == []
    from src.pending_queue import load_pending_queue

    assert [r["url"] for r in load_pending_queue(path)] == ["untouched"]


def test_download_pending_now_missing_url_key_is_noop(tmp_path: Path) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")

    win._download_pending_now([{"title": "no url key at all"}])

    assert win.requested == []
    assert win.logs == []


def test_download_pending_now_removes_then_requests_with_record_source(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    record = _pending_record(url="https://example.com/vid", source="1080playlists")
    save_pending_queue(path, [record])
    win = _make_win(vd, path)

    win._download_pending_now([record])

    from src.pending_queue import load_pending_queue

    assert load_pending_queue(path) == []  # removed before the request
    assert win.requested == [(["https://example.com/vid"], "1080playlists")]
    assert any("Downloading pending item now" in msg for msg in win.logs)


def test_download_pending_now_falls_back_to_1080_when_source_missing(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    record = _pending_record(url="https://example.com/vid")
    del record["source"]
    save_pending_queue(path, [{**record, "source": "1080"}])
    win = _make_win(vd, path)

    win._download_pending_now([record])

    assert win.requested == [(["https://example.com/vid"], "1080")]


def test_download_pending_now_processes_every_runnable_record(tmp_path: Path) -> None:
    """The multi-select regression: every actionable record must be requested, not just one."""
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    record_a = _pending_record(url="https://example.com/a", source="1080")
    record_b = _pending_record(url="https://example.com/b", source="720")
    save_pending_queue(path, [record_a, record_b])
    win = _make_win(vd, path)

    win._download_pending_now([record_a, record_b, {"title": "no url key"}])

    from src.pending_queue import load_pending_queue

    assert load_pending_queue(path) == []
    assert win.requested == [
        (["https://example.com/a"], "1080"),
        (["https://example.com/b"], "720"),
    ]


def test_download_pending_now_reentrant_repark_leaves_consistent_store(
    tmp_path: Path,
) -> None:
    """
    Simulate the documented reentrancy chain.

    request_detected's failure path calls back into _park_not_yet_released ->
    upsert_pending, re-adding the same URL with a fresh release time. Since removal
    already committed to disk before request_detected runs, the re-park must see a
    consistent (empty) store rather than losing or duplicating the write.
    """
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    record = _pending_record(url="https://example.com/vid", source="1080")
    save_pending_queue(path, [record])
    win = _make_win(vd, path)

    from src.pending_queue import load_pending_queue, upsert_pending

    def fake_request_detected(urls, source):
        # Mirror _park_not_yet_released re-parking the still-unreleased item.
        upsert_pending(path, {**record, "release_at": "2099-01-01T00:00:00+00:00"})

    win.request_detected = fake_request_detected

    win._download_pending_now([record])

    remaining = load_pending_queue(path)
    assert len(remaining) == 1
    assert remaining[0]["url"] == "https://example.com/vid"
    assert remaining[0]["release_at"] == "2099-01-01T00:00:00+00:00"


# --- _on_pending_dialog_destroyed --------------------------------------------


def test_on_pending_dialog_destroyed_clears_reference(tmp_path: Path) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    win._pending_dialog = _FakeDialog([])

    win._on_pending_dialog_destroyed()

    assert win._pending_dialog is None


# --- _show_pending_downloads --------------------------------------------------


def test_show_pending_downloads_creates_dialog_when_none_cached(tmp_path: Path) -> None:
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record()])
    win = _make_win(vd, path)

    with patch.object(vd, "PendingDownloadsDialog", _FakeDialog):
        win._show_pending_downloads()

    assert isinstance(win._pending_dialog, _FakeDialog)
    assert win._pending_dialog.shown is True
    assert len(win._pending_dialog.records) == 1


def test_show_pending_downloads_refocuses_existing_live_dialog_without_replacing_it(
    tmp_path: Path,
) -> None:
    vd = import_vid_module()
    win = _make_win(vd, tmp_path / "pending_queue.json")
    existing = _FakeDialog([])
    win._pending_dialog = existing

    with patch.object(vd, "PendingDownloadsDialog", Mock(side_effect=AssertionError(
        "should not construct a new dialog while one is cached and alive"
    ))):
        win._show_pending_downloads()

    assert win._pending_dialog is existing
    assert existing.shown is True


def test_show_pending_downloads_recovers_when_cached_dialog_was_deleted_by_qt(
    tmp_path: Path,
) -> None:
    """
    Recover when the cached dialog was deleted by Qt.

    The cached instance can be a dangling reference to a Qt object already destroyed
    at the C++ level (RuntimeError on any method call); recovery must fall through to
    building a fresh dialog rather than propagating the exception.
    """
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(path, [_pending_record()])
    win = _make_win(vd, path)
    win._pending_dialog = _RaisingDialog()

    with patch.object(vd, "PendingDownloadsDialog", _FakeDialog), patch.object(
        vd.utils, "log_exception"
    ) as mock_log:
        win._show_pending_downloads()

    assert isinstance(win._pending_dialog, _FakeDialog)
    assert win._pending_dialog.shown is True
    mock_log.assert_called_once()

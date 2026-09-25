"""Tests for MyWindow._retry_failed_downloads routing (single playlist entry vs batch)."""

from unittest.mock import Mock

import pytest

from tests._vd_loader import import_vid_module

PL = "PL" + "a" * 32
WATCH = "https://www.youtube.com/watch?v=RUh8D2Hau2o"


def _make_win(vd):
    class DummyWin:
        _retry_failed_downloads = vd.MyWindow._retry_failed_downloads
        _redownload = vd.MyWindow._redownload

        def _delete_failed_downloads(self, keys: list) -> None:
            self.deleted.extend(keys)

        def handle_log_entry(self, msg: str) -> None:
            self.logs.append(msg)

        def request_detected(self, urls: list, source: str) -> None:
            self.requested.append((urls, source))

        def _pending_deps(self) -> str:
            return "deps"

    win = DummyWin()
    win.deleted = []
    win.logs = []
    win.requested = []
    return win


def _record(**overrides: object) -> dict:
    record = {"key": WATCH, "urls": [WATCH], "source": "720playlists"}
    record.update(overrides)
    return record


@pytest.fixture
def vd():
    return import_vid_module()


@pytest.fixture
def enqueue(vd, monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(vd, "enqueue_entry", mock)
    return mock


def test_retry_single_playlist_entry_uses_enqueue_entry(vd, enqueue: Mock) -> None:
    win = _make_win(vd)

    win._retry_failed_downloads([_record(playlist_id=PL)])

    enqueue.assert_called_once_with(
        "deps", WATCH, "720playlists", playlist_id=PL, recheck_live=True
    )
    assert win.deleted == [WATCH]
    assert win.requested == []


def test_retry_without_playlist_id_uses_request_detected(vd, enqueue: Mock) -> None:
    win = _make_win(vd)

    win._retry_failed_downloads([_record()])

    assert win.requested == [([WATCH], "720playlists")]
    enqueue.assert_not_called()


def test_retry_multi_url_record_uses_request_detected(vd, enqueue: Mock) -> None:
    win = _make_win(vd)
    urls = [WATCH, "https://www.youtube.com/watch?v=b"]

    win._retry_failed_downloads([_record(urls=urls, playlist_id=PL)])

    assert win.requested == [(urls, "720playlists")]
    enqueue.assert_not_called()

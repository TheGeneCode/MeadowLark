"""
Podcast Status date invariant.

Every Podcast Status row built from a fetched or cached episode carries that
episode's date, whichever branch classified it.
"""

import time
from datetime import UTC, datetime

import pytest

from tests.test_cache_early_exit import _make_dummy_win, import_vid_module

LATEST_TS = 1700000000  # 2023-11-14 22:13:20 UTC
LATEST_DATE = "2023-11-14"
URL = "https://www.youtube.com/playlist?list=PLlatestdate"


@pytest.fixture(scope="module")
def vd():
    return import_vid_module()


def _entry(**overrides) -> dict:
    base = {
        "id": "vid_latest",
        "webpage_url": "https://www.youtube.com/watch?v=vid_latest",
        "title": "Episode 42",
        "duration": 3600,
        "timestamp": LATEST_TS,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def _run(vd, monkeypatch, tmp_path, entry, *, archived_ids=(), cache=None) -> dict:
    """Run the live filter for URL against a one-entry playlist; return its status row."""
    monkeypatch.setattr("QYT.HistoryLogger.HISTORY_PATH", tmp_path / "history_log.txt")
    monkeypatch.setattr("utils.load_playlist_comments_for_source", lambda _source: {})
    archive = tmp_path / "archive.txt"
    archive.write_text("".join(f"youtube {v}\n" for v in archived_ids), encoding="utf-8")
    monkeypatch.setattr(vd, "fetch_latest_accessible_entry", lambda _url: ([entry], False, {}))
    win = _make_dummy_win(vd, cache=cache)
    _, _, _, _, statuses = vd.MyWindow._filter_audio_playlist_urls(
        win, [URL], {"download_archive": str(archive)}
    )
    assert len(statuses) == 1
    return statuses[0]


def test_downloaded_row_carries_latest_date(vd, monkeypatch, tmp_path):
    status = _run(vd, monkeypatch, tmp_path, _entry(), archived_ids=("vid_latest",))
    assert status["status"] == "Downloaded"
    assert status["latest_date"] == LATEST_DATE
    assert status["latest_ts"] == LATEST_TS


def test_skipped_update_row_carries_latest_date(vd, monkeypatch, tmp_path):
    status = _run(vd, monkeypatch, tmp_path, _entry(title="Episode 42 (Update)"))
    assert status["status"] == "Skipped (Update)"
    assert status["latest_date"] == LATEST_DATE


def test_skipped_short_row_carries_latest_date(vd, monkeypatch, tmp_path):
    status = _run(vd, monkeypatch, tmp_path, _entry(duration=30))
    assert status["status"] == "Skipped Short"
    assert status["latest_date"] == LATEST_DATE


def test_ready_row_still_carries_latest_date(vd, monkeypatch, tmp_path):
    status = _run(vd, monkeypatch, tmp_path, _entry())
    assert status["status"] == "Ready"
    assert status["latest_date"] == LATEST_DATE


def test_upcoming_row_carries_future_date(vd, monkeypatch, tmp_path):
    future_ts = time.time() + 3 * 86400
    status = _run(vd, monkeypatch, tmp_path, _entry(timestamp=future_ts))
    assert status["status"] == "Upcoming"
    expected_date = datetime.fromtimestamp(future_ts, tz=UTC).strftime("%Y-%m-%d")
    assert status["latest_date"] == expected_date
    assert "recheck_ts" in status


def test_upload_date_only_entry_is_dated(vd, monkeypatch, tmp_path):
    status = _run(
        vd,
        monkeypatch,
        tmp_path,
        _entry(timestamp=None, upload_date="20260910"),
        archived_ids=("vid_latest",),
    )
    assert status["status"] == "Downloaded"
    assert status["latest_date"] == "2026-09-10"


def test_undated_entry_reads_unknown(vd, monkeypatch, tmp_path):
    status = _run(
        vd,
        monkeypatch,
        tmp_path,
        _entry(timestamp=None),
        archived_ids=("vid_latest",),
    )
    assert status["status"] == "Downloaded"
    assert status["latest_date"] == "(unknown)"


def test_cache_early_exit_row_carries_latest_date(vd, monkeypatch, tmp_path):
    def _no_network(_url):
        raise AssertionError("network")

    monkeypatch.setattr(vd, "fetch_latest_accessible_entry", _no_network)
    cache = {
        URL: {
            "latest_url": "https://www.youtube.com/watch?v=vid_latest",
            "latest_ts": LATEST_TS,
            "fetched_at": time.time(),
            "video_id": "vid_latest",
        }
    }
    monkeypatch.setattr("QYT.HistoryLogger.HISTORY_PATH", tmp_path / "history_log.txt")
    monkeypatch.setattr("utils.load_playlist_comments_for_source", lambda _source: {})
    archive = tmp_path / "archive.txt"
    archive.write_text("youtube vid_latest\n", encoding="utf-8")
    win = _make_dummy_win(vd, cache=cache)
    _, _, _, _, statuses = vd.MyWindow._filter_audio_playlist_urls(
        win, [URL], {"download_archive": str(archive)}
    )
    assert len(statuses) == 1
    status = statuses[0]
    assert status["status"] == "Downloaded"
    assert status["latest_date"] == LATEST_DATE


def test_classify_ts_none_ready_does_not_overwrite_callers_unknown_date(vd):
    """
    Ready-with-no-date branch.

    Caller already wrote "(unknown)" before dispatch; the classifier (which no
    longer writes latest_date itself) must leave it alone.
    """
    status_entry = {"latest_date": "(unknown)", "latest_ts": None}
    to_download: list = []
    pending: list = []
    vd.MyWindow._classify_episode_by_age(
        None,
        "vid1",
        "https://example.com/watch?v=vid1",
        None,
        time.time(),
        "My Podcast",
        to_download,
        pending,
        status_entry,
    )
    assert status_entry["status"] == "Ready"
    assert status_entry["latest_date"] == "(unknown)"
    assert to_download == [
        {"url": "https://example.com/watch?v=vid1", "playlist": "My Podcast"}
    ]


def test_cache_early_exit_row_without_ts_reads_unknown(vd, monkeypatch, tmp_path):
    def _no_network(_url):
        raise AssertionError("network")

    monkeypatch.setattr(vd, "fetch_latest_accessible_entry", _no_network)
    cache = {
        URL: {
            "latest_url": "https://www.youtube.com/watch?v=vid_latest",
            "latest_ts": None,
            "fetched_at": time.time(),
            "video_id": "vid_latest",
        }
    }
    monkeypatch.setattr("QYT.HistoryLogger.HISTORY_PATH", tmp_path / "history_log.txt")
    monkeypatch.setattr("utils.load_playlist_comments_for_source", lambda _source: {})
    archive = tmp_path / "archive.txt"
    archive.write_text("youtube vid_latest\n", encoding="utf-8")
    win = _make_dummy_win(vd, cache=cache)
    _, _, _, _, statuses = vd.MyWindow._filter_audio_playlist_urls(
        win, [URL], {"download_archive": str(archive)}
    )
    assert len(statuses) == 1
    assert statuses[0]["latest_date"] == "(unknown)"

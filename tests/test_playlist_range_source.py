"""
Invariant: a playlist URL dropped on a bare-rung target downloads with playlist semantics.

When ``_handle_playlist_dialog`` accepts a range for a URL carrying ``list=``, the
run is a playlist run, so ``get_options`` must resolve the *playlist* source
options -- ``ignoreerrors="only_download"`` (one unavailable entry must not abort
the remaining entries) and the ``%(playlist)s/%(playlist_index)s`` output
template.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Self

import meadowlark
import pytest

from src.ydl_options import build_shared_extraction_opts


class _AcceptingDialog:
    """Stand-in for PlaylistDialog: always accepted, always returns the same range."""

    returned_input = "134-165"

    def __init__(self, playlist_count: int) -> None:
        self.playlist_count = playlist_count

    def exec(self) -> bool:
        return True

    def get_playlist_input(self) -> str:
        return self.returned_input


class _CancellingDialog(_AcceptingDialog):
    """Stand-in for PlaylistDialog when the user presses Cancel."""

    def exec(self) -> bool:
        return False


class _FlatExtractor:
    """Stand-in for the extract_flat YoutubeDL used to count playlist entries."""

    seen_opts: list[dict] = []

    def __init__(self, opts: dict) -> None:
        self.opts = opts
        type(self).seen_opts.append(opts)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def extract_info(self, _url: str, download: bool = False) -> dict:
        return {"playlist_count": 200}


class _StubWindow:
    """Minimal host for the real get_options/_handle_playlist_dialog methods."""

    def __init__(self) -> None:
        self.checkSkipDownload = SimpleNamespace(isChecked=lambda: False)
        self.checkIgnoreArchive = SimpleNamespace(isChecked=lambda: False)
        self.logEdit = SimpleNamespace(appendPlainText=lambda _msg: None)

    def make_match_filter(self, source: str, label: str | None = None) -> object:
        return object()

    get_options = meadowlark.MyWindow.get_options
    skip_downloading = meadowlark.MyWindow.skip_downloading
    _handle_playlist_dialog = meadowlark.MyWindow._handle_playlist_dialog


_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLURDJQcufUYbVKtzNBG_A9mWWPO92J_3A"


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> _StubWindow:
    """Return a stub window whose playlist dialog is accepted without Qt or network work."""
    monkeypatch.setattr(meadowlark, "PlaylistDialog", _AcceptingDialog)
    monkeypatch.setattr(meadowlark.yt_dlp, "YoutubeDL", _FlatExtractor)
    return _StubWindow()


def test_dropped_playlist_range_uses_playlist_ignoreerrors(window: _StubWindow) -> None:
    """One unavailable entry must not abort the rest of a dropped playlist range."""
    options = window.get_options([_PLAYLIST_URL], "1080")
    assert options is not None
    assert options["playlist_items"] == "134-165"
    assert options["ignoreerrors"] == "only_download"


def test_dropped_playlist_range_uses_playlist_output_template(
    window: _StubWindow,
) -> None:
    """A playlist run writes into a per-playlist folder, numbered by playlist index."""
    options = window.get_options([_PLAYLIST_URL], "1080")
    assert options is not None
    assert "%(playlist)s" in options["outtmpl"]
    assert "%(playlist_index)s" in options["outtmpl"]


def test_dropped_playlist_range_on_audio_keeps_bare_audio_source(
    window: _StubWindow,
) -> None:
    """'audio' has no rung to promote; it must keep its own (misc podcast) options."""
    options = window.get_options([_PLAYLIST_URL], "audio")
    assert options is not None
    assert options["outtmpl"] == meadowlark.utils.get_source_options("audio")["outtmpl"]


def test_cancelled_playlist_dialog_still_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the range dialog cancels the download."""
    monkeypatch.setattr(meadowlark, "PlaylistDialog", _CancellingDialog)
    monkeypatch.setattr(meadowlark.yt_dlp, "YoutubeDL", _FlatExtractor)
    assert _StubWindow().get_options([_PLAYLIST_URL], "1080") is None


def test_non_playlist_url_keeps_bare_rung_source(window: _StubWindow) -> None:
    """A single-video drop never gets playlist semantics."""
    options = window.get_options(["https://www.youtube.com/watch?v=abcdefghijk"], "1080")
    assert options is not None
    assert "ignoreerrors" not in options
    assert "%(playlist)s" not in options["outtmpl"]


def test_playlist_url_on_an_already_playlist_source_skips_the_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target already carrying the playlist rung must not reopen the range dialog."""

    class _ExplodingDialog:
        def __init__(self, _playlist_count: int) -> None:
            raise AssertionError("dialog must not be constructed")

    class _ExplodingExtractor:
        def __init__(self, _opts: dict) -> None:
            raise AssertionError("extract_flat lookup must not run")

    monkeypatch.setattr(meadowlark, "PlaylistDialog", _ExplodingDialog)
    monkeypatch.setattr(meadowlark.yt_dlp, "YoutubeDL", _ExplodingExtractor)

    options = _StubWindow().get_options([_PLAYLIST_URL], "1080playlists")

    assert options is not None
    assert options["ignoreerrors"] == "only_download"
    assert "playlist_items" not in options


def test_playlist_count_lookup_carries_the_shared_extraction_wiring(
    window: _StubWindow,
) -> None:
    """
    The entry-count lookup is a real extraction and must not be a bare YoutubeDL.

    Without the shared options the bgutil PO-token provider falls back to its
    default server_home and pays a cold-cache Deno probe against yt-dlp's hard
    15s budget; without cookies a private or unlisted playlist reports no count
    at all. Both would surface as an exception before the range dialog appears.
    """
    _FlatExtractor.seen_opts.clear()
    window.get_options([_PLAYLIST_URL], "1080")

    assert len(_FlatExtractor.seen_opts) == 1
    opts = _FlatExtractor.seen_opts[0]
    assert opts["extract_flat"] == "in_playlist"
    assert opts["cookiefile"]
    assert opts["extractor_args"] == build_shared_extraction_opts()["extractor_args"]
    assert opts["js_runtimes"] == build_shared_extraction_opts()["js_runtimes"]


def test_archive_only_mode_enumerates_with_cookies_and_shared_wiring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Skip Download? writes ids to the archive; without cookies it silently writes none."""
    calls: list[dict] = []

    def _fake_entries(url: str, **kwargs: object) -> list[dict]:
        calls.append({"url": url, **kwargs})
        return [{"id": "abc123"}]

    monkeypatch.setattr(meadowlark, "extract_video_entries", _fake_entries)
    monkeypatch.setattr(meadowlark, "ARCHIVE_PATH", tmp_path / "archive.txt")
    monkeypatch.setattr(meadowlark.QYT, "QLogger", lambda _q: SimpleNamespace(debug=print))

    window = _StubWindow()
    window.downloadQueue = None
    window.labelOutput = SimpleNamespace(setText=lambda _t: None)
    window.barProgress = SimpleNamespace(setRange=lambda _a, _b: None, setValue=lambda _v: None)
    window.handle_queue_empty = lambda: None
    window.skip_downloading([_PLAYLIST_URL], "1080playlists")

    assert len(calls) == 1
    assert calls[0]["extract_flat"] == "in_playlist"
    assert calls[0]["cookiefile"]
    assert (tmp_path / "archive.txt").read_text(encoding="utf-8") == "youtube abc123\n"


def test_archive_only_mode_writes_a_video_shared_by_two_urls_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A video in two dropped playlists is archived and counted once, not per URL."""
    entries_by_url = {"u1": [{"id": "shared"}, {"id": "only1"}], "u2": [{"id": "shared"}]}
    monkeypatch.setattr(
        meadowlark, "extract_video_entries", lambda url, **_kw: entries_by_url[url]
    )
    monkeypatch.setattr(meadowlark, "ARCHIVE_PATH", tmp_path / "archive.txt")
    debug_lines: list[str] = []
    monkeypatch.setattr(
        meadowlark.QYT, "QLogger", lambda _q: SimpleNamespace(debug=debug_lines.append)
    )
    log_lines: list[str] = []

    window = _StubWindow()
    window.downloadQueue = None
    window.labelOutput = SimpleNamespace(setText=lambda _t: None)
    window.barProgress = SimpleNamespace(setRange=lambda _a, _b: None, setValue=lambda _v: None)
    window.logEdit = SimpleNamespace(appendPlainText=log_lines.append)
    window.handle_queue_empty = lambda: None
    window.skip_downloading(["u1", "u2"], "1080playlists")

    archive = (tmp_path / "archive.txt").read_text(encoding="utf-8")
    assert archive == "youtube shared\nyoutube only1\n"
    assert log_lines == ["Archive-only mode: 2 IDs written."]
    assert debug_lines == ["Added to archive: youtube shared", "Added to archive: youtube only1"]

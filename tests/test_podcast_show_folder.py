"""
Regression tests for the invariant that every podcast episode is filed under its show folder.

A podcast episode that is still live when the podcast check runs is skipped by
``build_match_filter`` and parked in the pending queue.  When the stream ends the
episode is re-queued from the pending queue, and that path must rebuild the same
``<podcast base>/<show label>/`` output template the grouped podcast download
used -- otherwise the episode lands in the "misc" directory that
``get_source_options("audio_playlists")`` points at.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.settings_dialog as sd
from src.path_utils import sanitize_for_path
from src.pending_queue import (
    load_pending_queue,
    make_pending_record,
    save_pending_queue,
)
from src.ydl_options import build_podcast_outtmpl, podcast_base_dir
from tests.test_cache_early_exit import import_vid_module

SHOW = "Android Faithful"


# ---------------------------------------------------------------------------
# Output-template helper
# ---------------------------------------------------------------------------


def test_build_podcast_outtmpl_uses_show_folder_beside_misc_dir() -> None:
    """The show folder is a sibling of the configured misc directory."""
    tmpl = build_podcast_outtmpl(SHOW)
    assert tmpl == f"{podcast_base_dir()}/{SHOW}/%(title)s.%(ext)s"


def test_build_podcast_outtmpl_falls_back_to_misc_for_unknown_show() -> None:
    """An episode with no resolvable show label still has a home."""
    assert build_podcast_outtmpl(None) == f"{podcast_base_dir()}/misc/%(title)s.%(ext)s"


def test_build_podcast_outtmpl_empty_string_label_falls_back_to_misc() -> None:
    """An empty-string label is falsy, same as None -- it must not create a folder named ''."""
    assert build_podcast_outtmpl("") == f"{podcast_base_dir()}/misc/%(title)s.%(ext)s"


def test_build_podcast_outtmpl_whitespace_only_label_falls_back_to_misc() -> None:
    """
    A whitespace-only label is truthy in Python.

    sanitize_for_path collapses it to '' internally, so it lands in misc too --
    via a different code path than None.
    """
    assert build_podcast_outtmpl("   ") == f"{podcast_base_dir()}/misc/%(title)s.%(ext)s"


def test_build_podcast_outtmpl_numeric_string_label_is_not_falsy() -> None:
    """A label of "0" is a non-empty string and must not be mistaken for missing (JS-style falsy trap)."""
    assert build_podcast_outtmpl("0") == f"{podcast_base_dir()}/0/%(title)s.%(ext)s"


def test_build_podcast_outtmpl_sanitizes_windows_invalid_chars() -> None:
    """Show names containing Windows-illegal path characters are sanitized, not passed through raw."""
    raw = 'Cool Show: "Live" <2024>|Ep?1*'
    expected_label = sanitize_for_path(raw)
    tmpl = build_podcast_outtmpl(raw)
    assert tmpl == f"{podcast_base_dir()}/{expected_label}/%(title)s.%(ext)s"
    label_segment = tmpl.split("/")[-2]
    for ch in '<>:"/\\|?*':
        assert ch not in label_segment


def test_build_podcast_outtmpl_slugifies_very_long_label() -> None:
    """
    A show label long enough to blow the Windows path budget gets slugified.

    It is replaced with a short hash-based slug rather than being written out in full.
    """
    with patch.dict(sd._runtime, {"VID_DL_PODCAST_MISC_OUTPUT_DIR": "/x/misc"}):
        long_label = "A" * 300
        tmpl = build_podcast_outtmpl(long_label)
    assert long_label not in tmpl
    label_segment = tmpl.split("/")[-2]
    assert len(label_segment) < 60


def test_build_podcast_outtmpl_uses_custom_misc_dir_override() -> None:
    """
    podcast_base_dir() and the misc fallback both track a runtime override.

    Not just the frozen config constant.
    """
    with patch.dict(sd._runtime, {"VID_DL_PODCAST_MISC_OUTPUT_DIR": "/custom/pods/misc"}):
        assert podcast_base_dir() == "/custom/pods"
        assert build_podcast_outtmpl(None) == "/custom/pods/misc/%(title)s.%(ext)s"
        assert build_podcast_outtmpl(SHOW) == f"/custom/pods/{SHOW}/%(title)s.%(ext)s"


# ---------------------------------------------------------------------------
# Pending-queue persistence of the show label
# ---------------------------------------------------------------------------


def test_pending_queue_round_trips_show_label(tmp_path: Path) -> None:
    """The show label survives the file round-trip so it can rebuild the outtmpl."""
    path = tmp_path / "pending_queue.json"
    record = make_pending_record(
        "https://yt.com/watch?v=abc", "audio_playlists", playlist_id="PLxyz", label=SHOW
    )
    save_pending_queue(path, [record])
    loaded = load_pending_queue(path)
    assert len(loaded) == 1
    assert loaded[0]["url"] == "https://yt.com/watch?v=abc"
    assert loaded[0]["playlist_id"] == "PLxyz"
    assert loaded[0]["label"] == SHOW


# ---------------------------------------------------------------------------
# Re-queue from the pending queue must restore the show folder
# ---------------------------------------------------------------------------


def test_window_check_pending_queue_restores_show_folder(tmp_path: Path) -> None:
    """MyWindow (the path the app actually runs) re-files an ended stream by label."""
    vd = import_vid_module()
    path = tmp_path / "pending_queue.json"
    save_pending_queue(
        path,
        [
            make_pending_record(
                "https://youtube.com/watch?v=ended", "audio_playlists", label=SHOW
            )
        ],
    )

    queued: list = []

    class DummyWin:
        pending_queue_path = path
        logEdit = SimpleNamespace(appendPlainText=lambda _msg: None)
        barProgress = SimpleNamespace(setRange=lambda _a, _b: None)
        downloadQueue = SimpleNamespace(put=queued.append)
        buttonPending = SimpleNamespace(setText=lambda _t: None, setVisible=lambda _v: None)
        _pending_dialog = None
        _pending_deps = vd.MyWindow._pending_deps
        check_pending_queue = vd.MyWindow.check_pending_queue
        _refresh_pending_button = vd.MyWindow._refresh_pending_button

        def _create_download_context(self):
            return (
                SimpleNamespace(info_changed=None),
                SimpleNamespace(message_changed=None),
                {},
            )

        def get_options(self, urls, source, skip_playlist_dialog=False):
            return dict(vd.utils.get_source_options(source))

        def append_properties(self, ydl_opts, properties):
            ydl_opts.update(properties)
            return ydl_opts

        def _wire_download_signals(self, _qhook, _qlogger) -> None:
            pass

    ydl = MagicMock()
    ydl.extract_info.return_value = {"is_live": False, "live_status": None}
    with patch.object(vd.yt_dlp, "YoutubeDL") as mock_ydl:
        mock_ydl.return_value.__enter__.return_value = ydl
        DummyWin().check_pending_queue()

    (_urls, queued_opts) = queued[0]
    assert queued_opts["outtmpl"] == build_podcast_outtmpl(SHOW)


def test_grouped_podcast_batch_binds_show_label_to_match_filter(
    tmp_path: Path,
) -> None:
    """The batch filter parks a still-live episode with the batch's show label."""
    vd = import_vid_module()

    queued: list = []

    class DummyWin:
        pending_queue_path = tmp_path / "pending_queue.json"
        live_queue_log = SimpleNamespace(emit=lambda _msg: None)
        downloadQueue = SimpleNamespace(put=queued.append)
        barProgress = SimpleNamespace(setRange=lambda _a, _b: None)
        add_to_live_queue = vd.MyWindow.add_to_live_queue
        make_match_filter = vd.MyWindow.make_match_filter
        _queue_podcast_downloads_grouped = vd.MyWindow._queue_podcast_downloads_grouped

        def _fork_download_context(self, base_opts: dict):
            return SimpleNamespace(info_changed=None), SimpleNamespace(
                message_changed=None
            ), dict(base_opts)

        def _wire_download_signals(self, _qhook, _qlogger) -> None:
            pass

    win = DummyWin()
    win._queue_podcast_downloads_grouped(
        [{"url": "https://youtube.com/watch?v=live", "playlist": SHOW}],
        {"match_filter": lambda _info, _incomplete: None},
    )

    (_urls, batch_opts) = queued[0]
    assert batch_opts["outtmpl"] == build_podcast_outtmpl(SHOW)

    batch_opts["match_filter"](
        {
            "is_live": True,
            "live_status": "is_live",
            "webpage_url": "https://youtube.com/watch?v=live",
        },
        False,
    )
    records = {r["url"]: r for r in load_pending_queue(DummyWin.pending_queue_path)}
    parked = records["https://youtube.com/watch?v=live"]
    assert (parked["source"], parked["playlist_id"], parked["label"]) == (
        "audio_playlists",
        None,
        SHOW,
    )

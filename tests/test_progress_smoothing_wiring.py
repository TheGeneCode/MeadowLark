"""
Unit tests for the Qt-side wiring of progress smoothing in meadowlark.pyw.

Covers MyWindow.handle_info_changed and MyWindow.handle_queue_empty.
src/progress_smoothing.py itself has no Qt dependency and is covered by
tests/test_progress_smoothing.py. This file covers the glue the handoff flagged as
untested: the None-handling in handle_info_changed's label/progress-bar branches, the
MAX_INT_PROGRESS clamp for huge totals, the early returns for "finished"/unknown status,
and handle_queue_empty resetting the smoother before touching the podcast indicator.
"""

from tests._vd_loader import import_vid_module


class _LabelRecorder:
    """Stand-in for QLabel -- records the last text set."""

    def __init__(self) -> None:
        self.text: str | None = None

    def setText(self, t: str) -> None:
        self.text = t


class _BarRecorder:
    """Stand-in for QProgressBar -- records setMaximum/setValue calls."""

    def __init__(self) -> None:
        self.maximum: int | None = None
        self.value: int | None = None
        self.maximum_calls: list[int] = []
        self.value_calls: list[int] = []
        self.range_calls: list[tuple[int, int]] = []

    def setMaximum(self, m: int) -> None:
        self.maximum = m
        self.maximum_calls.append(m)

    def setValue(self, v: int) -> None:
        self.value = v
        self.value_calls.append(v)

    def setRange(self, lo: int, hi: int) -> None:
        self.maximum = hi
        self.range_calls.append((lo, hi))


def _make_win(vd, *, min_interval: float = 0.0):
    """Build a minimal MyWindow stand-in wired to the real bound progress-handling methods."""

    class DummyWin:
        handle_info_changed = vd.MyWindow.handle_info_changed
        handle_queue_empty = vd.MyWindow.handle_queue_empty

        _ready_text = "Ready"
        _podcast_pending_urls: list = []
        _podcasts_downloading: set = set()

        def _set_podcast_indicator(self, state: str) -> None:
            self.podcast_indicator_calls.append(state)

    win = DummyWin()
    win._progress_smoother = vd.ProgressSmoother(min_interval=min_interval)
    win.labelOutput = _LabelRecorder()
    win.barProgress = _BarRecorder()
    win.podcast_indicator_calls = []
    return win


def _tick(downloaded: int, total: int | None = None, **extra) -> dict:
    d: dict = {"status": "downloading", "downloaded_bytes": downloaded, "filename": "a.mp4"}
    if total is not None:
        d["total_bytes"] = total
    d.update(extra)
    return d


def test_handle_info_changed_updates_label_and_bar_for_normal_total() -> None:
    """A normal-sized total updates both the label and the progress bar range/value."""
    vd = import_vid_module()
    win = _make_win(vd)

    win.handle_info_changed(_tick(500_000, total=1_000_000))

    assert win.labelOutput.text is not None
    assert "of" in win.labelOutput.text
    assert win.barProgress.maximum == 1_000_000
    assert win.barProgress.value == 500_000


def test_handle_info_changed_total_above_max_int_progress_scales_into_range() -> None:
    """A total exceeding MAX_INT_PROGRESS must clamp setMaximum and scale setValue, not overflow."""
    vd = import_vid_module()
    win = _make_win(vd)
    huge_total = vd.MAX_INT_PROGRESS + 1_000_000_000
    downloaded = huge_total // 2

    win.handle_info_changed(_tick(downloaded, total=huge_total))

    assert win.barProgress.maximum == vd.MAX_INT_PROGRESS
    # downloaded/total * MAX_INT_PROGRESS ~= half of MAX_INT_PROGRESS
    assert win.barProgress.value is not None
    assert 0 <= win.barProgress.value <= vd.MAX_INT_PROGRESS


def test_handle_info_changed_unknown_total_skips_bar_but_still_updates_label() -> None:
    """When total is unknown (None), the bar must be left untouched, only the label updates."""
    vd = import_vid_module()
    win = _make_win(vd)

    win.handle_info_changed(_tick(500_000, total=None))

    assert win.labelOutput.text is not None
    assert win.barProgress.maximum is None  # setMaximum never called
    assert win.barProgress.value is None  # setValue never called


def test_handle_info_changed_throttled_call_touches_neither_widget() -> None:
    """A throttled tick (smoother returns None) must not touch label or bar at all."""
    vd = import_vid_module()
    win = _make_win(vd, min_interval=10.0)

    win.handle_info_changed(_tick(100, total=1_000, filename="a.mp4"))  # first emits
    win.labelOutput.text = None  # clear so we can prove the second call is a no-op
    win.handle_info_changed(_tick(200, total=1_000, filename="a.mp4", speed=None))

    assert win.labelOutput.text is None
    assert win.barProgress.maximum_calls == [1_000]  # only the first call touched it


def test_handle_info_changed_finished_status_delegates_and_returns_early() -> None:
    """Status == 'finished' must roll the file into the smoother and touch neither widget."""
    vd = import_vid_module()
    win = _make_win(vd)

    win.handle_info_changed(
        {"status": "finished", "total_bytes": 12_345, "filename": "a.mp4"}
    )

    assert win.labelOutput.text is None
    assert win.barProgress.maximum is None
    assert win._progress_smoother._completed_bytes == 12_345


def test_handle_info_changed_unknown_status_is_a_complete_noop() -> None:
    """A status outside {'downloading', 'finished'} (e.g. yt-dlp's 'error') must change nothing."""
    vd = import_vid_module()
    win = _make_win(vd)

    win.handle_info_changed({"status": "error", "downloaded_bytes": 999})

    assert win.labelOutput.text is None
    assert win.barProgress.maximum is None
    assert win._progress_smoother._completed_bytes == 0


def test_handle_queue_empty_resets_smoother_and_sets_ready_text() -> None:
    """handle_queue_empty must reset the smoother's session state and show the ready label."""
    vd = import_vid_module()
    win = _make_win(vd)
    win.handle_info_changed(_tick(500_000, total=1_000_000))
    assert win._progress_smoother._file_downloaded == 500_000

    win.handle_queue_empty()

    assert win.labelOutput.text == "Ready"
    assert win._progress_smoother._file_downloaded == 0
    assert win._progress_smoother._completed_bytes == 0
    assert win.barProgress.range_calls == [(0, 1)]
    assert win.barProgress.value == 0


def test_handle_queue_empty_sets_pending_indicator_when_podcasts_pending() -> None:
    """With podcast URLs still pending, the indicator must go to 'pending', not 'all_good'."""
    vd = import_vid_module()
    win = _make_win(vd)
    win._podcast_pending_urls = ["https://example.com/1"]

    win.handle_queue_empty()

    assert win.podcast_indicator_calls == ["pending"]

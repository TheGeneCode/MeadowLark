"""
Unit tests for the dynamic resolution tile grid in meadowlark.pyw (Phase 4).

Covers MyWindow._build_resolution_cell, _build_resolution_container,
_populate_resolution_grid, the reload_settings rebuild trigger, and the
enabled-heights fallback in _download_pending_now.

Critical: `enabled_heights` must be patched on the executed meadowlark module
object (here bound to the name `vd`, via tests._vd_loader.import_vid_module),
not on `src.settings_dialog.enabled_heights` -- patching the latter has no effect
once meadowlark.pyw has already imported the name into its own namespace.
"""

from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from tests._vd_loader import import_vid_module

# Ensure QApplication exists for Qt testing
_app = QApplication.instance() or QApplication([])


def _make_window(vd, tmp_path: Path):
    """
    Build a lightweight QWidget-based stand-in for MyWindow.

    Binds only the methods under test (plus what they transitively call) rather
    than running MyWindow.__init__, which starts a download-queue thread and
    several timers that have no place in a unit test.
    """

    class _TestWindow(QWidget):
        _build_resolution_cell = vd.MyWindow._build_resolution_cell
        _add_grid_cell = vd.MyWindow._add_grid_cell
        _build_resolution_container = vd.MyWindow._build_resolution_container
        _populate_resolution_grid = vd.MyWindow._populate_resolution_grid
        _build_podcast_container = vd.MyWindow._build_podcast_container
        playlist_button_clicked = vd.MyWindow.playlist_button_clicked
        reload_settings = vd.MyWindow.reload_settings
        _apply_label_changes = vd.MyWindow._apply_label_changes
        _apply_path_changes = vd.MyWindow._apply_path_changes
        _apply_always_on_top = vd.MyWindow._apply_always_on_top
        _restart_podcast_timer = vd.MyWindow._restart_podcast_timer
        _download_pending_now = vd.MyWindow._download_pending_now
        _remove_pending_downloads = vd.MyWindow._remove_pending_downloads
        _refresh_pending_button = vd.MyWindow._refresh_pending_button

        def __init__(self) -> None:
            super().__init__()
            self._drop_labels: dict[int, object] = {}
            self._playlist_buttons: dict[int, object] = {}
            self.checkIgnoreArchive = MagicMock()
            self.checkIgnoreArchive.isChecked.return_value = False
            self.pending_queue_path = tmp_path / "pending_queue.json"
            self._pending_dialog = None
            self.buttonPending = MagicMock()
            self.logs: list[str] = []
            self.requested: list[tuple[list, str]] = []

        def handle_log_entry(self, msg: str) -> None:
            self.logs.append(msg)

        def request_detected(self, urls: list, source: str) -> None:
            self.requested.append((urls, source))

        def _show_podcast_status(self) -> None:
            pass

    vd._init_runtime_settings()
    return _TestWindow()


def _build_grid(vd, window, heights: tuple[int, ...]) -> None:
    """
    Build the resolution container with `enabled_heights` patched to `heights`.

    The returned container widget is stashed on `window` -- it has no parent, so
    without a live Python reference it (and everything the grid owns) would be
    garbage-collected the moment this function returns.
    """
    with patch.object(vd, "enabled_heights", return_value=heights):
        window._resolution_container = window._build_resolution_container()


def _all_grid_widgets(grid) -> list[QWidget]:
    """Every widget reachable from a QGridLayout: its direct cell widgets and their children."""
    widgets: list[QWidget] = []
    for i in range(grid.count()):
        item = grid.itemAt(i)
        w = item.widget() if item else None
        if w is not None:
            widgets.append(w)
            widgets.extend(w.findChildren(QWidget))
    return widgets


def _widget_at(grid, row: int, col: int) -> QWidget | None:
    """Return the widget occupying (row, col), or None if that cell is empty."""
    item = grid.itemAtPosition(row, col)
    return item.widget() if item is not None else None


# ---------------------------------------------------------------------------
# Building the grid from the enabled set
# ---------------------------------------------------------------------------


def test_default_enabled_builds_two_tiles_plus_audio(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, (1080, 720))

    assert set(window._drop_labels.keys()) == {1080, 720}
    assert len(window._drop_labels) == 2
    assert hasattr(window, "labelAudio")


def test_tile_colors_match_registry(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, (1080, 720))

    assert "#424769" in window._drop_labels[1080].styleSheet()
    assert "#7077A1" in window._drop_labels[720].styleSheet()


def test_tile_source_keys_are_bare_heights(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, (1080, 720))

    assert window._drop_labels[1080].source_key == "1080"
    assert window._drop_labels[720].source_key == "720"


def test_six_rungs_wrap_to_three_columns(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    all_heights = tuple(p.height for p in vd.RESOLUTION_PRESETS)

    _build_grid(vd, window, all_heights)

    grid = window._resolution_grid
    assert grid.columnCount() == 3
    # Each visual row occupies two grid rows -- buttons in 2r, tiles in 2r+1 --
    # so 7 cells across 3 visual rows span 6 grid rows and hold 14 widgets.
    assert grid.rowCount() == 6
    assert grid.count() == 14  # (6 rungs + audio) x (button + tile)


def test_tiles_shrink_as_count_grows(tmp_path: Path) -> None:
    vd = import_vid_module()

    window_small = _make_window(vd, tmp_path)
    _build_grid(vd, window_small, (1080, 720))
    assert window_small._drop_labels[1080].minimumWidth() == 150

    window_large = _make_window(vd, tmp_path)
    all_heights = tuple(p.height for p in vd.RESOLUTION_PRESETS)
    _build_grid(vd, window_large, all_heights)
    assert window_large._drop_labels[all_heights[0]].minimumWidth() == 100


# ---------------------------------------------------------------------------
# Playlist button routing
# ---------------------------------------------------------------------------


def test_playlist_button_routes_to_playlist_source(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))

    with patch.object(window, "playlist_button_clicked") as mock_clicked:
        window._playlist_buttons[1080].click()

    mock_clicked.assert_called_once_with("1080playlists")


def test_each_button_routes_to_its_own_rung(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (2160, 1080, 480))

    with patch.object(window, "playlist_button_clicked") as mock_clicked:
        window._playlist_buttons[2160].click()
        window._playlist_buttons[1080].click()
        window._playlist_buttons[480].click()

    assert [c.args for c in mock_clicked.call_args_list] == [
        ("2160playlists",),
        ("1080playlists",),
        ("480playlists",),
    ]


# ---------------------------------------------------------------------------
# Rebuild behaviour
# ---------------------------------------------------------------------------


def test_rebuild_replaces_widgets(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._populate_resolution_grid()  # must not raise

    assert set(window._drop_labels.keys()) == {2160, 480}
    assert window._resolution_grid.count() == 6  # (2 rungs + audio) x (button + tile)


def test_rebuild_does_not_leak_old_rungs(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    old_1080 = window._drop_labels[1080]
    old_720 = window._drop_labels[720]

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._populate_resolution_grid()

    remaining = _all_grid_widgets(window._resolution_grid)
    assert old_1080 not in remaining
    assert old_720 not in remaining


# ---------------------------------------------------------------------------
# reload_settings rebuild trigger
# ---------------------------------------------------------------------------


def test_settings_change_triggers_rebuild(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    window._resolution_grid = MagicMock()

    with patch.object(window, "_populate_resolution_grid") as mock_populate:
        window.reload_settings({"VID_DL_ENABLED_RESOLUTIONS": "2160,480"})

    mock_populate.assert_called_once()


def test_label_change_triggers_rebuild(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    window._resolution_grid = MagicMock()

    with patch.object(window, "_populate_resolution_grid") as mock_populate:
        window.reload_settings({"VID_DL_LABEL_DROP_720": "Medium"})

    mock_populate.assert_called_once()


def test_unrelated_setting_does_not_rebuild(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    window._resolution_grid = MagicMock()

    with patch.object(window, "_populate_resolution_grid") as mock_populate:
        window.reload_settings({"VID_DL_ALWAYS_ON_TOP": True})

    mock_populate.assert_not_called()


# ---------------------------------------------------------------------------
# Pending-download fallback source
# ---------------------------------------------------------------------------


def test_pending_fallback_uses_highest_enabled(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._download_pending_now([{"url": "u"}])

    assert window.requested == [(["u"], "2160")]


def test_pending_explicit_source_bypasses_fallback(tmp_path: Path) -> None:
    """A record with a real source must win over the enabled-heights fallback."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._download_pending_now([{"url": "u", "source": "480"}])

    assert window.requested == [(["u"], "480")]


def test_pending_empty_string_source_falls_back(tmp_path: Path) -> None:
    """An empty-string source is falsy, so it must fall back like a missing key."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    with patch.object(vd, "enabled_heights", return_value=(720,)):
        window._download_pending_now([{"url": "u", "source": ""}])

    assert window.requested == [(["u"], "720")]


def test_pending_missing_url_is_a_no_op(tmp_path: Path) -> None:
    """No `url` key must return before touching the pending store or requesting a download."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    window._download_pending_now([{"source": "1080"}])

    assert window.requested == []


# ---------------------------------------------------------------------------
# Tile size tiers (_tile_min_size boundaries) and unregistered-height guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heights", "expected_min_size", "expected_font_size"),
    [
        ((1080,), 150, 30),  # cell_count=2 (1 rung + audio): largest tier
        ((1080, 720), 150, 30),  # cell_count=3: largest-tier upper edge
        ((2160, 1440, 1080), 120, 24),  # cell_count=4: just above the lower boundary
        ((2160, 1440, 1080, 720, 480), 120, 24),  # cell_count=6: middle-tier upper edge
        ((2160, 1440, 1080, 720, 480, 360), 100, 20),  # cell_count=7: smallest tier
    ],
)
def test_tile_size_tiers_at_cell_count_boundaries(
    tmp_path: Path,
    heights: tuple[int, ...],
    expected_min_size: int,
    expected_font_size: int,
) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, heights)

    top_rung = heights[0]
    assert window._drop_labels[top_rung].minimumWidth() == expected_min_size
    assert window._drop_labels[top_rung].font().pointSize() == expected_font_size
    # The audio tile is built in the same pass and must share the tier.
    assert window.labelAudio.minimumWidth() == expected_min_size
    assert window.labelAudio.font().pointSize() == expected_font_size


def test_all_built_columns_get_stretch(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, (1080, 720))

    grid = window._resolution_grid
    assert [grid.columnStretch(c) for c in range(3)] == [1, 1, 1]


def test_all_drop_targets_share_one_top_and_height(tmp_path: Path) -> None:
    """
    Every drop target in a visual row must start at the same y and be equally tall.

    The audio column's button area is a container holding podcastIndicator, a
    fixed 34px square 10px taller than a bare PlaylistButton. While each column
    stacked its own button and tile in a private QVBoxLayout, nothing equalized
    those button areas across columns, so the audio tile started 10px lower and
    ended up 10px shorter than its neighbours. Buttons and tiles now occupy
    separate shared grid rows so QGridLayout equalizes both.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))

    container = window._resolution_container
    container.resize(700, 400)
    container.show()
    QApplication.processEvents()

    tiles = [window._drop_labels[1080], window._drop_labels[720], window.labelAudio]
    tops = {t.mapTo(container, t.rect().topLeft()).y() for t in tiles}
    heights = {t.height() for t in tiles}

    assert len(tops) == 1, f"drop targets start at different heights: {tops}"
    assert len(heights) == 1, f"drop targets have different heights: {heights}"


def test_every_column_shrinks_to_the_same_tile_width(tmp_path: Path) -> None:
    """
    Squeezed to the grid's minimum, every drop target must still be the same width.

    Each column stacks a button area over a tile, so the button areas used to set
    the column minimums -- and they disagree: the audio column's area carries the
    fixed 34px podcastIndicator on top of a playlist button. With seven cells the
    audio cell lands in column 0 and the tile minimum drops to 100px, so that
    column stopped shrinking with its neighbours and its tiles stayed wider.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    heights = (2160, 1440, 1080, 720, 480, 360)
    _build_grid(vd, window, heights)

    container = window._resolution_container
    container.show()
    # Narrower than any column floor, so the layout is pinned at its minimum.
    container.resize(1, 420)
    QApplication.processEvents()

    min_size = window._drop_labels[heights[0]].minimumWidth()
    widths = {t.width() for t in (*window._drop_labels.values(), window.labelAudio)}

    assert widths == {min_size}, f"drop targets have different widths: {widths}"


def test_playlist_buttons_share_one_top(tmp_path: Path) -> None:
    """
    The YT Podcasts button must sit level with every other rung's button.

    Its container is taller than the button itself (podcastIndicator is 34px),
    so the button is centered in that container. The shared button grid row
    equalizes the row height across columns, which lines all the buttons up
    without top-aligning the podcast button away from its own indicator.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))

    container = window._resolution_container
    container.resize(700, 400)
    container.show()
    QApplication.processEvents()

    buttons = [
        window._playlist_buttons[1080],
        window._playlist_buttons[720],
        window.buttonAudioPlaylists,
    ]
    tops = {b.mapTo(container, b.rect().topLeft()).y() for b in buttons}

    assert len(tops) == 1, f"playlist buttons start at different heights: {tops}"


def test_podcast_indicator_stays_centered_with_its_button(tmp_path: Path) -> None:
    """
    The indicator must stay vertically centered against buttonAudioPlaylists.

    Guards the regression introduced by top-aligning the button inside its
    container: that lined the button up with its neighbours but left the taller
    indicator centered in the row, so the indicator read as sitting too low.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))

    container = window._resolution_container
    container.resize(700, 400)
    container.show()
    QApplication.processEvents()

    button = window.buttonAudioPlaylists
    indicator = window.podcastIndicator
    button_center = button.mapTo(container, button.rect().center()).y()
    indicator_center = indicator.mapTo(container, indicator.rect().center()).y()

    assert abs(button_center - indicator_center) <= 1


# ---------------------------------------------------------------------------
# Grid row/column arithmetic: exact placement, partial final rows, 0-rung edge
# ---------------------------------------------------------------------------


def test_partial_last_row_places_widgets_without_collision(tmp_path: Path) -> None:
    """
    cell_count=4 (3 rungs + audio) leaves the final visual row two cells short.

    Guards the row-index arithmetic in `_add_grid_cell`: the audio cell (position
    3) must land at grid row 2/3, column 0, and the two grid columns it does not
    occupy in that visual row must stay empty rather than colliding with a
    neighbour or silently reusing a widget from the full first row.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    heights = (2160, 1440, 1080)

    _build_grid(vd, window, heights)

    grid = window._resolution_grid
    assert grid.rowCount() == 4
    # Visual row 0 (grid rows 0/1): full, one button+tile pair per column.
    for col, height in enumerate(heights):
        assert _widget_at(grid, 0, col) is window._playlist_buttons[height]
        assert _widget_at(grid, 1, col) is window._drop_labels[height]
    # Visual row 1 (grid rows 2/3): only the audio cell, at column 0.
    audio_container = _widget_at(grid, 2, 0)
    assert audio_container is not None
    assert window.buttonAudioPlaylists in audio_container.findChildren(type(window.buttonAudioPlaylists))
    assert _widget_at(grid, 3, 0) is window.labelAudio
    # Columns 1 and 2 of the partial row must be empty, not reused.
    assert _widget_at(grid, 2, 1) is None
    assert _widget_at(grid, 2, 2) is None
    assert _widget_at(grid, 3, 1) is None
    assert _widget_at(grid, 3, 2) is None


def test_full_final_row_has_no_gaps(tmp_path: Path) -> None:
    """cell_count=6 (5 rungs + audio) fills both visual rows completely."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    heights = (2160, 1440, 1080, 720, 480)

    _build_grid(vd, window, heights)

    grid = window._resolution_grid
    assert grid.rowCount() == 4
    for col, height in enumerate(heights[:3]):
        assert _widget_at(grid, 0, col) is window._playlist_buttons[height]
        assert _widget_at(grid, 1, col) is window._drop_labels[height]
    assert _widget_at(grid, 2, 0) is window._playlist_buttons[heights[3]]
    assert _widget_at(grid, 3, 0) is window._drop_labels[heights[3]]
    assert _widget_at(grid, 2, 1) is window._playlist_buttons[heights[4]]
    assert _widget_at(grid, 3, 1) is window._drop_labels[heights[4]]
    # Audio takes the last slot in the second visual row -- no gap before it.
    assert _widget_at(grid, 3, 2) is window.labelAudio
    assert _widget_at(grid, 2, 2) is not None


def test_zero_rungs_places_audio_alone_at_origin(tmp_path: Path) -> None:
    """
    Verify _add_grid_cell handles zero rungs.

    No enabled rungs (heights=()) is a degenerate but reachable shape: only the
    audio cell is built, at position 0. Verifies `_add_grid_cell`'s row/column
    arithmetic does not require at least one rung to behave correctly.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    _build_grid(vd, window, ())

    grid = window._resolution_grid
    assert window._drop_labels == {}
    assert window._playlist_buttons == {}
    assert grid.rowCount() == 2
    assert grid.count() == 2
    assert _widget_at(grid, 0, 0) is not None
    assert _widget_at(grid, 1, 0) is window.labelAudio


# ---------------------------------------------------------------------------
# Row growth / row-stretch behaviour under resize
# ---------------------------------------------------------------------------


def test_button_rows_stay_fixed_height_when_window_grows_tall(tmp_path: Path) -> None:
    """
    Growing the window taller must only grow the tile row, not the button row.

    The audio column's button-area container has the QWidget default (Preferred)
    vertical size policy, not Fixed like a bare PlaylistButton -- in principle it
    could compete for the extra vertical space Qt hands out to stretch-0 rows on
    resize. Pins the empirically-verified behaviour that it does not: the button
    row (shared across all columns) stays at its natural size and every column's
    button-row cell stays equal height, while the tile row absorbs all growth.

    WA_DontShowOnScreen runs the container through the real show()/layout-activation
    pipeline without letting Qt clamp its resize() to the runner's actual screen
    geometry -- CI's virtual desktop is shorter than a dev screen, and without this
    attribute the requested 700x1200 resize gets capped short of what the assertion
    below requires.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    container = window._resolution_container
    container.resize(700, 300)
    container.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    container.show()
    QApplication.processEvents()

    grid = window._resolution_grid
    short_button_heights = {_widget_at(grid, 0, c).height() for c in range(3)}
    short_tile_height = _widget_at(grid, 1, 0).height()

    container.resize(700, 1200)
    QApplication.processEvents()
    QApplication.processEvents()

    tall_button_heights = {_widget_at(grid, 0, c).height() for c in range(3)}
    tall_tile_height = _widget_at(grid, 1, 0).height()

    assert tall_button_heights == short_button_heights, (
        f"button row grew on resize: {short_button_heights} -> {tall_button_heights}"
    )
    assert tall_tile_height > short_tile_height + 500


# ---------------------------------------------------------------------------
# Audio widget freshness after rebuild (no dangling references)
# ---------------------------------------------------------------------------


def test_audio_button_and_indicator_are_fresh_after_rebuild(tmp_path: Path) -> None:
    """
    Verify the audio button and indicator are rebuilt, not just shadowed.

    `_build_podcast_container` reassigns `self.buttonAudioPlaylists` and
    `self.podcastIndicator` on every call. After a rebuild those attributes must
    point at newly-built widgets, and the old ones must no longer be reachable
    from the grid (they were torn down, not merely shadowed).
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    old_button = window.buttonAudioPlaylists
    old_indicator = window.podcastIndicator

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._populate_resolution_grid()

    assert window.buttonAudioPlaylists is not old_button
    assert window.podcastIndicator is not old_indicator
    remaining = _all_grid_widgets(window._resolution_grid)
    assert old_button not in remaining
    assert old_indicator not in remaining
    assert window.buttonAudioPlaylists in remaining
    assert window.podcastIndicator in remaining


def test_reload_settings_applies_podcast_button_label_to_fresh_widget(
    tmp_path: Path,
) -> None:
    """
    Verify the fresh podcast button, not a stale one, receives the new label.

    Symmetric to test_reload_settings_rebuild_runs_before_audio_label_apply, but
    for the podcast button label rather than the audio drop label -- both are
    recreated by the same rebuild and must both land on the fresh widget.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    stale_button = window.buttonAudioPlaylists

    with (
        patch.object(vd, "enabled_heights", return_value=(2160,)),
        patch.object(vd, "get_setting", return_value=None),
    ):
        window.reload_settings(
            {
                "VID_DL_ENABLED_RESOLUTIONS": "2160",
                "VID_DL_LABEL_BTN_PODCASTS": "New Podcasts Label",
            }
        )

    assert window.buttonAudioPlaylists is not stale_button
    assert window.buttonAudioPlaylists.text() == "New Podcasts Label"


def test_build_resolution_cell_unregistered_height_raises(tmp_path: Path) -> None:
    """
    Defensive branch.

    enabled_heights() should never emit an unregistered height, but
    _build_resolution_cell must still refuse to build a tile with no preset.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    with pytest.raises(ValueError, match="900"):
        window._build_resolution_cell(900, min_size=150, font_size=30)


# ---------------------------------------------------------------------------
# get_setting() override / fallback-chain behaviour
# ---------------------------------------------------------------------------


def test_custom_drop_label_setting_is_honored(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    def fake_get_setting(key: str) -> str | None:
        return "My Custom Drop" if key == "VID_DL_LABEL_DROP_1080" else None

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", side_effect=fake_get_setting),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._drop_labels[1080].text() == "My Custom Drop"


def test_custom_button_label_setting_is_honored(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    def fake_get_setting(key: str) -> str | None:
        # VID_DL_LABEL_BTN_PLAYLISTS is the legacy button-label key for 1080.
        return "Custom Button" if key == "VID_DL_LABEL_BTN_PLAYLISTS" else None

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", side_effect=fake_get_setting),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._playlist_buttons[1080].text() == "Custom Button"


def test_custom_playlist_path_setting_is_honored(tmp_path: Path) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    custom_path = str(tmp_path / "custom.txt")

    def fake_get_setting(key: str) -> str | None:
        # VID_DL_PLAYLISTS_FILE is the legacy playlist-file key for 1080.
        return custom_path if key == "VID_DL_PLAYLISTS_FILE" else None

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", side_effect=fake_get_setting),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._playlist_buttons[1080].playlist_path == Path(custom_path)


def test_empty_string_setting_falls_back_to_default(tmp_path: Path) -> None:
    """An empty-string override is falsy and must fall back, not render a blank tile."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", return_value=""),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._drop_labels[1080].text() != ""
    assert window._playlist_buttons[1080].text() != ""


def test_drop_label_falls_through_to_env_var_when_setting_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Verify the two-level fallback chain.

    get_setting() returning None must still let the os.getenv fallback inside
    drop_label_for_height() win, before finally reaching the bare preset label.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    monkeypatch.setenv("VID_DL_LABEL_DROP_1080", "From Env")

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", return_value=None),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._drop_labels[1080].text() == "From Env"


def test_drop_label_falls_through_to_preset_label_when_nothing_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    monkeypatch.delenv("VID_DL_LABEL_DROP_1080", raising=False)

    with (
        patch.object(vd, "enabled_heights", return_value=(1080,)),
        patch.object(vd, "get_setting", return_value=None),
    ):
        window._resolution_container = window._build_resolution_container()

    assert window._drop_labels[1080].text() == "1080"


# ---------------------------------------------------------------------------
# Rebuild ordering and idempotency
# ---------------------------------------------------------------------------


def test_reload_settings_rebuild_runs_before_audio_label_apply(tmp_path: Path) -> None:
    """
    Verify rebuild-before-apply ordering.

    A combined settings-apply (resolution change + audio label change in the same
    payload) must land the label on the freshly rebuilt labelAudio, not a stale widget
    that reload_settings is about to delete.
    """
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    stale_audio_label = window.labelAudio

    with (
        patch.object(vd, "enabled_heights", return_value=(2160,)),
        patch.object(vd, "get_setting", return_value=None),
    ):
        window.reload_settings(
            {
                "VID_DL_ENABLED_RESOLUTIONS": "2160",
                "VID_DL_LABEL_DROP_AUDIO": "New Audio Text",
            }
        )

    assert window.labelAudio is not stale_audio_label
    assert window.labelAudio.text() == "New Audio Text"


def test_double_rebuild_in_a_row_does_not_leak_or_raise(tmp_path: Path) -> None:
    """Simulates rapid back-to-back Settings->Apply clicks."""
    vd = import_vid_module()
    window = _make_window(vd, tmp_path)
    _build_grid(vd, window, (1080, 720))
    first_1080 = window._drop_labels[1080]

    with patch.object(vd, "enabled_heights", return_value=(2160, 480)):
        window._populate_resolution_grid()
        window._populate_resolution_grid()  # must not raise on the second call

    remaining = _all_grid_widgets(window._resolution_grid)
    assert first_1080 not in remaining
    # (2 rungs + audio) x (button + tile), not accumulated across the two rebuilds
    assert set(window._drop_labels.keys()) == {2160, 480}
    assert window._resolution_grid.count() == 6


# ---------------------------------------------------------------------------
# request_detected's generalized playlist-comments guard
#
# height_from_source(source) is not None and source.endswith("playlists") replaced
# a hardcoded `source in ["720playlists", "1080playlists"]` check. This guard has no
# other test coverage anywhere in the suite (MyWindow.request_detected is always
# stubbed out in the other GUI test files), so the generalized condition -- and its
# two independent halves -- were unverified.
# ---------------------------------------------------------------------------


def _make_request_detected_window(vd, tmp_path: Path, playlist_comments: dict):
    class _RequestWindow(QWidget):
        request_detected = vd.MyWindow.request_detected
        _create_download_context = vd.MyWindow._create_download_context
        _wire_download_signals = vd.MyWindow._wire_download_signals
        append_properties = vd.MyWindow.append_properties

        def __init__(self) -> None:
            super().__init__()
            self.playlist_comments = dict(playlist_comments)
            self.downloadQueue: Queue = Queue()
            self.barProgress = MagicMock()
            self.checkSkipDownload = MagicMock()
            self.checkSkipDownload.isChecked.return_value = False

        def handle_info_changed(self, d: dict) -> None:
            pass

        def handle_log_entry(self, msg: str) -> None:
            pass

        def _setup_podcast_check(self, urls: list, ydl_opts: dict) -> None:
            # audio_playlists takes this branch instead of downloadQueue.put -- park
            # the built opts where the test can still inspect them.
            self.podcast_ydl_opts = ydl_opts

    window = _RequestWindow()
    # Bypass get_options's playlist-dialog/archive machinery entirely -- this test
    # is only exercising the playlist_comments guard downstream of it.
    window.get_options = lambda _urls, _source: {"format": "mp4"}
    window.podcast_ydl_opts = None
    return window


def _captured_qmeta(window) -> dict:
    """Read back the qmeta dict built by request_detected, from whichever path it took."""
    if window.podcast_ydl_opts is not None:
        return window.podcast_ydl_opts["qmeta"]
    _urls, ydl_opts = window.downloadQueue.get_nowait()
    return ydl_opts["qmeta"]


@pytest.mark.parametrize(
    ("source", "expect_comments_in_qmeta"),
    [
        ("1080playlists", True),  # registered height, playlist source: injected
        ("480playlists", True),  # a newly-supported rung: this is what "generalized" means
        ("1080", False),  # registered height, bare (non-playlist) source: excluded
        ("999playlists", False),  # unregistered height, playlist-shaped: excluded
        ("audio_playlists", False),  # non-resolution source, still ends with "playlists"
    ],
)
def test_request_detected_playlist_comments_guard(
    tmp_path: Path, source: str, expect_comments_in_qmeta: bool
) -> None:
    vd = import_vid_module()
    window = _make_request_detected_window(vd, tmp_path, {"PL123": "a comment"})

    window.request_detected(["https://youtube.com/watch?v=abc&list=PL123"], source)

    qmeta = _captured_qmeta(window)
    assert ("playlist_comments" in qmeta) is expect_comments_in_qmeta


def test_request_detected_empty_playlist_comments_never_injected(tmp_path: Path) -> None:
    """Even a fully-qualifying playlist source must not add an empty comments dict."""
    vd = import_vid_module()
    window = _make_request_detected_window(vd, tmp_path, {})

    window.request_detected(["https://youtube.com/watch?v=abc&list=PL123"], "1080playlists")

    qmeta = _captured_qmeta(window)
    assert "playlist_comments" not in qmeta

"""Main-window layout: resolution tile grid, top buttons and podcast container."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from src.config import (
    LABEL_BTN_PODCASTS,
    LABEL_DROP_AUDIO,
    LABEL_OUTPUT_FONT_NAME,
    LABEL_OUTPUT_FONT_SIZE,
    PLAYLISTS_AUDIO_FILE,
    drop_label_for_height,
    playlist_path_for_height,
)
from src.resolutions import (
    button_label_key,
    drop_label_key,
    get_preset,
    playlist_file_key,
    playlist_source_key,
    source_key,
)
from src.settings_dialog import enabled_heights, get_str_setting
from src.ui_classes import DropLabel, PlaylistButton
from src.window_base import WindowState

# Resolution tiles wrap after this many columns. Three keeps the default
# (1080 + 720 + audio) window exactly as wide as it is today.
_TILE_COLUMNS = 3


def _shrink_with_column(widget: QWidget) -> None:
    """
    Let ``widget`` shrink below its text width so it never sets a column's floor.

    A layout item's minimum width is the widget's own size hint unless its
    horizontal policy is ``Ignored``. Every resolution column stacks a button area
    above a drop target, so the *button areas* -- not the tiles -- decided how
    narrow each column could get, and they disagree: the audio column's area is a
    playlist button plus the fixed 34px podcastIndicator, the widest floor of the
    set. Once seven cells pushed the tile minimum down to 100px that floor stopped
    being redundant, and the leftmost column (where the audio cell lands at
    position 6) refused to shrink with its neighbours. Ignoring the button areas'
    width leaves ``_tile_min_size`` as every column's only floor, so all the drop
    targets stay the same size at every window width.
    """
    widget.setSizePolicy(
        QSizePolicy.Policy.Ignored,
        widget.sizePolicy().verticalPolicy(),
    )


def _tile_min_size(cell_count: int) -> int:
    """Shrink the tiles as more resolutions are enabled so the window stays usable."""
    if cell_count <= 3:
        return 150
    if cell_count <= 6:
        return 120
    return 100


class LayoutMixin(WindowState):
    """UI-building methods of the main window."""

    def _build_podcast_container(self) -> QWidget:
        """Build the podcast button + indicator container widget."""
        self.buttonAudioPlaylists = PlaylistButton(
            LABEL_BTN_PODCASTS,
            str(PLAYLISTS_AUDIO_FILE),
        )
        self.buttonAudioPlaylists.setMaximumWidth(140)
        self.buttonAudioPlaylists.clicked.connect(
            lambda: self.playlist_button_clicked("audio_playlists"),
        )
        self.podcastIndicator = QPushButton("", self)
        self.podcastIndicator.setFixedSize(34, 34)
        self.podcastIndicator.setFlat(True)
        self.podcastIndicator.setStyleSheet(
            "font-size:18px;background:transparent;border:0px",
        )
        self.podcastIndicator.setToolTip("Podcast status")
        self.podcastIndicator.clicked.connect(self._show_podcast_status)
        container = QWidget()
        inner = QGridLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.buttonAudioPlaylists, 0, 0)
        inner.addWidget(self.podcastIndicator, 0, 1)
        container.setLayout(inner)
        # The container is shrunk by _add_grid_cell; the button has to give way
        # inside it too, or the inner layout just clips the indicator instead.
        _shrink_with_column(self.buttonAudioPlaylists)
        return container

    def _build_top_buttons(self) -> QWidget:
        """Build the top-row button strip (pending/failed/update/settings)."""
        self.buttonUpdate = QPushButton("⤓")
        self.buttonUpdate.clicked.connect(lambda: self.request_detected([], "Update"))
        self.buttonUpdate.setVisible(True)
        self.buttonSettings = QPushButton("⚙")
        self.buttonSettings.clicked.connect(self._open_settings)
        self.buttonFailed = QPushButton("⚠")
        self.buttonFailed.setToolTip("Failed downloads — click to view")
        self.buttonFailed.setStyleSheet("color:#d9534f;font-weight:bold;")
        self.buttonFailed.clicked.connect(self._show_failed_downloads)
        self.buttonFailed.setVisible(False)
        self.buttonPending = QPushButton("⏳")
        self.buttonPending.setToolTip("Pending downloads — waiting to become available")
        self.buttonPending.setStyleSheet("color:#8a6d3b;font-weight:bold;")
        self.buttonPending.clicked.connect(self._show_pending_downloads)
        self.buttonPending.setVisible(False)

        top_buttons = QWidget()
        top_inner = QHBoxLayout()
        top_inner.setContentsMargins(0, 0, 0, 0)
        top_inner.addStretch()
        top_inner.addWidget(self.buttonPending)
        top_inner.addWidget(self.buttonFailed)
        top_inner.addWidget(self.buttonUpdate)
        top_inner.addWidget(self.buttonSettings)
        top_buttons.setLayout(top_inner)
        return top_buttons

    def _build_resolution_cell(
        self, height: int, min_size: int, font_size: int
    ) -> tuple[PlaylistButton, DropLabel]:
        """Build one column's playlist button and drop target, unparented."""
        preset = get_preset(height)
        if preset is None:
            msg = f"Unregistered resolution height: {height}"
            raise ValueError(msg)

        btn_label = get_str_setting(button_label_key(height), f"{preset.label} Playlists")
        playlist_path = get_str_setting(
            playlist_file_key(height), str(playlist_path_for_height(height))
        )
        button = PlaylistButton(btn_label, playlist_path)
        # Default-argument capture: a bare closure over `height` would make every
        # button route to the last rung built.
        button.clicked.connect(
            lambda _checked=False, h=height: self.playlist_button_clicked(playlist_source_key(h))
        )
        self._playlist_buttons[height] = button

        drop_text = get_str_setting(drop_label_key(height), drop_label_for_height(height))
        tile = DropLabel(
            drop_text,
            preset.color,
            self.request_detected,
            source_key=source_key(height),
            text_color=preset.text_color,
            min_size=min_size,
            font_size=font_size,
        )
        self._drop_labels[height] = tile

        return button, tile

    def _add_grid_cell(self, position: int, top_widget: QWidget, tile: DropLabel) -> None:
        """
        Place one cell's button area and drop target into the shared grid.

        The button area and the tile go in *separate* grid rows (2r and 2r+1 for
        visual row r) rather than a per-cell QVBoxLayout, so QGridLayout equalizes
        the button-area height and the tile height across every column. That
        matters because the audio column's button area is a container holding the
        fixed 34px podcastIndicator, 10px taller than a bare PlaylistButton: with
        per-cell layouts that extra height pushed the audio drop target down and
        left it shorter than its neighbours.

        The button area also gives up its say over the column's *width* (see
        ``_shrink_with_column``) so that every column's minimum is the tile
        minimum and all the drop targets stay identically sized.
        """
        grid = self._resolution_grid
        visual_row = position // _TILE_COLUMNS
        column = position % _TILE_COLUMNS
        _shrink_with_column(top_widget)
        grid.addWidget(top_widget, visual_row * 2, column)
        grid.addWidget(tile, visual_row * 2 + 1, column)

    def _build_resolution_container(self) -> QWidget:
        """Build the wrapping grid of resolution tiles plus the audio tile."""
        container = QWidget()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        container.setLayout(grid)
        self._resolution_grid = grid
        self._populate_resolution_grid()
        return container

    def _populate_resolution_grid(self) -> None:
        """Fill the resolution grid from the enabled set, replacing whatever is there."""
        grid = self._resolution_grid
        # Tear down first. takeAt(0) detaches the item; setParent(None) removes it
        # from the widget tree immediately so a rebuild in the same event does not
        # briefly show two copies, and deleteLater() frees it once Qt unwinds. Doing
        # only deleteLater() leaves the old tiles visible until the event loop turns.
        while (item := grid.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._drop_labels.clear()
        self._playlist_buttons.clear()

        heights = enabled_heights()
        cell_count = len(heights) + 1  # + the audio cell
        min_size = _tile_min_size(cell_count)
        font_size = max(18, min_size // 5)

        for position, height in enumerate(heights):
            button, tile = self._build_resolution_cell(height, min_size, font_size)
            self._add_grid_cell(position, button, tile)

        # Contrast fix on an existing colour: the orange background was too light
        # for the white text it effectively never had. Background hex unchanged.
        self.labelAudio = DropLabel(
            get_str_setting("VID_DL_LABEL_DROP_AUDIO", LABEL_DROP_AUDIO),
            "#FF9843",
            self.request_detected,
            source_key="audio",
            text_color="#1A1B2E",
            min_size=min_size,
            font_size=font_size,
        )
        self._add_grid_cell(len(heights), self._build_podcast_container(), self.labelAudio)

        for column in range(_TILE_COLUMNS):
            grid.setColumnStretch(column, 1)

    def _setup_ui_layout(self) -> None:
        """Create and arrange all UI widgets and layout."""
        self._drop_labels: dict[int, DropLabel] = {}
        self._playlist_buttons: dict[int, PlaylistButton] = {}

        layout = QGridLayout()
        self.checkIgnoreArchive = QCheckBox("Ignore Archive?")
        self.checkIgnoreArchive.setChecked(False)
        self.checkSkipDownload = QCheckBox("Skip Download")
        self.checkSkipDownload.setChecked(False)
        self.labelOutput = QLabel(self._ready_text)
        self.labelOutput.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.labelOutput.setFont(QFont(LABEL_OUTPUT_FONT_NAME, LABEL_OUTPUT_FONT_SIZE))
        self.barProgress = QProgressBar()
        self.logEdit = QPlainTextEdit()
        self.logEdit.setReadOnly(True)

        layout.addWidget(self.checkSkipDownload, 0, 0)
        layout.addWidget(self.checkIgnoreArchive, 0, 1)
        layout.addWidget(self._build_top_buttons(), 0, 2)
        layout.addWidget(self._build_resolution_container(), 1, 0, 1, 3)
        layout.addWidget(self.labelOutput, 2, 0, 1, 3)
        layout.addWidget(self.barProgress, 3, 0, 1, 3)
        layout.addWidget(self.logEdit, 4, 0, 1, 3)
        layout.setColumnStretch(2, 1)

        self.setLayout(layout)

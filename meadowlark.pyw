"""
MeadowLark - A PyQt6-based GUI application for downloading and managing video and audio content from YouTube and other platforms using yt-dlp.

This application provides a user-friendly interface for batch downloading videos, playlists, and audio files with customizable options. It supports drag-and-drop, playlist selection, progress tracking, and automatic updates for yt-dlp.

Features:
- Drag-and-drop support for video/audio URLs.
- Batch downloading of playlists (any enabled resolution preset, plus audio-only).
- Custom output templates and post-processing (e.g., SponsorBlock, chapter modification).
- Progress bar and real-time log output.
- Optional archive checking to avoid duplicate downloads.
- Automatic detection and login for supported platforms (e.g., Nebula).
- Cookie-based authentication for YouTube.
- Update checker and one-click upgrade for yt-dlp.
- Opens download directory and sets up application icon resources on startup.

Usage:
- Run this script directly to launch the GUI.
- Drag URLs or select playlist options to queue downloads.
- Monitor progress and logs in the main window.
- Use the update button to check for and install yt-dlp updates.

Dependencies:
- Python 3.11+
- PyQt6
- yt-dlp
- hurry.filesize
- Custom modules: QYT, UIClasses

Author: Gene
"""

import contextlib
import logging
import os
import queue
import shutil
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial
from os import startfile
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import yt_dlp
from hurry.filesize import size
from PyQt6.QtCore import (
    QDir,
    QObject,
    QPoint,
    QProcess,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QFont,
    QIcon,
    QKeySequence,
    QSessionManager,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import QYT
import utils
from src.config import (
    ALWAYS_ON_TOP,
    ARCHIVE_PATH,
    FAILED_DOWNLOADS_FILE,
    LABEL_BTN_PODCASTS,
    LABEL_DROP_AUDIO,
    LABEL_OUTPUT_FONT_NAME,
    LABEL_OUTPUT_FONT_SIZE,
    LABEL_READY_TEXT,
    LIVE_QUEUE_CHECK_INTERVAL_MINUTES,
    LIVE_QUEUE_FILE,
    PENDING_QUEUE_FILE,
    PLAYLISTS_AUDIO_FILE,
    PODCAST_AUTO_CHECK,
    PODCAST_STATUS_GEOMETRY_KEY,
    VENV_SCRIPTS_DIR,
    VIDEO_STORAGE_DIR,
    YDL_COMMON_ERRORS,
    YDL_EXTRACTION_ERRORS,
    drop_label_for_height,
    playlist_path_for_height,
)
from src.failed_downloads import (
    add_failed_download,
    load_failed_downloads,
    record_video_id,
    remove_failed_downloads,
)
from src.failed_downloads_dialog import FailedDownloadsDialog
from src.first_run_wizard import FirstRunWizard, needs_first_run
from src.history_dialog import HistoryDialog
from src.match_filter import build_match_filter
from src.pending_check import PendingCheckDeps
from src.pending_check import check_pending_queue as run_pending_check
from src.pending_downloads_dialog import PendingDownloadsDialog
from src.pending_queue import (
    KIND_LIVE,
    KIND_PREMIERE,
    PendingRecord,
    load_pending_queue,
    make_pending_record,
    migrate_legacy_live_queue,
    remove_pending_many,
    save_pending_queue,
    upsert_pending,
)
from src.podcast_filtering import (
    PODCAST_MIN_DURATION_SECONDS,
    append_downloaded_video_ids,
    append_to_archive_and_mark_skipped,
    check_sponsorblock_for_video_id,
    format_timestamp_readable,
    load_downloaded_video_ids,
    parse_scheduled_time_from_error,
    parse_video_id_from_error,
    parse_video_timestamp,
)
from src.podcast_helpers import fetch_latest_accessible_entry
from src.pot_provider import check_pot_provider, start_deno_warmup
from src.progress_smoothing import ProgressSmoother
from src.release_status import (
    is_not_yet_released,
    parse_relative_release,
    to_release_at,
)
from src.resolutions import (
    RESOLUTION_PRESETS,
    button_label_key,
    drop_label_key,
    get_preset,
    height_from_source,
    playlist_file_key,
    playlist_source_key,
    source_key,
)
from src.settings_dialog import (
    SettingsDialog,
    _init_runtime_settings,
    _persist_setting,
    enabled_heights,
    get_setting,
)
from src.url_utils import extract_playlist_id
from src.window_geometry import GeometryMemoryDialog
from src.ydl_options import (
    build_podcast_outtmpl,
    podcast_base_dir,
    resolve_cookiefile,
)
from src.ydl_utils import extract_playlist_info, extract_video_entries
from UIClasses import DropLabel, PlaylistButton, PlaylistDialog

logger = logging.getLogger(__name__)

# Helper for podcast playlist handling -------------------------------------------------
#
# YouTube sometimes reports that the *latest* video in a playlist is a "Private
# video".  Calling ``yt_dlp`` with ``playlistend=1`` will either raise an exception
# containing the words "Private video" or return an info dict whose one entry has a
# title beginning with that string.  In both cases the normal podcast-check logic
# treats that as a hard error and flags the entire playlist as broken.  The desired
# behaviour is instead to ignore the private item and pretend the previous accessible
# video is the latest.  ``_fetch_latest_accessible_entry`` encapsulates that logic so
# it can be exercised by unit tests.
#
# New strategy (2026-02-27): instead of scraping the entire playlist when a
# private video is detected we incrementally request the last N entries, growing
# N by one each attempt.  This avoids a full playlist fetch in the common case of
# a single private stub.  A limit prevents runaway loops.

# how many entries to look ahead before giving up - see src/podcast_helpers.py


# Constants
MAX_INT_PROGRESS = 2147483647
THREAD_QUIT_TIMEOUT_MS = 2000
THREAD_TERMINATE_TIMEOUT_MS = 1000

# First-run size of the Podcast Status window, matching the other list dialogs
# (history, pending, failed). After that the window reopens at whatever size it
# was closed at -- see src/window_geometry.py.
_PODCAST_STATUS_DEFAULT_SIZE = (900, 500)

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


def _make_podcast_status_entry(
    podcast: str,
    url: str,
    status: str = "(unknown)",
    latest_date: str = "(unknown)",
    **kwargs: object,
) -> dict:
    """Create a podcast status entry dict with standard keys and optional extensions."""
    return {
        "podcast": podcast,
        "latest_date": latest_date,
        "status": status,
        "url": url,
        **kwargs,
    }


def _label_from_comments(url: str, audio_pl_comments: dict) -> str | None:
    """
    Return the user-assigned playlist label for ``url``, or ``None`` if unset.

    Resolves offline using only the playlist id, so it is safe to call from
    error paths (e.g. upcoming-premiere extraction failures) where yt-dlp
    ``info`` was never fetched.  Callers supply their own fallback via ``or``.
    """
    pl_id = extract_playlist_id(url)
    if pl_id and pl_id in audio_pl_comments:
        return utils.sanitize_for_path(audio_pl_comments[pl_id])
    return None


class MyWindow(QWidget):
    """
    MyWindow - A PyQt6-based main window for the MeadowLark application, providing a GUI for downloading and managing video and audio content from YouTube and other platforms.

    Features include playlist and audio download options, drag-and-drop support, progress tracking, log display, update checking, and integration with custom download queue and processing logic.
    """

    live_queue_log = pyqtSignal(str)

    _BRAVE_PATHS: ClassVar[list[str]] = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]

    def __init__(self) -> None:
        """
        MeadowLark is a PyQt6-based GUI application for downloading and managing video and audio content from YouTube and other platforms using yt-dlp.

        Provides a user-friendly interface for batch downloading videos, playlists, and audio files with customizable options. Features include drag-and-drop support, playlist selection, progress tracking, real-time logging, archive checking, and automatic yt-dlp updates.

        Run this script to launch the GUI, queue downloads, monitor progress, and manage updates.
        """
        super().__init__()
        self.setWindowTitle("MeadowLark")
        if ALWAYS_ON_TOP:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        _init_runtime_settings()
        self._settings_dialog: SettingsDialog | None = None
        self._ready_text = LABEL_READY_TEXT
        self._progress_smoother = ProgressSmoother()

        self._setup_ui_layout()
        self._pending_dialog: PendingDownloadsDialog | None = None
        self._setup_queue_and_downloader()
        self._setup_timers()
        self._setup_podcast_state()

        self._failed_dialog: FailedDownloadsDialog | None = None
        self._refresh_failed_button()
        self._refresh_pending_button()

        QShortcut(QKeySequence("Ctrl+H"), self).activated.connect(self._show_history)
        QShortcut(QKeySequence("Ctrl+U"), self).activated.connect(
            self._start_app_update_check
        )

        self.live_queue_log.connect(self.handle_log_entry)

        self.playlist_comments = {}

        update_available, _, _ = utils.is_yt_dlp_update_available()
        self.buttonUpdate.setVisible(update_available)

        self._maybe_start_auto_app_update_check()

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

        btn_label = get_setting(button_label_key(height)) or f"{preset.label} Playlists"
        playlist_path = get_setting(playlist_file_key(height)) or str(
            playlist_path_for_height(height)
        )
        button = PlaylistButton(str(btn_label), str(playlist_path))
        # Default-argument capture: a bare closure over `height` would make every
        # button route to the last rung built.
        button.clicked.connect(
            lambda _checked=False, h=height: self.playlist_button_clicked(
                playlist_source_key(h)
            )
        )
        self._playlist_buttons[height] = button

        drop_text = get_setting(drop_label_key(height)) or drop_label_for_height(height)
        tile = DropLabel(
            str(drop_text),
            preset.color,
            self.request_detected,
            source_key=source_key(height),
            text_color=preset.text_color,
            min_size=min_size,
            font_size=font_size,
        )
        self._drop_labels[height] = tile

        return button, tile

    def _add_grid_cell(
        self, position: int, top_widget: QWidget, tile: DropLabel
    ) -> None:
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
            str(get_setting("VID_DL_LABEL_DROP_AUDIO") or LABEL_DROP_AUDIO),
            "#FF9843",
            self.request_detected,
            source_key="audio",
            text_color="#1A1B2E",
            min_size=min_size,
            font_size=font_size,
        )
        self._add_grid_cell(
            len(heights), self._build_podcast_container(), self.labelAudio
        )

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
        self.logEdit = QPlainTextEdit(readOnly=True)

        layout.addWidget(self.checkSkipDownload, 0, 0)
        layout.addWidget(self.checkIgnoreArchive, 0, 1)
        layout.addWidget(self._build_top_buttons(), 0, 2)
        layout.addWidget(self._build_resolution_container(), 1, 0, 1, 3)
        layout.addWidget(self.labelOutput, 2, 0, 1, 3)
        layout.addWidget(self.barProgress, 3, 0, 1, 3)
        layout.addWidget(self.logEdit, 4, 0, 1, 3)
        layout.setColumnStretch(2, 1)

        self.setLayout(layout)

    def _setup_queue_and_downloader(self) -> None:
        """Set up download queue, downloader, and signal connections."""
        self.downloadQueue = queue.Queue()
        self.downloader = QYT.QYTQueue(self.downloadQueue)
        self.downloader.message_changed.connect(self.handle_log_entry)
        self.downloader.queue_empty.connect(self.handle_queue_empty)
        self.downloader.history_entry_added.connect(self._on_history_entry_added)
        self.downloader.download_failed.connect(self._on_download_failed)
        self.downloader.start()

    def _setup_timers(self) -> None:
        """Create and start timers for live queue and podcast checks."""
        # Pending queue setup (live streams + unreleased premieres) and periodic recheck
        self.pending_queue_path = PENDING_QUEUE_FILE
        self.pending_queue_path.parent.mkdir(parents=True, exist_ok=True)
        migrate_legacy_live_queue(LIVE_QUEUE_FILE, self.pending_queue_path)
        self.live_check_timer = QTimer(self)
        self.live_check_timer.setInterval(LIVE_QUEUE_CHECK_INTERVAL_MINUTES * 60 * 1000)
        self.live_check_timer.timeout.connect(self.check_pending_queue)
        self.live_check_timer.start()
        # Do an initial check on startup
        self.check_pending_queue()

        # Schedule hourly automated YT Podcasts check (runs at :15 past the hour)
        if PODCAST_AUTO_CHECK:
            self._schedule_hourly_podcast_checks()

    def _setup_podcast_state(self) -> None:
        """Initialize podcast-related attributes and state."""
        self._podcast_pending_urls: set[str] = set()
        self._last_podcast_check_error = False
        self._podcast_check_running = False
        # Keep worker/thread refs so they don't get GC'd while running
        self._podcast_worker = None
        self._podcast_worker_thread = None
        # Cache the last podcast statuses (populated after each background check)
        self._podcast_last_statuses: list[dict] = []
        # Latest-URL cache: maps playlist_url -> {"latest_url": str, "latest_ts": int|None, "fetched_at": float}
        self._podcast_latest_url_cache: dict[str, dict] = {}
        # track podcasts for which a Download Now request is in progress
        self._podcasts_downloading: set[str] = set()
        # default indicator to unknown/all good
        self._set_podcast_indicator("all_good")

        # Ensure podcast worker threads are shut down on application exit
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._shutdown_podcast_thread)

    def _load_playlist_urls(self, source: str) -> list[dict[str, str]] | None:
        """Load URLs and associated comments from playlist file for the given source, or return None."""
        playlists_path = utils.get_playlist_file_for_source(source)
        if playlists_path:
            try:
                playlist_data = []
                last_comment = None
                with Path(playlists_path).open("r", encoding="utf-8") as file:
                    for raw_line in file:
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("#"):
                            last_comment = line[1:].strip()
                        else:
                            playlist_data.append({"url": line, "comment": last_comment})
                            last_comment = None  # Reset for next URL
            except FileNotFoundError:
                print("File not found.")
                return None
            else:
                return playlist_data
        return None

    def _setup_podcast_check(self, urls: list, ydl_opts: dict) -> None:
        """Set up background podcast check for SponsorBlock info."""
        # If a check is already running, skip this trigger
        if getattr(self, "_podcast_check_running", False):
            self.logEdit.appendPlainText(
                "Podcast check already running; skipping this trigger.",
            )
        else:
            # Indicate check in progress and run off the UI thread
            self._podcast_check_running = True
            self._set_podcast_indicator("checking")
            self.labelOutput.setText(
                "Checking podcasts for SponsorBlock info...",
            )

            # Avoid passing Qt QObject instances (logger/progress hooks) into worker threads.
            worker_ydl_opts = dict(ydl_opts)
            worker_ydl_opts.pop("logger", None)
            worker_ydl_opts.pop("progress_hooks", None)
            worker = self._PodcastCheckWorker(
                self._filter_audio_playlist_urls,
                urls,
                worker_ydl_opts,
            )
            thread = QThread(self)
            thread.setObjectName("PodcastCheckThread")
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            # When the thread finishes, log it and update the status label (connect as separate slots)
            thread.finished.connect(
                lambda: self.logEdit.appendPlainText(
                    "Podcast check thread finished.",
                ),
            )
            thread.finished.connect(
                lambda: self.labelOutput.setText(self._ready_text),
            )

            # Store references to avoid GC while running
            self._podcast_worker = worker
            self._podcast_worker_thread = thread

            def _on_finished(
                to_download: list,
                pending: list,
                had_error: bool,
                messages: list[str],
                statuses: list[str],
            ) -> None:
                # Handle results back on the main thread
                try:
                    self._on_podcast_check_finished(
                        to_download,
                        pending,
                        had_error,
                        ydl_opts,
                        messages,
                        statuses,
                    )
                finally:
                    # Clear stored refs
                    self._podcast_worker = None
                    self._podcast_worker_thread = None

            worker.finished.connect(_on_finished)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            try:
                thread.start()
                # Helpful log for debugging repeated starts
                self.logEdit.appendPlainText(
                    "Started podcast check (background thread).",
                )
            except RuntimeError as e:
                self.logEdit.appendPlainText(
                    f"Failed to start podcast check thread: {e}",
                )
                utils.log_exception(
                    e,
                    "Failed to start podcast check thread",
                )
                self._podcast_check_running = False
                self._set_podcast_indicator("error")

    def _handle_playlist_dialog(
        self, urls: list, source: str
    ) -> tuple[dict, str] | None:
        """
        Handle the range dialog for an individual playlist dropped on a non-playlist target.

        Returns ``(properties, source)``, or ``None`` if the user cancelled. The
        returned source is the caller's: accepting the dialog turns the request
        into a playlist run, which must resolve the ``...playlists`` source
        options -- ``ignoreerrors="only_download"`` so one dead entry does not
        abort the remaining ones, plus the per-playlist output template. Merely
        rebinding the *source* parameter here would be discarded on return.
        """
        if "list=" in urls[0] and "playlist" not in source:
            # extract_playlist_info rather than a bare YoutubeDL: it carries the
            # shared PO-token/JS-runtime wiring every extraction needs, and the
            # cookies a private or unlisted playlist needs to report a count at all.
            info = extract_playlist_info(
                urls[0],
                extra_opts={
                    "extract_flat": "in_playlist",
                    "cookiefile": resolve_cookiefile(),
                },
            )
            playlist_count = info["playlist_count"]
            dialog = PlaylistDialog(playlist_count)
            if dialog.exec():
                playlist_input = dialog.get_playlist_input()
                # a blank return will set no option so default to downloading whole playlist
                properties = {}
                if playlist_input:
                    properties["playlist_items"] = playlist_input
                if source != "audio":
                    source += "playlists"
                return properties, source
            # will cancel playlist download
            return None
        return {}, source

    def playlist_button_clicked(self, source: str) -> None:
        """
        Handle playlist button click with optional confirmation if Ignore Archive is checked.

        Args:
            source (str): The source/playlist type (e.g., "1080playlists", "720playlists", "audio_playlists").
        """
        if self.checkIgnoreArchive.isChecked():
            reply = QMessageBox.warning(
                self,
                "Ignore Archive Enabled",
                "You have 'Ignore Archive?' checked. This will re-download previously downloaded videos.\n\nDo you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return
        self.request_detected([], source)

    def append_properties(self, dictionary: dict, properties: dict) -> dict:
        """Merge properties into dictionary recursively."""
        # Merge properties into dictionary recursively using the shared utility.
        return utils.merge_dicts_recursive(dictionary, properties)

    def request_detected(self, urls: list, source: str) -> None:
        """
        Handle a detected download request by preparing options, updating URLs if needed, and queuing the download task.

        Args:
            urls (list): List of URLs to process.
            source (str): The source type or action (e.g., playlist type or 'Update').

        If the source is 'Update', triggers the update process. Otherwise, prepares yt-dlp options, merges additional properties, and enqueues the download with progress and log handlers.
        """
        if source == "Update":
            self.do_updates()
            return
        qhook, qlogger, ydl_opts = self._create_download_context()

        # Load playlist URLs if applicable
        if not urls:  # Only load from file if no URLs provided (e.g., button press)
            playlist_data = self._load_playlist_urls(source)
            if playlist_data is not None:
                urls = [item["url"] for item in playlist_data]
                self.playlist_comments.clear()
                for item in playlist_data:
                    if item["comment"]:
                        parsed = urlparse(item["url"])
                        query = parse_qs(parsed.query)
                        if "list" in query:
                            pl_id = query["list"][0]
                            self.playlist_comments[pl_id] = item["comment"]

        properties = self.get_options(urls, source)
        if properties:
            ydl_opts = self.append_properties(ydl_opts, properties)
            if ydl_opts:
                # Provide metadata for history logging
                ydl_opts["qmeta"] = {
                    "site": utils.detect_site_from_urls(urls),
                    "type": source,
                }
                # Pass playlist comments for fallback folder naming
                if (
                    height_from_source(source) is not None
                    and source.endswith("playlists")
                    and self.playlist_comments
                ):
                    ydl_opts["qmeta"]["playlist_comments"] = self.playlist_comments

                # Special handling for YT Podcasts: filter new episodes <24h without SponsorBlock
                if source == "audio_playlists":
                    self._setup_podcast_check(urls, ydl_opts)
                else:
                    self.downloadQueue.put((urls, ydl_opts))
                    self._wire_download_signals(qhook, qlogger)
                    self.barProgress.setRange(0, 1)

    def skip_downloading(self, urls: list, source: str) -> None:
        """Skip downloading the given URLs for the source."""
        self.labelOutput.setText("Skipping downloads.")
        qlogger = QYT.QLogger(self.downloadQueue)
        total_added = 0
        # One running set across URLs: a video in two dropped playlists is written once.
        existing_ids = load_downloaded_video_ids(str(ARCHIVE_PATH))
        for url in urls:
            # Use extract_flat="in_playlist" for playlists, True for single videos.
            # extract_video_entries rather than a bare YoutubeDL: it carries the
            # shared PO-token/JS-runtime wiring, and without cookies a private or
            # unlisted playlist enumerates nothing and silently archives nothing.
            entries = extract_video_entries(
                url,
                extract_flat="in_playlist" if "lists" in source else True,
                cookiefile=resolve_cookiefile(),
            )
            added = append_downloaded_video_ids(
                ARCHIVE_PATH, [entry.get("id") for entry in entries], existing_ids
            )
            total_added += len(added)
            for video_id in added:
                # QLogger.debug takes one pre-formatted message, not lazy %-args.
                message = f"Added to archive: youtube {video_id}"
                qlogger.debug(message)
        self.labelOutput.setText("IDs added to archive.")
        self.barProgress.setRange(0, 1)
        self.barProgress.setValue(1)
        self.logEdit.appendPlainText(
            f"Archive-only mode: {total_added} IDs written.",
        )
        self.handle_queue_empty()

    def get_options(
        self,
        urls: list,
        source: str,
        skip_playlist_dialog: bool = False,
    ) -> dict | None:
        """
        Build yt-dlp options dict based on URLs and source type.

        Args:
            urls: List of URLs to process.
            source: The source type (e.g., "1080playlists", "audio").
            skip_playlist_dialog: If True, bypass playlist dialog during live-queue check.

        Returns:
            Dict of yt-dlp options, or None if download should be skipped/cancelled.
        """
        if self.checkSkipDownload.isChecked():
            self.skip_downloading(urls, source)
            return None

        # If a playlist file contained no URLs, bail out early to avoid errors
        if not urls:
            self.logEdit.appendPlainText(f"No URLs found for source: {source}")
            return None

        properties = {}

        # ignore archive checkbox
        if not self.checkIgnoreArchive.isChecked():
            properties["download_archive"] = str(ARCHIVE_PATH)

        # detect if YT
        if "youtube.com" in urls[0]:
            # Use a custom match_filter that records live videos for later
            properties["match_filter"] = self.make_match_filter(source)

        # strip out unnecessary parts of URL if dropping from Watch Later
        urls = [url.split("&list=WL")[0] for url in urls]

        if not skip_playlist_dialog:
            # Handle individual playlist dialog. Accepting it promotes the source
            # to its playlist variant, which is what makes get_source_options
            # below hand back the playlist options rather than the single-video
            # ones -- see _handle_playlist_dialog.
            dialog_result = self._handle_playlist_dialog(urls, source)
            if dialog_result is None:
                return None  # cancelled
            playlist_props, source = dialog_result
            properties.update(playlist_props)

        # Get source-specific options
        source_props = utils.get_source_options(source)
        properties.update(source_props)

        return properties

    def handle_log_entry(self, entry: str) -> None:
        """
        Append a log entry to the log display and updates the output label if a merge operation is detected.

        Args:
            entry: The log message to display.
        """
        if "[Merger]" in entry:
            self.labelOutput.setText("Merging! This can take a while...")
        self.logEdit.appendPlainText(entry)

    def handle_info_changed(self, d: dict) -> None:
        """
        Update the progress bar and output label with smoothed download status.

        Speed, ETA, and the total size are rolling-averaged by
        ``src.progress_smoothing.ProgressSmoother``; the downloaded byte count is
        exact and only clamped non-decreasing. Repaints are throttled, so this
        returns without touching the widgets on most calls.

        Args:
            d (dict): Dictionary containing download progress information.
        """
        status = d.get("status")
        if status == "finished":
            self._progress_smoother.mark_file_finished(d)
            return
        if status != "downloading":
            return
        smoothed = self._progress_smoother.update(d)
        if smoothed is None:
            return

        total = smoothed.total
        speed = smoothed.speed or 0
        output = (
            f"{size(smoothed.downloaded)} of {size(total or 0)} at {size(int(speed))}/s"
        )
        if smoothed.eta is not None:
            output += f" | ETA: {timedelta(seconds=round(smoothed.eta))}"
        self.labelOutput.setText(output)

        if total:
            if total > MAX_INT_PROGRESS:
                self.barProgress.setMaximum(MAX_INT_PROGRESS)
                self.barProgress.setValue(
                    int(smoothed.downloaded / total * MAX_INT_PROGRESS),
                )
            else:
                self.barProgress.setMaximum(total)
                self.barProgress.setValue(smoothed.downloaded)

    def handle_queue_empty(self) -> None:
        """Update the output label to indicate that the download queue is ready and update podcast indicator if applicable."""
        self._progress_smoother.reset()
        self.barProgress.setRange(0, 1)
        self.barProgress.setValue(0)
        self.labelOutput.setText(self._ready_text)
        # If podcast downloads finished, reflect pending/all_good state
        if self._podcast_pending_urls:
            self._set_podcast_indicator("pending")
        # Only set to all_good if not currently running
        elif not getattr(self, "_podcast_check_running", False):
            # If previous check had an error, show error
            if getattr(self, "_last_podcast_check_error", False):
                self._set_podcast_indicator("error")
            else:
                self._set_podcast_indicator("all_good")

        # mark any podcasts that were downloading as completed
        if self._podcasts_downloading:
            if getattr(self, "_podcast_last_statuses", None):
                for s in self._podcast_last_statuses:
                    if s.get("url") in self._podcasts_downloading:
                        s["status"] = "Downloaded"
                # refresh table UI if visible
                with contextlib.suppress(Exception):
                    self._refresh_podcast_status_dialog()
            # clear the set now that we've updated statuses
            self._podcasts_downloading.clear()

        # Cleanup any stored qhook/logger refs to allow GC now that downloads finished
        if hasattr(self, "_active_qhooks"):
            self._active_qhooks.clear()

    class _AppUpdateWorker(QObject):
        """Worker that checks GitHub Releases for a newer app version off the GUI thread."""

        finished = pyqtSignal(
            bool, str, str
        )  # (update_available, latest_tag, download_url)

        def run(self) -> None:
            update_available, tag, url = utils.is_app_update_available()
            self.finished.emit(bool(update_available), tag or "", url or "")

    class _PodcastCheckWorker(QObject):
        """Worker that runs podcast playlist expansion and SponsorBlock checks off the GUI thread."""

        # finished: to_download, pending, had_error, messages, statuses
        finished = pyqtSignal(list, list, bool, list, list)

        def __init__(self, func: Callable, urls: list, ydl_opts: dict) -> None:
            super().__init__()
            self.func = func
            self.urls = urls
            self.ydl_opts = ydl_opts
            # Best-effort cancellation flag (worker cooperatively checks this)
            self._stop_requested = False

        def request_stop(self) -> None:
            """Signal the worker to attempt to stop (best-effort)."""
            self._stop_requested = True

        def run(self) -> None:
            errors: list[str] = []
            statuses: list[dict] = []
            to_download, pending, had_error = [], [], False
            try:
                # Honor stop requests before starting heavy work
                if self._stop_requested:
                    errors.append(
                        "Podcast check aborted before start (stop requested).",
                    )
                    to_download, pending, had_error = [], [], True
                else:
                    result = self.func(self.urls, self.ydl_opts)
                    # Support multiple return shapes:
                    # - (to_download, pending, had_error)
                    # - (to_download, pending, had_error, messages)
                    # - (to_download, pending, had_error, messages, statuses)
                    if isinstance(result, tuple):
                        if len(result) == 5:
                            to_download, pending, had_error, errors, statuses = result
                        elif len(result) == 4:
                            to_download, pending, had_error, errors = result
                        else:
                            to_download, pending, had_error = result
                    else:
                        to_download, pending, had_error = result
                    # If a stop was requested while running, treat as aborted
                    if self._stop_requested:
                        errors.append("Podcast check aborted (stop requested).")
                        to_download, pending, had_error = [], [], True
            except Exception as exc:
                to_download, pending, had_error = [], [], True
                errors.append(f"Podcast check worker exception: {exc}")
                utils.log_exception(exc, "Podcast check worker exception")
            # Emit results back to the main thread; the main thread will perform any GUI logging.
            with contextlib.suppress(RuntimeError):
                self.finished.emit(to_download, pending, had_error, errors, statuses)
            # If the main thread or receiver has gone away, swallow to avoid crashing

    # --- Live queue management ---
    def make_match_filter(self, source: str, label: str | None = None) -> Callable:
        """
        Build a match_filter that skips live/upcoming videos and queues them.

        Args:
            source: The download source identifier.
            label: Destination folder label the skipped item was bound for, so
                ``check_pending_queue`` can restore it once the stream ends.
        """
        return build_match_filter(
            source,
            add_to_queue_fn=partial(self.add_to_live_queue, label=label),
            log_fn=self.live_queue_log.emit,
        )

    def load_pending_queue(self) -> list[PendingRecord]:
        """Load parked downloads (live streams and unreleased premieres)."""
        return load_pending_queue(self.pending_queue_path)

    def save_pending_queue(self, records: list[PendingRecord]) -> None:
        """Save the parked-download records to the store."""
        save_pending_queue(self.pending_queue_path, records)

    def _create_download_context(self) -> tuple[QYT.QHook, QYT.QLogger, dict]:
        """Create a fresh QHook, QLogger, and base ydl_opts dict."""
        qhook = QYT.QHook()
        qlogger = QYT.QLogger(self.downloadQueue)
        ydl_opts = utils.build_base_ydl_opts(qlogger, qhook)
        return qhook, qlogger, ydl_opts

    def _fork_download_context(
        self,
        base_opts: dict,
    ) -> tuple[QYT.QHook, QYT.QLogger, dict]:
        """Create a fresh QHook/QLogger and return a copy of base_opts with them wired in."""
        qhook = QYT.QHook()
        qlogger = QYT.QLogger(self.downloadQueue)
        opts = dict(base_opts)
        opts["logger"] = qlogger
        opts["progress_hooks"] = [qhook]
        return qhook, qlogger, opts

    def _wire_download_signals(self, qhook: QYT.QHook, qlogger: QYT.QLogger) -> None:
        """Connect qhook/qlogger signals to the main window handler slots."""
        qhook.info_changed.connect(self.handle_info_changed)
        qlogger.message_changed.connect(self.handle_log_entry)

    def add_to_live_queue(
        self,
        url: str,
        source: str,
        playlist_id: str | None = None,
        label: str | None = None,
    ) -> None:
        """Park a live/upcoming item. Signature is fixed by build_match_filter's callback."""
        upsert_pending(
            self.pending_queue_path,
            make_pending_record(
                url, source, playlist_id=playlist_id, label=label, kind=KIND_LIVE
            ),
        )

    def _pending_deps(self) -> PendingCheckDeps:
        """Bind this window's callbacks to the shared pending-queue poll loop."""
        return PendingCheckDeps(
            path=self.pending_queue_path,
            cookiefile=resolve_cookiefile(),
            get_options=lambda urls, source: self.get_options(
                urls, source, skip_playlist_dialog=True
            ),
            append_properties=self.append_properties,
            create_context=self._create_download_context,
            wire_signals=self._wire_download_signals,
            enqueue=lambda urls, opts: self.downloadQueue.put((urls, opts)),
            log=self.logEdit.appendPlainText,
            set_progress_range=self.barProgress.setRange,
            detect_site=utils.detect_site_from_urls,
            load_playlist_comments=utils.load_playlist_comments_for_source,
            ydl_class=yt_dlp.YoutubeDL,
        )

    def check_pending_queue(self) -> list[PendingRecord]:
        """Poll parked downloads, queue any that became available, refresh the UI."""
        remaining = run_pending_check(self._pending_deps())
        self._refresh_pending_button(remaining)
        return remaining

    def _episode_already_archived(
        self,
        vid: str,
        existing_ids: set[str],
        status_entry: dict,
    ) -> bool:
        if vid in existing_ids:
            status_entry["status"] = "Downloaded"
            return True
        return False

    def _skip_if_update_episode(
        self,
        entry: dict,
        vid: str,
        webpage: str,
        archive_path: str | None,
        existing_ids: set[str],
        messages: list[str],
        status_entry: dict,
    ) -> bool:
        title = entry.get("title", "") or ""
        if "(Update)" not in title:
            return False
        append_to_archive_and_mark_skipped(
            archive_path,
            vid,
            existing_ids,
            title,
            messages,
        )
        status_entry["status"] = "Skipped (Update)"
        QYT.HistoryLogger().log_skip(
            site=utils.detect_site_from_urls([webpage]),
            dtype="audio_playlists",
            title=title,
            reason="Update exception",
        )
        return True

    def _skip_if_short_duration(
        self,
        entry: dict,
        vid: str,
        webpage: str,
        archive_path: str | None,
        existing_ids: set[str],
        messages: list[str],
        status_entry: dict,
    ) -> bool:
        duration = entry.get("duration")
        title = entry.get("title", "") or ""
        if duration is None or duration >= PODCAST_MIN_DURATION_SECONDS:
            return False
        append_to_archive_and_mark_skipped(
            archive_path,
            vid,
            existing_ids,
            title,
            messages,
            reason="Short duration (<3 min)",
        )
        status_entry["status"] = "Skipped Short"
        QYT.HistoryLogger().log_skip(
            site=utils.detect_site_from_urls([webpage]),
            dtype="audio_playlists",
            title=title,
            reason="Short duration (<3 min)",
        )
        return True

    def _classify_episode_by_age(
        self,
        vid: str,
        webpage: str,
        ts: float | None,
        now_ts: float,
        playlist_label: str,
        to_download: list,
        pending: list,
        status_entry: dict,
        *,
        bypass_sponsorblock_wait: bool = False,
    ) -> None:
        obj = {"url": webpage, "playlist": playlist_label}
        if ts is None:
            to_download.append(obj)
            status_entry["status"] = "Ready"
            return
        if ts > now_ts:
            status_entry["status"] = "Upcoming"
            status_entry["recheck_ts"] = ts
            return
        age_seconds = now_ts - ts
        if bypass_sponsorblock_wait or age_seconds >= 24 * 60 * 60:
            to_download.append(obj)
            status_entry["status"] = "Ready"
            return
        site = utils.detect_site_from_urls([webpage])
        if site != "youtube" or check_sponsorblock_for_video_id(vid):
            to_download.append(obj)
            status_entry["status"] = "Ready"
        else:
            pending.append(obj)
            status_entry["status"] = "Pending SponsorBlock"

    def _filter_audio_playlist_urls(  # noqa: PLR0912, PLR0915
        self,
        urls: list,
        ydl_opts: dict,
        *,
        bypass_sponsorblock_wait: bool = False,
    ) -> tuple[list, list, bool, list, list]:
        """
        Expand playlist URLs and return enriched objects with per-episode URL and resolved playlist label.

        Returns: (to_download_objs, pending_objs, had_error, messages, statuses)
        where each obj is {"url": <video_url>, "playlist": <playlist_label>}.

        Only the most recent item in each playlist is inspected to keep work
        minimal.  If the latest entry is a private video, this used to cause a
        hard error and mark the entire podcast as broken; we now detect that
        case, skip the private entry and pretend the previous accessible video
        is the latest.  ``messages`` will include an informational note and
        ``had_error`` remains False when skipping a private item.

        pending_urls is a
        list of video URLs for which SponsorBlock info was not yet present. had_error will be True
        if any errors occurred during expansion. messages is a list of human-readable strings to
        be logged from the main thread. statuses is a list of dicts with {podcast, latest_date, status, url}.
        """
        to_download: list[dict] = []
        pending: list[dict] = []
        had_error = False
        messages: list[str] = []
        statuses: list[dict] = []
        archive_path = ydl_opts.get("download_archive")
        existing_ids: set[str] = load_downloaded_video_ids(archive_path)
        now_ts = datetime.now(tz=UTC).timestamp()
        audio_pl_comments = utils.load_playlist_comments_for_source("audio_playlists")

        for url in urls:
            try:
                entries, skipped, info = fetch_latest_accessible_entry(url)
                if skipped:
                    messages.append(
                        f"Latest episode for podcast {url} is private - using previous accessible video",
                    )
                playlist_label = _label_from_comments(
                    url, audio_pl_comments
                ) or utils.resolve_playlist_label(info, url)
                status_entry = _make_podcast_status_entry(playlist_label, url)

                vid: str | None = None
                for entry in entries:
                    vid = entry.get("id") or entry.get("url")
                    webpage = entry.get("webpage_url") or entry.get("url")
                    if not vid or not webpage:
                        continue
                    ts = parse_video_timestamp(entry)
                    status_entry["latest_url"] = webpage
                    status_entry["latest_ts"] = ts
                    status_entry["latest_date"] = format_timestamp_readable(ts)

                    if self._episode_already_archived(vid, existing_ids, status_entry):
                        break
                    if self._skip_if_update_episode(
                        entry,
                        vid,
                        webpage,
                        archive_path,
                        existing_ids,
                        messages,
                        status_entry,
                    ):
                        break
                    if self._skip_if_short_duration(
                        entry,
                        vid,
                        webpage,
                        archive_path,
                        existing_ids,
                        messages,
                        status_entry,
                    ):
                        break

                    self._classify_episode_by_age(
                        vid,
                        webpage,
                        ts,
                        now_ts,
                        playlist_label,
                        to_download,
                        pending,
                        status_entry,
                        bypass_sponsorblock_wait=bypass_sponsorblock_wait,
                    )
                    break

                self._cache_put(
                    url,
                    status_entry.get("latest_url"),
                    status_entry.get("latest_ts"),
                )
                statuses.append(status_entry)
            except YDL_EXTRACTION_ERRORS as e:
                utils.log_exception(e, f"Error expanding playlist/url {url}")
                errstr = str(e)
                scheduled_ts = parse_scheduled_time_from_error(errstr)
                if scheduled_ts:
                    vid = parse_video_id_from_error(errstr)
                    extra: dict[str, object] = {"recheck_ts": scheduled_ts}
                    if vid:
                        extra["latest_url"] = f"https://www.youtube.com/watch?v={vid}"
                        extra["latest_ts"] = scheduled_ts
                    statuses.append(
                        _make_podcast_status_entry(
                            _label_from_comments(url, audio_pl_comments) or url,
                            url,
                            status="Upcoming",
                            latest_date="(scheduled)",
                            **extra,
                        ),
                    )
                    messages.append(
                        f"Podcast {url} scheduled; will recheck at {datetime.fromtimestamp(scheduled_ts).astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
                    )
                else:
                    had_error = True
                    messages.append(f"Error expanding playlist/url {url}: {e}")
                    statuses.append(
                        _make_podcast_status_entry(
                            _label_from_comments(url, audio_pl_comments) or url,
                            url,
                            status=f"Error: {e}",
                            latest_date="(error)",
                        ),
                    )

        return to_download, pending, had_error, messages, statuses

    def _set_podcast_indicator(self, state: str) -> None:
        """
        Set the podcast indicator button visual state.

        state: one of 'checking', 'busy', 'pending', 'all_good', 'error'
        """
        states = {
            "checking": ("⏳", "Checking podcasts..."),
            "busy": ("🔄", "Downloads queued/active for podcasts"),
            "pending": ("⏳", "Pending SponsorBlock info for some episodes"),
            "all_good": ("✅", "All podcasts up to date"),
            "error": ("⚠️", "Error while checking podcasts"),
        }
        symbol, tip = states.get(state, ("❔", "Unknown podcast status"))
        self.podcastIndicator.setText(symbol)
        self.podcastIndicator.setToolTip(tip)

    def _create_podcast_status_table(self, statuses: list[dict]) -> QTableWidget:
        """Create and populate a podcast status table widget."""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Podcast", "Latest Episode", "Status"])
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch,
        )
        table.setRowCount(len(statuses))
        for i, s in enumerate(statuses):
            table.setItem(i, 0, QTableWidgetItem(s.get("podcast") or "(unknown)"))
            table.setItem(
                i,
                1,
                QTableWidgetItem(s.get("latest_date") or "(unknown)"),
            )
            table.setItem(i, 2, QTableWidgetItem(s.get("status") or "(unknown)"))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            self._on_podcast_status_context_menu,
        )
        return table

    def _show_podcast_status(self) -> None:
        """
        Open a non-blocking dialog showing the last known podcast statuses.

        This uses cached data from the last background check and will NOT trigger a re-check.
        If a dialog is already open, bring it to the front instead of creating a new one.
        """
        # Reuse existing dialog if open
        existing = getattr(self, "_podcast_status_dialog", None)
        if existing and getattr(existing, "isVisible", lambda: False)():
            try:
                existing.raise_()
                existing.activateWindow()
            except (RuntimeError, AttributeError) as exc:
                utils.log_exception(
                    exc,
                    "Failed to focus existing podcast status dialog",
                )
            return

        dialog = GeometryMemoryDialog(
            self,
            PODCAST_STATUS_GEOMETRY_KEY,
            _PODCAST_STATUS_DEFAULT_SIZE,
        )
        dialog.setWindowTitle("Podcast Status")
        # Destroy on close rather than hide: the reuse check above is a visibility
        # test, so a merely-hidden dialog would be rebuilt on every reopen while the
        # old one lived on as a child of this window until app teardown.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout()

        if not self._podcast_last_statuses:
            lbl = QLabel(
                "No cached podcast status available.\nPress 'YT Podcasts' to refresh.",
            )
            layout.addWidget(lbl)
            self._podcast_status_table = None
        else:
            table = self._create_podcast_status_table(self._podcast_last_statuses)
            layout.addWidget(table)
            self._podcast_status_table = table

        dialog.setLayout(layout)
        dialog.setModal(False)  # non-blocking
        dialog.show()
        # Keep a reference so it doesn't get GC'd immediately and allow refreshes
        self._podcast_status_dialog = dialog
        # Ensure we clear our reference when the dialog is destroyed
        dialog.destroyed.connect(self._on_podcast_status_dialog_destroyed)

    def _on_podcast_status_dialog_destroyed(self) -> None:
        """Clear stored references when the status dialog is destroyed."""
        self._podcast_status_dialog = None
        self._podcast_status_table = None

    def _refresh_failed_button(self) -> None:
        """Sync the warning button's count and visibility with the store."""
        count = len(load_failed_downloads(FAILED_DOWNLOADS_FILE))
        self.buttonFailed.setText(f"⚠ {count}")
        self.buttonFailed.setVisible(count > 0)

    def _refresh_pending_button(
        self, records: list[PendingRecord] | None = None
    ) -> None:
        """Sync the pending button's count and visibility with the store."""
        if records is None:
            records = load_pending_queue(self.pending_queue_path)
        self.buttonPending.setText(f"⏳ {len(records)}")
        self.buttonPending.setVisible(bool(records))
        if self._pending_dialog is not None:
            self._pending_dialog.set_records(records)

    def _show_pending_downloads(self) -> None:
        """Open (or refocus) the non-blocking pending-downloads dialog."""
        existing = self._pending_dialog
        if existing is not None:
            try:
                # Closing the dialog only hides it (no WA_DeleteOnClose), so a cached
                # instance may be alive but invisible; show() is what makes the button
                # work on every click, not just the first.
                existing.show()
                existing.raise_()
                existing.activateWindow()
            except RuntimeError as exc:
                utils.log_exception(exc, "Failed to focus pending-downloads dialog")
            else:
                return

        dialog = PendingDownloadsDialog(
            load_pending_queue(self.pending_queue_path), self
        )
        dialog.download_now_requested.connect(self._download_pending_now)
        dialog.remove_requested.connect(self._remove_pending_downloads)
        dialog.destroyed.connect(self._on_pending_dialog_destroyed)
        dialog.show()
        self._pending_dialog = dialog

    def _on_pending_dialog_destroyed(self) -> None:
        self._pending_dialog = None

    def _remove_pending_downloads(self, urls: list[str]) -> None:
        """Drop parked downloads in one write and refresh the button and dialog."""
        self._refresh_pending_button(remove_pending_many(self.pending_queue_path, urls))

    def _download_pending_now(self, records: list[dict]) -> None:
        """Force parked downloads through the normal pipeline, ignoring their release time."""
        runnable = [r for r in records if r.get("url")]
        if not runnable:
            return
        # Remove-first mirrors _retry_failed_downloads: if one is still unreleased the
        # failure path re-parks it with a fresh release time, and a success leaves the
        # pending list clean.
        self._remove_pending_downloads([r["url"] for r in runnable])
        # enabled_heights() never returns an empty tuple (falls back to the default
        # pair), so [0] is safe. Falling back to the highest enabled rung rather than
        # a literal "1080" avoids silently dropping to 1080 for a user who only
        # enabled 2160.
        fallback_source = source_key(enabled_heights()[0])
        for record in runnable:
            url = record["url"]
            self.handle_log_entry(
                f"Downloading pending item now: {record.get('title') or url}"
            )
            self.request_detected([url], record.get("source") or fallback_source)

    def _on_download_failed(self, record: dict) -> None:
        """Persist a failure reported by the download thread (GUI-thread slot)."""
        if is_not_yet_released(record.get("error")):
            self._park_not_yet_released(record)
            return
        try:
            records = add_failed_download(FAILED_DOWNLOADS_FILE, record)
            self._refresh_failed_button()
            if self._failed_dialog is not None:
                self._failed_dialog.set_records(records)
        except OSError as exc:
            # A broken store file must never take down the slot.
            utils.log_exception(exc, "Failed to persist failed download")

    def _park_not_yet_released(self, record: dict) -> None:
        """
        Park an announced-but-unaired item instead of filing it as a failure.

        yt-dlp raises "Premieres in 6 hours" out of extraction before any info_dict
        exists, so match_filter never sees the video and the error is the only signal
        we get. The relative time parsed from that message is coarse; the immediate
        check_pending_queue() below replaces it with the exact release_timestamp (and
        downloads straight away if the premiere already aired).
        """
        urls = record.get("urls") or []
        url = urls[0] if urls else record.get("key")
        if not url:
            return
        error = record.get("error") or ""
        try:
            upsert_pending(
                self.pending_queue_path,
                make_pending_record(
                    url,
                    record.get("source") or "unknown",
                    kind=KIND_PREMIERE,
                    title=record.get("title") or url,
                    release_at=to_release_at(parse_relative_release(error)),
                ),
            )
        except OSError as exc:
            utils.log_exception(exc, "Failed to park not-yet-released download")
            return
        self.handle_log_entry(f"Not released yet, parked: {record.get('title') or url}")
        self.check_pending_queue()

    def _show_failed_downloads(self) -> None:
        """Open (or refocus) the non-blocking failed-downloads dialog."""
        existing = self._failed_dialog
        if existing is not None:
            try:
                # Closing the dialog only hides it (no WA_DeleteOnClose), so a
                # cached instance may be alive but invisible; show() is what
                # makes the button work on every click, not just the first.
                existing.show()
                existing.raise_()
                existing.activateWindow()
            except RuntimeError as exc:
                utils.log_exception(exc, "Failed to focus failed-downloads dialog")
            else:
                return

        dialog = FailedDownloadsDialog(
            load_failed_downloads(FAILED_DOWNLOADS_FILE), self
        )
        dialog.retry_requested.connect(self._retry_failed_downloads)
        dialog.mark_downloaded_requested.connect(self._mark_failed_downloaded)
        dialog.delete_requested.connect(self._delete_failed_downloads)
        dialog.destroyed.connect(self._on_failed_dialog_destroyed)
        dialog.show()
        self._failed_dialog = dialog

    def _on_failed_dialog_destroyed(self) -> None:
        self._failed_dialog = None

    def _delete_failed_downloads(self, keys: list[str]) -> None:
        """Drop records from the store in one write and refresh the button and dialog."""
        records = remove_failed_downloads(FAILED_DOWNLOADS_FILE, keys)
        self._refresh_failed_button()
        if self._failed_dialog is not None:
            self._failed_dialog.set_records(records)

    def _retry_failed_downloads(self, records: list[dict]) -> None:
        """Re-queue failed downloads through the normal download pipeline."""
        # request_detected treats an empty url list as "run this source's whole
        # playlist file", so a malformed record must never reach it.
        runnable = [
            r
            for r in records
            if isinstance(r.get("urls"), list) and r["urls"] and isinstance(r.get("source"), str)
        ]
        if not runnable:
            return
        # Remove-first is deliberate: a repeat failure re-adds the record with a
        # fresh timestamp via download_failed; a success leaves the list clean.
        self._delete_failed_downloads([r.get("key") for r in runnable])
        for record in runnable:
            self.handle_log_entry(
                f"Retrying failed download: {record.get('title') or record['urls'][0]}"
            )
            self.request_detected(list(record["urls"]), record["source"])

    def _mark_failed_downloaded(self, records: list[dict]) -> None:
        """
        Add each failed video's ID to the archive and drop those records from the list.

        Writing to the same archive yt-dlp's download_archive option checks is
        what keeps a permanently-broken (private/deleted) playlist entry from
        being re-reported as failed on every future playlist scan.
        """
        marked = [(r, vid) for r in records if (vid := record_video_id(r)) is not None]
        if not marked:
            return
        try:
            # The helper dedupes too: two records can share one video id.
            append_downloaded_video_ids(ARCHIVE_PATH, [vid for _, vid in marked])
        except OSError as exc:
            utils.log_exception(exc, "Failed to add videos to archive from Failed Downloads")
            return
        self._delete_failed_downloads([r.get("key") for r, _ in marked])
        for record, vid in marked:
            self.handle_log_entry(f"Marked as downloaded: {record.get('title') or vid}")

    def _show_history(self) -> None:
        """Open a non-blocking dialog showing download history (Ctrl+H)."""
        existing = getattr(self, "_history_dialog", None)
        if existing and getattr(existing, "isVisible", lambda: False)():
            try:
                existing.raise_()
                existing.activateWindow()
            except (RuntimeError, AttributeError) as exc:
                utils.log_exception(exc, "Failed to focus existing history dialog")
            return

        dialog = HistoryDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(self._on_history_dialog_destroyed)
        dialog.show()
        self._history_dialog = dialog

    def _on_history_dialog_destroyed(self) -> None:
        self._history_dialog = None

    def _on_history_entry_added(self, record: dict) -> None:
        dialog = getattr(self, "_history_dialog", None)
        if dialog and dialog.isVisible():
            dialog.prepend_row(record)

    # Cache TTL: 6 hours
    CACHE_TTL_SECONDS = 6 * 60 * 60

    def _cache_put(
        self,
        playlist_url: str,
        latest_url: str | None,
        latest_ts: int | None,
    ) -> None:
        """Store or update a cache entry for a podcast's latest URL."""
        if not playlist_url or not latest_url:
            return
        self._podcast_latest_url_cache[playlist_url] = {
            "latest_url": latest_url,
            "latest_ts": latest_ts,
            "fetched_at": time.time(),
        }

    def _cache_get_fresh(self, playlist_url: str) -> str | None:
        """Retrieve cached latest URL if present and not stale (within TTL)."""
        entry = self._podcast_latest_url_cache.get(playlist_url)
        if not entry:
            return None
        # Check time-based TTL
        if (time.time() - entry.get("fetched_at", 0)) > self.CACHE_TTL_SECONDS:
            return None
        return entry.get("latest_url")

    def _on_podcast_status_context_menu(self, pos: QPoint) -> None:
        """Handle right-click context menu on Podcast Status table."""
        table = getattr(self, "_podcast_status_table", None)
        if not table:
            return
        index = table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        menu = QMenu(table)

        action_open = menu.addAction("Open Latest Video in Browser")
        action_open.triggered.connect(
            lambda: self._guarded_status_action(
                self._open_latest_for_row,
                row,
                error_label="open latest video",
            ),
        )

        # Download Now bypasses the SponsorBlock wait
        action_download = menu.addAction("Download Now")
        action_download.triggered.connect(
            lambda: self._guarded_status_action(
                self._download_podcast_now_action,
                row,
                error_label="start download now",
            ),
        )

        menu.exec(table.viewport().mapToGlobal(pos))

    def _guarded_status_action(
        self,
        action: Callable[[int], None],
        row: int,
        *,
        error_label: str,
    ) -> None:
        """
        Run a status-table action, logging any error instead of crashing.

        Unhandled exceptions raised inside a Qt slot terminate the whole
        process, so this boundary keeps a failed menu action from taking the
        app down silently.
        """
        try:
            action(row)
        except Exception as exc:
            self.logEdit.appendPlainText(f"Failed to {error_label}: {exc}")
            utils.log_exception(exc, f"Error in status menu action: {error_label}")

    def _open_latest_for_row(self, row: int) -> None:
        """Open the latest episode for the podcast at ``row`` in a browser."""
        statuses = getattr(self, "_podcast_last_statuses", [])
        if not (0 <= row < len(statuses)):
            return
        st = statuses[row]
        playlist_url = st.get("url")
        label = st.get("podcast")
        # Prefer status-provided latest_url (from cache populated by status generation)
        latest_url = st.get("latest_url")
        if not latest_url and playlist_url:
            latest_url = self._cache_get_fresh(playlist_url)
        if latest_url:
            self._open_url_in_browser(latest_url, label)
            return
        # Fallback: resolve on-demand and cache (skip if we have no playlist URL)
        resolved = (
            self._resolve_latest_via_ytdlp(playlist_url) if playlist_url else None
        )
        if resolved:
            self._cache_put(playlist_url, resolved["url"], resolved.get("ts"))
            self._open_url_in_browser(resolved["url"], label)
        else:
            self.logEdit.appendPlainText(
                f"Could not resolve latest episode for {label or playlist_url}",
            )
            if playlist_url:
                self.logEdit.appendPlainText(
                    f"Opening podcast page for {label or playlist_url} instead",
                )
                self._open_url_in_browser(playlist_url, label)

    def _resolve_latest_via_ytdlp(self, playlist_url: str) -> dict | None:
        """
        Use yt-dlp to resolve the latest episode URL from a playlist.

        Returns dict with {"url": webpage_url, "ts": timestamp} or None on error.
        """
        try:
            info = extract_playlist_info(playlist_url, playlistend=1)
            if not info:
                return None
            entries = info.get("entries", [info])
            # Upcoming/unavailable items can surface as a None entry (or the
            # whole list may be empty); skip those instead of dereferencing None.
            latest = next((e for e in entries if e), None)
            if not latest:
                return None
            webpage = latest.get("webpage_url") or latest.get("url")
            ts = latest.get("timestamp")
            if webpage:
                return {"url": webpage, "ts": ts}
            return None
        except YDL_COMMON_ERRORS as exc:
            utils.log_exception(
                exc,
                f"Failed to resolve latest episode via yt-dlp for {playlist_url}",
            )
            # Upcoming premieres fail extraction but name the video in the error
            # string; recover the watch URL so "Open Latest Video" still works.
            vid = parse_video_id_from_error(str(exc))
            if vid:
                return {
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "ts": parse_scheduled_time_from_error(str(exc)),
                }
            return None

    def _try_open_default_browser(self, url: str, label: str | None) -> bool:
        """Try to open URL in the system default browser. Returns True on success."""
        try:
            if webbrowser.open_new_tab(url):
                self.logEdit.appendPlainText(
                    f"Opened latest for {label or url} in default browser",
                )
                return True
        except (webbrowser.Error, OSError) as exc:
            utils.log_exception(exc, "Failed to open URL in default browser")
        return False

    def _get_brave_controller(self) -> webbrowser.BaseBrowser | None:
        """Return a Brave browser controller, registering it from disk if needed."""
        try:
            return webbrowser.get("brave")
        except (webbrowser.Error, OSError) as exc:
            utils.log_exception(
                exc,
                "Failed to get Brave controller via webbrowser.get",
            )
        for p in self._BRAVE_PATHS:
            if Path(p).exists():
                webbrowser.register(
                    "windows-brave",
                    None,
                    webbrowser.BackgroundBrowser(p),
                )
                with contextlib.suppress(webbrowser.Error, OSError):
                    return webbrowser.get("windows-brave")
        return None

    def _open_url_in_browser(self, latest_url: str, label: str | None = None) -> None:
        """Open a URL in the default browser, with fallback to Brave."""
        if self._try_open_default_browser(latest_url, label):
            return
        try:
            controller = self._get_brave_controller()
            if controller:
                controller.open_new_tab(latest_url)
                self.logEdit.appendPlainText(
                    f"Opened latest for {label or latest_url} in Brave",
                )
                return
        except (webbrowser.Error, OSError) as e:
            self.logEdit.appendPlainText(f"Failed to open Brave: {e}")
            utils.log_exception(e, "Failed to open URL in Brave")
        self.logEdit.appendPlainText(f"Failed to open latest for {label or latest_url}")

    def _refresh_podcast_status_dialog(self) -> None:
        """Refresh the contents of the Podcast Status dialog if it's currently visible."""
        dialog = getattr(self, "_podcast_status_dialog", None)
        if not dialog or not getattr(dialog, "isVisible", lambda: False)():
            return
        layout = dialog.layout()
        # Remove existing widgets
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
        if not self._podcast_last_statuses:
            layout.addWidget(
                QLabel(
                    "No cached podcast status available.\nPress 'YT Podcasts' to refresh.",
                ),
            )
            self._podcast_status_table = None
            return
        table = self._create_podcast_status_table(self._podcast_last_statuses)
        layout.addWidget(table)
        self._podcast_status_table = table

    def _download_podcast_now_action(self, row: int) -> None:
        """
        Initiate an immediate download of all pending episodes for the podcast at `row`.

        This bypasses the usual 24-hour / SponsorBlock wait period by invoking
        ``_filter_audio_playlist_urls`` with ``bypass_sponsorblock_wait=True`` and
        then reusing the normal ``_on_podcast_check_finished`` handler to queue the
        returned URLs.
        """
        statuses = getattr(self, "_podcast_last_statuses", [])
        if not (0 <= row < len(statuses)):
            return
        st = statuses[row]
        playlist_url = st.get("url")
        if not playlist_url:
            return

        # register that this podcast is actively downloading
        self._podcasts_downloading.add(playlist_url)

        # build ydl options similarly to the normal request path
        _, _, ydl_opts = self._create_download_context()
        properties = self.get_options([playlist_url], "audio_playlists")
        if properties:
            ydl_opts = self.append_properties(ydl_opts, properties)
            # attach metadata used by history/logging code
            ydl_opts["qmeta"] = {
                "site": utils.detect_site_from_urls([playlist_url]),
                "type": "audio_playlists",
            }

        # perform filtering immediately on the main thread
        to_download, pending, had_error, messages, statuses2 = (
            self._filter_audio_playlist_urls(
                [playlist_url],
                ydl_opts,
                bypass_sponsorblock_wait=True,
            )
        )

        # merge the single returned status into the existing list so we don't wipe
        # out the other rows in the status table
        current = list(getattr(self, "_podcast_last_statuses", []))
        updated = []
        found = False
        for s in current:
            if s.get("url") == playlist_url:
                found = True
                # take the new entry if available
                new = next((x for x in statuses2 if x.get("url") == playlist_url), None)
                if new:
                    new = dict(new)
                    # mark downloading regardless of what the filter says
                    new["status"] = "Downloading"
                    updated.append(new)
                else:
                    updated.append(s)
            else:
                updated.append(s)
        if not found:
            # podcast wasn't previously in table, just append new entries
            for item in statuses2:
                new_item = dict(item)
                new_item["status"] = "Downloading"
                updated.append(new_item)
        # pass merged list to handler so UI keeps all rows
        self._on_podcast_check_finished(
            to_download,
            pending,
            had_error,
            ydl_opts,
            messages,
            updated,
        )

    def _schedule_podcast_rechecks(self, statuses: list[dict]) -> None:
        """Store podcast statuses and schedule QTimer rechecks for upcoming episodes."""
        self._podcast_last_statuses = statuses
        if not hasattr(self, "_podcast_recheck_times"):
            self._podcast_recheck_times = {}
        if not hasattr(self, "_podcast_recheck_timers"):
            self._podcast_recheck_timers = {}
        for s in statuses:
            try:
                url = s.get("url")
                lu = s.get("latest_url")
                lts = s.get("latest_ts")
                if url and lu:
                    self._cache_put(url, lu, lts)
                rts = s.get("recheck_ts")
                if rts and url:
                    self._podcast_recheck_times[url] = rts
                    now_ts = datetime.now(tz=UTC).timestamp()
                    if rts > now_ts and url not in self._podcast_recheck_timers:
                        delay_ms = int((rts - now_ts) * 1000)
                        t = QTimer(self)
                        t.setSingleShot(True)
                        t.timeout.connect(
                            lambda u=url: self.request_detected([u], "audio_playlists"),
                        )
                        try:
                            t.start(delay_ms)
                            self._podcast_recheck_timers[url] = t
                        except (RuntimeError, ValueError, TypeError) as exc:
                            self.logEdit.appendPlainText(
                                f"Failed to schedule recheck timer for {url}",
                            )
                            utils.log_exception(
                                exc,
                                f"Failed to schedule recheck timer for {url}",
                            )
                else:
                    self._podcast_recheck_times.pop(url, None)
                    t = self._podcast_recheck_timers.pop(url, None)
                    if t:
                        with contextlib.suppress(Exception):
                            t.stop()
            except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
                utils.log_exception(
                    exc,
                    "Unexpected error while processing podcast statuses",
                )

    def _queue_podcast_downloads_grouped(
        self,
        to_download: list[dict],
        ydl_opts: dict,
    ) -> None:
        """Queue podcast downloads grouped by playlist label, one batch per label."""
        base_dir = podcast_base_dir()
        groups: dict[str, list[str]] = {}
        for obj in to_download:
            try:
                label = obj.get("playlist") or "misc"
            except (AttributeError, TypeError) as exc:
                label = "misc"
                utils.log_exception(
                    exc,
                    "Failed to read playlist label from podcast object",
                )
            safe_label = utils.slugify_if_too_long(base_dir, label)
            url = obj.get("url") if isinstance(obj, dict) else obj
            if url:
                groups.setdefault(safe_label, []).append(url)
        if not groups:
            return
        for label, urls in groups.items():
            qhook, qlogger, batch_opts = self._fork_download_context(
                ydl_opts if isinstance(ydl_opts, dict) else {},
            )
            batch_opts["outtmpl"] = build_podcast_outtmpl(label)
            if batch_opts.get("match_filter"):
                # Re-bind the filter to this batch's show so an episode that is
                # still live gets parked in the live queue with its label and
                # can be filed back under the same folder when it ends.
                batch_opts["match_filter"] = self.make_match_filter(
                    "audio_playlists",
                    label=label,
                )
            self.downloadQueue.put((urls, batch_opts))
            self._wire_download_signals(qhook, qlogger)
            if not hasattr(self, "_active_qhooks"):
                self._active_qhooks = []
            self._active_qhooks.append((qhook, qlogger))
        self.barProgress.setRange(0, 1)

    def _queue_podcast_downloads_flat(
        self,
        to_download: list[str],
        ydl_opts: dict,
    ) -> None:
        """Queue all podcast download URLs as a single batch."""
        qhook, qlogger, download_opts = self._fork_download_context(
            ydl_opts if isinstance(ydl_opts, dict) else {},
        )
        self.downloadQueue.put((to_download, download_opts))
        self._wire_download_signals(qhook, qlogger)
        if not hasattr(self, "_active_qhooks"):
            self._active_qhooks = []
        self._active_qhooks.append((qhook, qlogger))
        self.barProgress.setRange(0, 1)

    def _update_podcast_indicator(
        self,
        had_error: bool,
        to_download: list,
        pending_urls: set[str],
    ) -> None:
        """Set the podcast status indicator based on current results."""
        if had_error:
            self._set_podcast_indicator("error")
        elif to_download:
            self._set_podcast_indicator("busy")
        elif pending_urls:
            self._set_podcast_indicator("pending")
        else:
            self._set_podcast_indicator("all_good")

    def _on_podcast_check_finished(
        self,
        to_download: list,
        pending: list,
        had_error: bool,
        ydl_opts: dict,
        messages: list | None,
        statuses: list | None = None,
    ) -> None:
        """Handle results of a background podcast check (runs in main thread)."""
        for m in messages or []:
            self.logEdit.appendPlainText(m)

        if statuses:
            self._schedule_podcast_rechecks(statuses)

        self._last_podcast_check_error = had_error
        self._podcast_pending_urls.clear()
        for v in pending:
            url = v.get("url") if isinstance(v, dict) else v
            if url:
                self._podcast_pending_urls.add(url)

        if to_download:
            if isinstance(to_download[0], dict):
                self._queue_podcast_downloads_grouped(to_download, ydl_opts)
            else:
                self._queue_podcast_downloads_flat(to_download, ydl_opts)

        self._update_podcast_indicator(
            had_error,
            to_download,
            self._podcast_pending_urls,
        )
        self._podcast_check_running = False

        if not had_error and not to_download and not self._podcast_pending_urls:
            self.logEdit.appendPlainText(
                "No eligible podcast episodes found for immediate download. Pending items will be rechecked at the next scheduled YT Podcasts check.",
            )
        self.logEdit.appendPlainText(
            f"Podcast check complete: {len(to_download)} queued, {len(self._podcast_pending_urls)} pending, error={had_error}",
        )
        try:
            self._refresh_podcast_status_dialog()
        except (RuntimeError, AttributeError) as e:
            self.logEdit.appendPlainText(f"Error refreshing Podcast Status dialog: {e}")
            utils.log_exception(e, "Error refreshing Podcast Status dialog")

    def _shutdown_podcast_thread(self) -> None:
        """Attempt a clean shutdown of any running podcast worker thread."""
        thread = getattr(self, "_podcast_worker_thread", None)
        worker = getattr(self, "_podcast_worker", None)
        if not thread:
            return
        try:
            if worker and hasattr(worker, "request_stop"):
                worker.request_stop()
            if thread.isRunning():
                self.logEdit.appendPlainText("Shutting down podcast worker thread...")
                thread.quit()
                # Wait a short while for clean exit
                if not thread.wait(THREAD_QUIT_TIMEOUT_MS):
                    self.logEdit.appendPlainText(
                        "Podcast worker did not exit; terminating thread.",
                    )
                    try:
                        thread.terminate()
                    except (RuntimeError, OSError) as e:
                        self.logEdit.appendPlainText(
                            f"Error terminating podcast thread: {e}",
                        )
                        utils.log_exception(e, "Error terminating podcast thread")
                    thread.wait(THREAD_TERMINATE_TIMEOUT_MS)
        except (RuntimeError, OSError) as e:
            self.logEdit.appendPlainText(f"Error shutting down podcast thread: {e}")
            utils.log_exception(e, "Error shutting down podcast thread")
        finally:
            self._podcast_worker = None
            self._podcast_worker_thread = None

    def _save_open_dialog_geometry(self) -> None:
        """
        Record the Podcast Status window's geometry when the app quits with it open.

        Qt tears a child dialog down without routing it through close()/done() on
        this path, so the dialog's own dismissal-time save never runs and the size
        the user left it at would be lost.
        """
        dialog = getattr(self, "_podcast_status_dialog", None)
        if dialog is None:
            return
        try:
            if dialog.isVisible():
                dialog.save_geometry()
        except RuntimeError as e:
            # The dialog's C++ object is already gone; nothing left to measure.
            utils.log_exception(e, "Failed to save Podcast Status window geometry")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Ensure background podcast checks are stopped when the window closes."""
        try:
            self._save_open_dialog_geometry()
            self._shutdown_podcast_thread()
        finally:
            super().closeEvent(event)

    def _on_session_commit(self, manager: QSessionManager) -> None:
        logger.error(
            "SHUTDOWN: App closed by OS session event (hibernate/shutdown/logoff)"
        )

    # --- Hourly automated YT Podcasts checks ---
    def _schedule_hourly_podcast_checks(self) -> None:
        """Schedule the initial single-shot to fire at the next :15 past the hour, then start recurring hourly checks."""
        now = datetime.now(tz=UTC)
        # Target minute is 15 past the hour
        target = now.replace(minute=15, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(hours=1)
        delay_ms = int((target - now).total_seconds() * 1000)
        QTimer.singleShot(delay_ms, self._start_hourly_podcast_timer)
        self.logEdit.appendPlainText(
            f"Scheduled hourly YT Podcasts checks beginning {target.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        )

    def _start_hourly_podcast_timer(self) -> None:
        # Run once immediately at the scheduled time, then start recurring hourly timer.
        # Guard: _restart_podcast_timer may have already created an active timer via Settings.
        self._hourly_podcast_check()
        if (
            not hasattr(self, "_podcast_hour_timer")
            or not self._podcast_hour_timer.isActive()
        ):
            self._podcast_hour_timer = QTimer(self)
            self._podcast_hour_timer.setInterval(60 * 60 * 1000)  # 1 hour
            self._podcast_hour_timer.timeout.connect(self._hourly_podcast_check)
            self._podcast_hour_timer.start()

    def _hourly_podcast_check(self) -> None:
        """Perform a scheduled YT Podcasts check. Skips if 'Ignore Archive?' is enabled to avoid prompting."""
        if self.checkIgnoreArchive.isChecked():
            self.logEdit.appendPlainText(
                "Skipping scheduled YT Podcasts check because 'Ignore Archive?' is enabled.",
            )
            return
        self.logEdit.appendPlainText("Running scheduled YT Podcasts check (hourly).")
        # Use the same code path as the button, but avoid showing GUI prompts
        self.request_detected([], "audio_playlists")

    def _open_settings(self) -> None:
        """Open (or raise) the Settings dialog."""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self)
            self._settings_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self._settings_dialog.settings_changed.connect(self.reload_settings)
            # destroyed, not finished: finished only clears the reference, which would
            # leave the closed dialog alive as a hidden child of this window.
            self._settings_dialog.destroyed.connect(
                lambda: setattr(self, "_settings_dialog", None)
            )
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def reload_settings(self, changes: dict) -> None:
        """Apply live setting changes emitted by the Settings dialog."""
        resolution_keys = {"VID_DL_ENABLED_RESOLUTIONS"}
        for preset in RESOLUTION_PRESETS:
            resolution_keys.add(drop_label_key(preset.height))
            resolution_keys.add(button_label_key(preset.height))
            resolution_keys.add(playlist_file_key(preset.height))
        if resolution_keys & changes.keys():
            # A label, a playlist path, or the enabled set moved. Rebuilding the
            # whole grid is cheaper to reason about than patching individual
            # widgets, and it is the only path that handles a rung appearing or
            # disappearing. This also recreates self.labelAudio and
            # self.buttonAudioPlaylists, so the audio handlers below must run
            # after this, not before, or they would style widgets about to be
            # deleted.
            self._populate_resolution_grid()
        self._apply_label_changes(changes)
        self._apply_path_changes(changes)
        podcast_keys = {
            "VID_DL_PODCAST_AUTO_CHECK",
            "VID_DL_PODCAST_CHECK_INTERVAL_MINUTES",
        }
        if podcast_keys & changes.keys():
            self._restart_podcast_timer()
        if "VID_DL_ALWAYS_ON_TOP" in changes:
            self._apply_always_on_top(changes["VID_DL_ALWAYS_ON_TOP"])

    def _apply_always_on_top(self, enabled: bool) -> None:
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # setWindowFlags hides the window; must re-show

    def _apply_label_changes(self, changes: dict) -> None:
        """Update widget text from settings changes."""
        if "VID_DL_LABEL_DROP_AUDIO" in changes:
            self.labelAudio.setText(changes["VID_DL_LABEL_DROP_AUDIO"])
            self.labelAudio.originalText = changes["VID_DL_LABEL_DROP_AUDIO"]
        if "VID_DL_LABEL_READY_TEXT" in changes:
            self._ready_text = changes["VID_DL_LABEL_READY_TEXT"]
            if self.labelOutput.text() != "Checking podcasts for SponsorBlock info...":
                self.labelOutput.setText(self._ready_text)
        if "VID_DL_LABEL_BTN_PODCASTS" in changes:
            self.buttonAudioPlaylists.setText(changes["VID_DL_LABEL_BTN_PODCASTS"])

    def _apply_path_changes(self, changes: dict) -> None:
        """Update playlist paths from settings changes."""
        if "VID_DL_PLAYLISTS_AUDIO_FILE" in changes:
            self.buttonAudioPlaylists.playlist_path = Path(
                changes["VID_DL_PLAYLISTS_AUDIO_FILE"]
            )

    def _restart_podcast_timer(self) -> None:
        """Stop the existing hourly podcast timer and restart it if auto-check is enabled."""
        if hasattr(self, "_podcast_hour_timer"):
            self._podcast_hour_timer.stop()
        auto = get_setting("VID_DL_PODCAST_AUTO_CHECK")
        if auto:
            interval_min = int(
                get_setting("VID_DL_PODCAST_CHECK_INTERVAL_MINUTES") or 60
            )
            self._podcast_hour_timer = QTimer(self)
            self._podcast_hour_timer.setInterval(interval_min * 60 * 1000)
            self._podcast_hour_timer.timeout.connect(self._hourly_podcast_check)
            self._podcast_hour_timer.start()
            self.logEdit.appendPlainText(
                f"Podcast auto-check restarted: every {interval_min} min."
            )
        else:
            self.logEdit.appendPlainText("Podcast auto-check disabled.")

    def _start_app_update_check(self, auto: bool = False) -> None:
        """Start a background check for a newer app version."""
        self._app_update_worker = self._AppUpdateWorker()
        self._app_update_thread = QThread(self)
        self._app_update_worker.moveToThread(self._app_update_thread)
        self._app_update_thread.started.connect(self._app_update_worker.run)
        self._app_update_worker.finished.connect(
            lambda avail, tag, url: self._on_app_update_result(
                avail, tag, url, auto=auto
            )
        )
        self._app_update_worker.finished.connect(self._app_update_thread.quit)
        self._app_update_thread.start()

    def _maybe_start_auto_app_update_check(self) -> None:
        """Fire a background app-update check at most once per week if the setting is on."""
        if not get_setting("VID_DL_APP_UPDATE_AUTO_CHECK"):
            logger.info("Auto app update check disabled by setting")
            return
        last_checked = get_setting("VID_DL_APP_UPDATE_LAST_CHECKED")
        if last_checked:
            try:
                last_dt = date.fromisoformat(str(last_checked))
                if (datetime.now(tz=UTC).date() - last_dt).days < 7:
                    logger.info(
                        "Auto app update check skipped: last checked %s", last_checked
                    )
                    return
            except ValueError:
                pass
        logger.info("Starting automatic app update check")
        self._start_app_update_check(auto=True)

    def _on_app_update_result(
        self,
        update_available: bool,
        latest_tag: str,
        download_url: str,
        *,
        auto: bool = False,
    ) -> None:
        """Handle the result of the background app update check."""
        if auto:
            _persist_setting(
                "VID_DL_APP_UPDATE_LAST_CHECKED",
                datetime.now(tz=UTC).date().isoformat(),
            )
        if not update_available:
            if not auto:
                QMessageBox.information(
                    self,
                    "No Update Available",
                    "You are running the latest version.",
                )
            else:
                logger.info("Auto app update check: already on latest version")
            return
        answer = QMessageBox.question(
            self,
            "Update Available",
            f"Version {latest_tag} is available. Download now?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            webbrowser.open(download_url)

    def do_updates(self) -> None:
        """
        Update YT_DLP at start.

        Upgrade yt-dlp to the latest version using uv, open the changelog in the default browser,
        and restart the application if the update is successful. Updates the UI to indicate failure
        if the installed version does not match the latest version after the update attempt.
        """
        uv_path = shutil.which("uv")
        if uv_path is None:
            self.buttonUpdate.setStyleSheet("color: red;")
            return

        # 1. Update the lockfile to the latest yt-dlp
        lock_process = subprocess.run(  # noqa: S603
            [uv_path, "lock", "--upgrade-package", "yt-dlp"],
            check=False,
        )
        # 2. Sync the environment to actually install the new yt-dlp, using the updated lockfile
        sync_process = subprocess.run(  # noqa: S603
            [uv_path, "sync"],
            check=False,
        )

        if lock_process.returncode == 0 and sync_process.returncode == 0:
            webbrowser.open("https://github.com/yt-dlp/yt-dlp/blob/master/Changelog.md")
            # Update successful, restart app
            python = sys.executable
            script = os.path.realpath(sys.argv[0])
            subprocess.Popen([python, script, *sys.argv[1:]])  # noqa: S603
            sys.exit()
        else:
            # Update failed or no version change
            self.buttonUpdate.setStyleSheet("color: red;")


if __name__ == "__main__":
    _storage = Path(VIDEO_STORAGE_DIR)
    if _storage.exists():
        startfile(str(_storage))  # noqa: S606
    if getattr(sys, "frozen", False):
        dirname = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        dirname = Path(__file__).parent
    QDir.addSearchPath("icons", str(dirname / "resources" / "icons"))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon("icons:meadowlark.png"))
    app.setQuitOnLastWindowClosed(True)

    window = MyWindow()
    app.commitDataRequest.connect(window._on_session_commit)
    window.show()

    if needs_first_run():
        _wizard = FirstRunWizard(window)
        if _wizard.exec() == QDialog.DialogCode.Accepted:
            _restart_args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
            QProcess.startDetached(sys.executable, _restart_args)
            app.quit()

    if not shutil.which("ffmpeg"):
        QMessageBox.warning(
            None,
            "FFmpeg not found",
            "FFmpeg is not installed or not on PATH.\nAudio and podcast downloads will fail.",
        )

    deno_exe = Path(VENV_SCRIPTS_DIR) / "deno.exe"
    if not deno_exe.exists():
        if getattr(sys, "frozen", False):
            _deno_msg = f"Deno not found at {deno_exe}.\nYouTube downloads may fail."
        else:
            _deno_msg = f"Deno not found at {deno_exe}.\nRun `uv sync` to install it."
        QMessageBox.warning(None, "Deno not found", _deno_msg)

    _pot = check_pot_provider()
    if not _pot.ok:
        QMessageBox.warning(
            None,
            "PO Token Providers: none",
            "The YouTube PO-token provider is not fully set up "
            f"(missing: {_pot.summary()}).\n\n"
            "High-resolution downloads (1080p and above) will fail with HTTP 403 "
            '("unable to download video data"). Lower resolutions still work.\n\n'
            "See the README PO-token setup section to fix this.",
        )
    else:
        # Cold DENO_DIR makes the provider's own 15s script probe time out (~26s cold
        # vs ~1.5s warm), which silently kills PO-token minting and 403s the first
        # 1080p download. Warm it off-thread so the UI never waits on it; the download
        # worker waits on the result before its first item (see QYTQueue._await_deno_warm)
        # so an early download cannot race a cold probe and lose. Only worth doing when
        # the provider is otherwise complete -- warm_deno_cache() no-ops anyway if not.
        start_deno_warmup()

    app.exec()

# TODO: size control for error logs (low priority)
# TODO: make sure tests don't leave logs in the real error log
# TODO: playlist numbering gets tricked by Members first. It counts them even if it can't download them, so as they come off the members first list you get the same numbered playlist video multiple times.

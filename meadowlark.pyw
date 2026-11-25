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

The main window's feature groups live in `src/window_*.py` mixins, which `MyWindow` composes.

Dependencies:
- Python 3.11+
- PyQt6
- yt-dlp
- hurry.filesize
- Custom modules: src.qyt, src.ui_classes

Author: Gene
"""

import contextlib
import logging
import queue
import shutil
import sys
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from os import startfile
from pathlib import Path

from hurry.filesize import size
from PyQt6.QtCore import (
    QDir,
    QObject,
    QProcess,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QIcon,
    QKeySequence,
    QSessionManager,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QWidget,
)

from src import qyt
from src.config import (
    ALWAYS_ON_TOP,
    ARCHIVE_PATH,
    LABEL_READY_TEXT,
    LIVE_QUEUE_CHECK_INTERVAL_MINUTES,
    LIVE_QUEUE_FILE,
    PENDING_QUEUE_FILE,
    PODCAST_AUTO_CHECK,
    VENV_SCRIPTS_DIR,
    VIDEO_STORAGE_DIR,
)
from src.dict_utils import merge_dicts_recursive
from src.failed_downloads_dialog import FailedDownloadsDialog
from src.first_run_wizard import FirstRunWizard, needs_first_run
from src.logging_utils import init_app_logging, log_exception
from src.match_filter import build_match_filter
from src.pending_downloads_dialog import PendingDownloadsDialog
from src.pending_queue import (
    migrate_legacy_live_queue,
)
from src.playlist_utils import (
    detect_site_from_urls,
    labels_by_playlist_id,
    load_playlist_entries_for_source,
)
from src.podcast_filtering import (
    append_downloaded_video_ids,
    load_downloaded_video_ids,
)
from src.pot_provider import check_pot_provider, start_deno_warmup
from src.progress_smoothing import ProgressSmoother
from src.resolutions import (
    height_from_source,
)
from src.settings_dialog import (
    SettingsDialog,
    _init_runtime_settings,
)
from src.ui_classes import PlaylistDialog
from src.url_utils import host_matches
from src.version_utils import is_app_update_available, is_yt_dlp_update_available
from src.window_download_lists import DownloadListsMixin
from src.window_layout import LayoutMixin
from src.window_podcast_check import PodcastCheckMixin
from src.window_podcast_status import PodcastStatusMixin
from src.window_settings import SettingsUpdatesMixin
from src.ydl_options import (
    build_base_ydl_opts,
    get_source_options,
    resolve_cookiefile,
)
from src.ydl_utils import extract_playlist_info, extract_video_entries

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


class MyWindow(
    LayoutMixin,
    PodcastCheckMixin,
    PodcastStatusMixin,
    DownloadListsMixin,
    SettingsUpdatesMixin,
    QWidget,
):
    """
    MyWindow - A PyQt6-based main window for the MeadowLark application, providing a GUI for downloading and managing video and audio content from YouTube and other platforms.

    Features include playlist and audio download options, drag-and-drop support, progress tracking, log display, update checking, and integration with custom download queue and processing logic.
    """

    live_queue_log = pyqtSignal(str)

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

        update_available, _, _ = is_yt_dlp_update_available()
        self.buttonUpdate.setVisible(update_available)

        self._maybe_start_auto_app_update_check()

    def _setup_queue_and_downloader(self) -> None:
        """Set up download queue, downloader, and signal connections."""
        self.downloadQueue = queue.Queue()
        self.downloader = qyt.QYTQueue(self.downloadQueue)
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
        return merge_dicts_recursive(dictionary, properties)

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
            entries = load_playlist_entries_for_source(source)
            if entries is not None:
                urls = [entry.url for entry in entries]
                # Rebind, never .clear(): an earlier queued job's qmeta holds the old dict.
                self.playlist_comments = labels_by_playlist_id(entries)

        properties = self.get_options(urls, source)
        if properties:
            ydl_opts = self.append_properties(ydl_opts, properties)
            if ydl_opts:
                # Provide metadata for history logging
                ydl_opts["qmeta"] = {
                    "site": detect_site_from_urls(urls),
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
        qlogger = qyt.QLogger(self.downloadQueue)
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
        if host_matches(urls[0], "youtube.com"):
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
        source_props = get_source_options(source)
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
            update_available, tag, url = is_app_update_available()
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
                log_exception(exc, "Podcast check worker exception")
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

    def _create_download_context(self) -> tuple[qyt.QHook, qyt.QLogger, dict]:
        """Create a fresh QHook, QLogger, and base ydl_opts dict."""
        qhook = qyt.QHook()
        qlogger = qyt.QLogger(self.downloadQueue)
        ydl_opts = build_base_ydl_opts(qlogger, qhook)
        return qhook, qlogger, ydl_opts

    def _fork_download_context(
        self,
        base_opts: dict,
    ) -> tuple[qyt.QHook, qyt.QLogger, dict]:
        """Create a fresh QHook/QLogger and return a copy of base_opts with them wired in."""
        qhook = qyt.QHook()
        qlogger = qyt.QLogger(self.downloadQueue)
        opts = dict(base_opts)
        opts["logger"] = qlogger
        opts["progress_hooks"] = [qhook]
        return qhook, qlogger, opts

    def _wire_download_signals(self, qhook: qyt.QHook, qlogger: qyt.QLogger) -> None:
        """Connect qhook/qlogger signals to the main window handler slots."""
        qhook.info_changed.connect(self.handle_info_changed)
        qlogger.message_changed.connect(self.handle_log_entry)

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
            log_exception(e, "Failed to save Podcast Status window geometry")

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


if __name__ == "__main__":
    init_app_logging()  # first: anything logged before this goes to stderr, not error_log.txt
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

    # Every blocking startup probe runs BEFORE window.show(). Explorer creates the
    # taskbar button on first show and asks the window for its icon (WM_GETICON); a GUI
    # thread that is not pumping messages for ~1s then (e.g. a cold deno.exe spawn)
    # makes Explorer fall back to the generic class icon, and it never asks again.
    # Modal dialogs below are fine anywhere: their exec() pumps messages.
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

    window = MyWindow()
    app.commitDataRequest.connect(window._on_session_commit)
    window.show()

    if needs_first_run():
        _wizard = FirstRunWizard(window)
        if _wizard.exec() == QDialog.DialogCode.Accepted:
            _restart_args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
            QProcess.startDetached(sys.executable, _restart_args)
            # app.quit() is a no-op before app.exec() starts its loop, so exit for real:
            # otherwise this instance keeps running beside the restart, with stale config.
            window.close()
            sys.exit(0)

    app.exec()

# TODO: size control for error logs (low priority)
# TODO: playlist numbering gets tricked by Members first. It counts them even if it can't download them, so as they come off the members first list you get the same numbered playlist video multiple times.

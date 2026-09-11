"""
DownloadService - Handles download request processing, queue management, and podcast triggering.

This module provides the DownloadService class that encapsulates the logic for processing download requests,
managing the download queue, and triggering podcast checks. It extracts this functionality from the MyWindow class
to improve testability and separation of concerns.
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from queue import Queue
from typing import Any

import yt_dlp

import QYT
import utils
from src.config import (
    ARCHIVE_PATH,
    COOKIES_FILE,
    LIVE_QUEUE_FILE,
    PENDING_QUEUE_FILE,
    PLAYLISTS_AUDIO_FILE,
    playlist_path_for_height,
)
from src.match_filter import build_match_filter
from src.pending_check import (
    PendingCheckDeps,
)
from src.pending_check import (
    check_pending_queue as run_pending_check,
)
from src.pending_queue import (
    KIND_LIVE,
    PendingRecord,
    load_pending_queue,
    make_pending_record,
    migrate_legacy_live_queue,
    save_pending_queue,
    upsert_pending,
)
from src.playlist_utils import load_playlist_urls
from src.podcast_filtering import append_downloaded_video_ids, load_downloaded_video_ids
from src.resolutions import height_from_source
from src.ydl_options import build_shared_extraction_opts


class DownloadService:
    """
    Service class for handling download requests, queue management, and podcast triggering.

    This class extracts the download logic from MyWindow to provide a testable, modular component
    that manages download requests, builds options, handles playlists, and coordinates podcast checks.
    """

    def __init__(
        self,
        download_queue: Queue,
        ignore_archive_callback: Callable[[], bool],
        skip_download_callback: Callable[[], bool],
        label_output_set_text_callback: Callable[[str], None],
        log_edit_append_callback: Callable[[str], None],
        bar_progress_set_range_callback: Callable[[int, int], None],
        bar_progress_set_value_callback: Callable[[int], None],
        handle_info_changed_callback: Callable[[Any], None],
        handle_log_entry_callback: Callable[[str], None],
        handle_queue_empty_callback: Callable[[], None],
        do_updates_callback: Callable[[], None],
        add_to_live_queue_callback: Callable[..., None],
        qhook_factory: Callable[[], QYT.QHook],
        qlogger_factory: Callable[[], QYT.QLogger],
    ) -> None:
        """
        Initialize the DownloadService with necessary callbacks and dependencies.

        Args:
            download_queue: The download queue to use for queuing downloads.
            ignore_archive_callback: Callback to check if archive should be ignored.
            skip_download_callback: Callback to check if download should be skipped.
            label_output_set_text_callback: Callback to set label output text.
            log_edit_append_callback: Callback to append to log edit.
            bar_progress_set_range_callback: Callback to set progress bar range.
            bar_progress_set_value_callback: Callback to set progress bar value.
            handle_info_changed_callback: Callback to handle info changed.
            handle_log_entry_callback: Callback to handle log entry.
            handle_queue_empty_callback: Callback to handle queue empty.
            do_updates_callback: Callback to perform updates.
            add_to_live_queue_callback: Callback to add to live queue (url, source, playlist_id, label).
            qhook_factory: Factory for QHook.
            qlogger_factory: Factory for QLogger.
        """
        self.download_queue = download_queue
        self.ignore_archive_callback = ignore_archive_callback
        self.skip_download_callback = skip_download_callback
        self.label_output_set_text_callback = label_output_set_text_callback
        self.log_edit_append_callback = log_edit_append_callback
        self.bar_progress_set_range_callback = bar_progress_set_range_callback
        self.bar_progress_set_value_callback = bar_progress_set_value_callback
        self.handle_info_changed_callback = handle_info_changed_callback
        self.handle_log_entry_callback = handle_log_entry_callback
        self.handle_queue_empty_callback = handle_queue_empty_callback
        self.do_updates_callback = do_updates_callback
        self.add_to_live_queue_callback = add_to_live_queue_callback
        self.qhook_factory = qhook_factory
        self.qlogger_factory = qlogger_factory

        self.pending_queue_path = PENDING_QUEUE_FILE
        migrate_legacy_live_queue(LIVE_QUEUE_FILE, self.pending_queue_path)

    def request_detected(self, urls: list, source: str) -> tuple[str, list, dict]:
        """
        Handle a detected download request by preparing options, updating URLs if needed, and returning action.

        Args:
            urls (list): List of URLs to process.
            source (str): The source type or action (e.g., playlist type or 'Update').

        Returns:
            tuple: (action, urls, ydl_opts) where action is 'update', 'skip', 'queue', 'podcast_check'
        """
        if source == "Update":
            self.do_updates_callback()
            return ("update", [], {})

        # Load playlist URLs if applicable
        if not urls:  # Only load from file if no URLs provided
            playlist_urls = self._load_playlist_urls(source)
            if playlist_urls is not None:
                urls = playlist_urls

        properties = self.get_options(urls, source)
        if properties is None:
            return ("skip", [], {})

        ydl_opts = utils.build_base_ydl_opts(None, None)
        ydl_opts = self.append_properties(ydl_opts, properties)
        ydl_opts["qmeta"] = {
            "site": utils.detect_site_from_urls(urls),
            "type": source,
        }

        if source == "audio_playlists":
            return ("podcast_check", urls, ydl_opts)
        return ("queue", urls, ydl_opts)

    def _load_playlist_urls(self, source: str) -> list[str] | None:
        """Load playlist URLs from the appropriate file based on the source."""
        if source == "audio_playlists":
            path = Path(PLAYLISTS_AUDIO_FILE)
        else:
            height = height_from_source(source)
            if height is None or not source.endswith("playlists"):
                return None
            path = playlist_path_for_height(height)
        return load_playlist_urls(path) or None

    def get_options(
        self,
        urls: list,
        source: str,
        skip_playlist_dialog: bool = False,
    ) -> dict | None:
        """
        Build yt-dlp options dict based on URLs and source type.

        Returns None if the download should be skipped or there are no URLs.
        """
        if self.skip_download_callback():
            self.skip_downloading(urls, source)
            return None

        if not urls:
            self.log_edit_append_callback(f"No URLs found for source: {source}")
            return None

        properties = utils.get_source_options(source)
        self._add_archive_if_needed(properties)
        self._add_match_filter_if_youtube(properties, urls, source)
        self._strip_watch_later_list_param(urls)
        return properties

    def _add_archive_if_needed(self, properties: dict) -> None:
        """Add the download archive path to properties unless the user opted to ignore it."""
        if not self.ignore_archive_callback():
            properties["download_archive"] = str(ARCHIVE_PATH)

    def _add_match_filter_if_youtube(
        self, properties: dict, urls: list, source: str
    ) -> None:
        """Attach a custom match_filter for YouTube URLs to skip and queue live videos."""
        if urls and "youtube.com" in urls[0]:
            properties["match_filter"] = self.make_match_filter(source)

    def _strip_watch_later_list_param(self, urls: list) -> None:
        """Remove the &list= parameter from a Watch Later URL in place."""
        if urls and "youtube.com/watch" in urls[0] and "&list=" in urls[0]:
            urls[0] = urls[0].split("&list=")[0]

    def append_properties(self, ydl_opts: dict, properties: dict) -> dict | None:
        """
        Append additional properties to yt-dlp options.

        Args:
            ydl_opts (dict): Base yt-dlp options.
            properties (dict): Additional properties to append.

        Returns:
            dict: Updated yt-dlp options, or None if cancelled.
        """
        ydl_opts.update(properties)
        return ydl_opts

    def skip_downloading(self, urls: list, source: str) -> None:
        """Skip downloading the given URLs for the source."""
        self.label_output_set_text_callback("Skipping downloads.")
        qlogger = self.qlogger_factory()
        total_added = 0
        existing_ids = load_downloaded_video_ids(str(ARCHIVE_PATH))
        for url in urls:
            # Use extract_flat="in_playlist" for playlists, True for single videos
            ydl_opts = {
                **build_shared_extraction_opts(),
                "extract_flat": "in_playlist" if "lists" in source else True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get("entries", [info])
            added = append_downloaded_video_ids(
                ARCHIVE_PATH, [entry.get("id") for entry in entries], existing_ids
            )
            total_added += len(added)
            for video_id in added:
                # QLogger.debug takes one pre-formatted message, not lazy %-args.
                message = f"Added to archive: youtube {video_id}"
                qlogger.debug(message)
        self.label_output_set_text_callback("IDs added to archive.")
        self.bar_progress_set_range_callback(0, 1)
        self.bar_progress_set_value_callback(1)
        self.log_edit_append_callback(
            f"Archive-only mode: {total_added} IDs written.",
        )
        self.handle_queue_empty_callback()

    def make_match_filter(self, source: str, label: str | None = None) -> Callable:
        """
        Build a match_filter that skips live/upcoming videos and queues them.

        Args:
            source: The download source identifier.
            label: Destination folder label the skipped item was bound for, so
                the pending-queue re-queue can restore it (see check_pending_queue).
        """
        return build_match_filter(
            source,
            add_to_queue_fn=partial(self.add_to_live_queue_callback, label=label),
            log_fn=self.log_edit_append_callback,
        )

    def load_pending_queue(self) -> list[PendingRecord]:
        """Load parked downloads (live streams and unreleased premieres)."""
        return load_pending_queue(self.pending_queue_path)

    def save_pending_queue(self, records: list[PendingRecord]) -> None:
        """Save the parked-download records to the store."""
        save_pending_queue(self.pending_queue_path, records)

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

    def _create_download_context(self) -> tuple[Any, Any, dict]:
        """Create a fresh QHook, QLogger, and base ydl_opts dict."""
        qhook = self.qhook_factory()
        qlogger = self.qlogger_factory()
        return qhook, qlogger, utils.build_base_ydl_opts(qlogger, qhook)

    def _wire_download_signals(self, qhook: Any, qlogger: Any) -> None:
        """Connect qhook/qlogger signals to the injected handler callbacks."""
        qhook.info_changed.connect(self.handle_info_changed_callback)
        qlogger.message_changed.connect(self.handle_log_entry_callback)

    def _pending_deps(self) -> PendingCheckDeps:
        """Bind this service's callbacks to the shared pending-queue poll loop."""
        return PendingCheckDeps(
            path=self.pending_queue_path,
            cookiefile=str(COOKIES_FILE),
            get_options=lambda urls, source: self.get_options(
                urls, source, skip_playlist_dialog=True
            ),
            append_properties=self.append_properties,
            create_context=self._create_download_context,
            wire_signals=self._wire_download_signals,
            enqueue=lambda urls, opts: self.download_queue.put((urls, opts)),
            log=self.log_edit_append_callback,
            set_progress_range=self.bar_progress_set_range_callback,
            detect_site=utils.detect_site_from_urls,
            load_playlist_comments=utils.load_playlist_comments_for_source,
            ydl_class=yt_dlp.YoutubeDL,
        )

    def check_pending_queue(self) -> list[PendingRecord]:
        """Poll parked downloads and queue any that became available."""
        return run_pending_check(self._pending_deps())

"""Main-window download lists: pending queue, failed downloads and history dialogs."""

# Bodies were moved verbatim from meadowlark.pyw, which ruff never linted (.pyw is outside its
# default include), so their long lines are exempted here rather than rewrapped in the move.

import yt_dlp
from PyQt6.QtCore import Qt

from src.config import ARCHIVE_PATH, FAILED_DOWNLOADS_FILE
from src.failed_downloads import (
    add_failed_download,
    keys_resolved_by_download,
    load_failed_downloads,
    record_video_id,
    remove_failed_downloads,
)
from src.failed_downloads_dialog import FailedDownloadsDialog
from src.history_dialog import HistoryDialog
from src.logging_utils import log_exception
from src.pending_check import PendingCheckDeps, enqueue_entry
from src.pending_check import check_pending_queue as run_pending_check
from src.pending_downloads_dialog import PendingDownloadsDialog
from src.pending_queue import (
    KIND_LIVE,
    KIND_PREMIERE,
    PendingRecord,
    load_pending_queue,
    make_pending_record,
    remove_pending_many,
    save_pending_queue,
    upsert_pending,
)
from src.playlist_entry import playlist_entry_target
from src.playlist_utils import detect_site_from_urls, load_playlist_comments_for_source
from src.podcast_filtering import append_downloaded_video_ids
from src.release_status import is_not_yet_released, parse_relative_release, to_release_at
from src.resolutions import source_key
from src.settings_dialog import enabled_heights
from src.window_base import WindowState
from src.ydl_options import resolve_cookiefile


class DownloadListsMixin(WindowState):
    """Pending, Failed and History lists of the main window."""

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
            make_pending_record(url, source, playlist_id=playlist_id, label=label, kind=KIND_LIVE),
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
            detect_site=detect_site_from_urls,
            load_playlist_comments=load_playlist_comments_for_source,
            ydl_class=yt_dlp.YoutubeDL,
        )

    def check_pending_queue(self) -> list[PendingRecord]:
        """Poll parked downloads, queue any that became available, refresh the UI."""
        remaining = run_pending_check(self._pending_deps())
        self._refresh_pending_button(remaining)
        return remaining

    def _refresh_failed_button(self) -> None:
        """Sync the warning button's count and visibility with the store."""
        count = len(load_failed_downloads(FAILED_DOWNLOADS_FILE))
        self.buttonFailed.setText(f"⚠ {count}")
        self.buttonFailed.setVisible(count > 0)

    def _refresh_pending_button(self, records: list[PendingRecord] | None = None) -> None:
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
                log_exception(exc, "Failed to focus pending-downloads dialog")
            else:
                return

        dialog = PendingDownloadsDialog(load_pending_queue(self.pending_queue_path), self)
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

    def _redownload(self, url: str, source: str, playlist_id: str | None) -> None:
        """
        Queue one item again, through its playlist URL when it is a video-playlist entry.

        A bare watch URL under a <height>playlists source lands in NA/NA - <title> (BACKLOG #23).
        """
        if playlist_entry_target(url, source, playlist_id) is None:
            self.request_detected([url], source)
            return
        enqueue_entry(self._pending_deps(), url, source, playlist_id=playlist_id, recheck_live=True)

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
            self.handle_log_entry(f"Downloading pending item now: {record.get('title') or url}")
            self._redownload(
                url, record.get("source") or fallback_source, record.get("playlist_id")
            )

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
            log_exception(exc, "Failed to persist failed download")

    def _park_not_yet_released(self, record: dict) -> None:
        """
        Park an announced-but-unaired item instead of filing it as a failure.

        yt-dlp raises "Premieres in 6 hours" out of extraction before any info_dict
        exists, so match_filter never sees the video and the error is the only signal
        we get. The relative time parsed from that message is coarse; the immediate
        check_pending_queue() below replaces it with the exact release_timestamp (and
        downloads straight away if the premiere already aired). The playlist id comes from the
        failed record (see ``FailureHook.set_current_url``).
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
                    playlist_id=record.get("playlist_id"),
                    kind=KIND_PREMIERE,
                    title=record.get("title") or url,
                    release_at=to_release_at(parse_relative_release(error)),
                ),
            )
        except OSError as exc:
            log_exception(exc, "Failed to park not-yet-released download")
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
                log_exception(exc, "Failed to focus failed-downloads dialog")
            else:
                return

        dialog = FailedDownloadsDialog(load_failed_downloads(FAILED_DOWNLOADS_FILE), self)
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
        self._delete_failed_downloads([r["key"] for r in runnable])
        for record in runnable:
            self.handle_log_entry(
                f"Retrying failed download: {record.get('title') or record['urls'][0]}"
            )
            urls = list(record["urls"])
            # A multi-URL record failed as a batch; only a single entry has one playlist to walk.
            if len(urls) == 1:
                self._redownload(urls[0], record["source"], record.get("playlist_id"))
            else:
                self.request_detected(urls, record["source"])

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
            log_exception(exc, "Failed to add videos to archive from Failed Downloads")
            return
        self._delete_failed_downloads([r["key"] for r, _ in marked])
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
                log_exception(exc, "Failed to focus existing history dialog")
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
        if record.get("result") == "SUCCESS":
            self._clear_resolved_failures(record.get("url"))

    def _clear_resolved_failures(self, url: str | None) -> None:
        """Drop failed-download records that a successful download just resolved."""
        try:
            keys = keys_resolved_by_download(load_failed_downloads(FAILED_DOWNLOADS_FILE), url)
            if keys:
                self._delete_failed_downloads(keys)
        except OSError as exc:
            # An exception escaping a Qt slot aborts the interpreter.
            log_exception(exc, "Failed to clear resolved failed downloads")

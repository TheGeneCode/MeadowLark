"""Main-window Podcast Status dialog: table, latest-episode cache, context menu, browser opening and download-now."""

# Bodies were moved verbatim from meadowlark.pyw, which ruff never linted (.pyw is outside its
# default include), so their long lines are exempted here rather than rewrapped in the move.
# Likewise, the few PyQt6 calls on Optional-typed returns carry rule-scoped pyright ignores.
# ruff: noqa: E501

import contextlib
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.config import PODCAST_STATUS_GEOMETRY_KEY, YDL_COMMON_ERRORS
from src.logging_utils import log_exception
from src.playlist_utils import detect_site_from_urls
from src.podcast_filtering import parse_scheduled_time_from_error, parse_video_id_from_error
from src.window_base import WindowState
from src.window_geometry import GeometryMemoryDialog
from src.ydl_utils import extract_playlist_info

# First-run size of the Podcast Status window, matching the other list dialogs
# (history, pending, failed). After that the window reopens at whatever size it
# was closed at -- see src/window_geometry.py.
_PODCAST_STATUS_DEFAULT_SIZE = (900, 500)


class PodcastStatusMixin(WindowState):
    """Podcast Status dialog of the main window."""

    _BRAVE_PATHS: ClassVar[list[str]] = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]

    # Cache TTL: 6 hours
    CACHE_TTL_SECONDS = 6 * 60 * 60

    def _create_podcast_status_table(self, statuses: list[dict]) -> QTableWidget:
        """Create and populate a podcast status table widget."""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Podcast", "Latest Episode", "Status"])
        table.horizontalHeader().setSectionResizeMode(  # pyright: ignore[reportOptionalMemberAccess]
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
                log_exception(
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
        action_open.triggered.connect(  # pyright: ignore[reportOptionalMemberAccess]
            lambda: self._guarded_status_action(
                self._open_latest_for_row,
                row,
                error_label="open latest video",
            ),
        )

        # Download Now bypasses the SponsorBlock wait
        action_download = menu.addAction("Download Now")
        action_download.triggered.connect(  # pyright: ignore[reportOptionalMemberAccess]
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
            log_exception(exc, f"Error in status menu action: {error_label}")

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
        resolved = self._resolve_latest_via_ytdlp(playlist_url) if playlist_url else None
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
            log_exception(
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
            log_exception(exc, "Failed to open URL in default browser")
        return False

    def _get_brave_controller(self) -> webbrowser.BaseBrowser | None:
        """Return a Brave browser controller, registering it from disk if needed."""
        try:
            return webbrowser.get("brave")
        except (webbrowser.Error, OSError) as exc:
            log_exception(
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
            log_exception(e, "Failed to open URL in Brave")
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
                "site": detect_site_from_urls([playlist_url]),
                "type": "audio_playlists",
            }

        # perform filtering immediately on the main thread
        to_download, pending, had_error, messages, statuses2 = self._filter_audio_playlist_urls(
            [playlist_url],
            ydl_opts,
            bypass_sponsorblock_wait=True,
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

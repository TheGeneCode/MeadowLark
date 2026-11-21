"""Main-window podcast auto-check: scheduling, episode filtering, result handling and timers."""

# Bodies were moved verbatim from meadowlark.pyw, which ruff never linted (.pyw is outside its
# default include), so their long lines are exempted here rather than rewrapped in the move.
# ruff: noqa: E501

import contextlib
from datetime import UTC, datetime, timedelta

from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication

from src import qyt
from src.config import YDL_EXTRACTION_ERRORS
from src.logging_utils import log_exception
from src.path_utils import resolve_playlist_label, sanitize_for_path, slugify_if_too_long
from src.playlist_utils import (
    detect_site_from_urls,
    load_playlist_comments_for_source,
    playlist_id_for_line,
)
from src.podcast_filtering import (
    PODCAST_MIN_DURATION_SECONDS,
    append_to_archive_and_mark_skipped,
    check_sponsorblock_for_video_id,
    format_timestamp_readable,
    load_downloaded_video_ids,
    parse_scheduled_time_from_error,
    parse_video_id_from_error,
    parse_video_timestamp,
)
from src.podcast_helpers import fetch_latest_accessible_entry
from src.settings_dialog import get_int_setting, get_setting
from src.window_base import WindowState
from src.ydl_options import build_podcast_outtmpl, podcast_base_dir

THREAD_QUIT_TIMEOUT_MS = 2000
THREAD_TERMINATE_TIMEOUT_MS = 1000


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
    pl_id = playlist_id_for_line(url)
    if pl_id and pl_id in audio_pl_comments:
        return sanitize_for_path(audio_pl_comments[pl_id])
    return None


class PodcastCheckMixin(WindowState):
    """Podcast auto-check pipeline of the main window."""

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
                log_exception(
                    e,
                    "Failed to start podcast check thread",
                )
                self._podcast_check_running = False
                self._set_podcast_indicator("error")

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
        qyt.HistoryLogger().log_skip(
            site=detect_site_from_urls([webpage]),
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
        qyt.HistoryLogger().log_skip(
            site=detect_site_from_urls([webpage]),
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
        site = detect_site_from_urls([webpage])
        if site != "youtube" or check_sponsorblock_for_video_id(vid):
            to_download.append(obj)
            status_entry["status"] = "Ready"
        else:
            pending.append(obj)
            status_entry["status"] = "Pending SponsorBlock"

    def _filter_audio_playlist_urls(  # noqa: PLR0915
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
        audio_pl_comments = load_playlist_comments_for_source("audio_playlists")

        for url in urls:
            try:
                entries, skipped, info = fetch_latest_accessible_entry(url)
                if skipped:
                    messages.append(
                        f"Latest episode for podcast {url} is private - using previous accessible video",
                    )
                playlist_label = _label_from_comments(
                    url, audio_pl_comments
                ) or resolve_playlist_label(info, url)
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
                log_exception(e, f"Error expanding playlist/url {url}")
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
                            log_exception(
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
                log_exception(
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
                log_exception(
                    exc,
                    "Failed to read playlist label from podcast object",
                )
            safe_label = slugify_if_too_long(base_dir, label)
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
            log_exception(e, "Error refreshing Podcast Status dialog")

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
                        log_exception(e, "Error terminating podcast thread")
                    thread.wait(THREAD_TERMINATE_TIMEOUT_MS)
        except (RuntimeError, OSError) as e:
            self.logEdit.appendPlainText(f"Error shutting down podcast thread: {e}")
            log_exception(e, "Error shutting down podcast thread")
        finally:
            self._podcast_worker = None
            self._podcast_worker_thread = None

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
        if not hasattr(self, "_podcast_hour_timer") or not self._podcast_hour_timer.isActive():
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

    def _restart_podcast_timer(self) -> None:
        """Stop the existing hourly podcast timer and restart it if auto-check is enabled."""
        if hasattr(self, "_podcast_hour_timer"):
            self._podcast_hour_timer.stop()
        auto = get_setting("VID_DL_PODCAST_AUTO_CHECK")
        if auto:
            interval_min = get_int_setting("VID_DL_PODCAST_CHECK_INTERVAL_MINUTES", 60)
            self._podcast_hour_timer = QTimer(self)
            self._podcast_hour_timer.setInterval(interval_min * 60 * 1000)
            self._podcast_hour_timer.timeout.connect(self._hourly_podcast_check)
            self._podcast_hour_timer.start()
            self.logEdit.appendPlainText(f"Podcast auto-check restarted: every {interval_min} min.")
        else:
            self.logEdit.appendPlainText("Podcast auto-check disabled.")

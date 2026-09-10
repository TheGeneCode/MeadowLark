"""Provides PyQt-based classes for logging, progress signaling, and threaded download queue management using yt-dlp. Includes QLogger for emitting log messages, QHook for progress updates, and QYTQueue for managing and executing download tasks in a background thread with wake lock support."""

import logging
import re
from collections.abc import Callable
from pathlib import Path
from queue import Queue

from dotenv import load_dotenv
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from wakepy import keep

_user_env = Path.home() / "AppData" / "Roaming" / "MeadowLark" / ".env"
load_dotenv(dotenv_path=_user_env if _user_env.exists() else None)
load_dotenv()  # also load project .env in dev (won't override vars already set)

from genekit.logging import configure_logging  # noqa: E402

import utils  # noqa: E402  (imported after load_dotenv so env is set at import time)
from src.config import (  # noqa: E402
    ERROR_LOG_PATH,
    HISTORY_LOG_PATH,
    LOGFILE_MIGRATION_ENABLED,
)
from src.download_executor import DownloadExecutor  # noqa: E402
from src.failed_downloads import (  # noqa: E402
    ErrorCapturingLogger,
    FailureHook,
    make_failed_record,
)
from src.logging_utils import (  # noqa: E402
    get_local_timestamp,
    get_ytdlp_debug_logger,
)
from src.pot_provider import deno_warmup_pending, wait_for_deno_warm  # noqa: E402

configure_logging("ERROR", log_file=ERROR_LOG_PATH, console="none")

# Migrate prior logfile name to the new error log if present
if LOGFILE_MIGRATION_ENABLED:
    try:
        if Path("logfile.txt").exists() and not ERROR_LOG_PATH.exists():
            Path("logfile.txt").replace(ERROR_LOG_PATH)
    except OSError as exc:
        # Never fail on migration, but do record the issue for later diagnosis
        utils.log_exception(exc, "Failed to migrate logfile.txt to error_log.txt")


class QLogger(QObject):
    """
    A PyQt-based logger class that emits log messages via the messageChanged signal.

    Integrates with a download queue and provides debug, warning, and error methods,
    emitting messages to connected slots, with filtering for debug messages containing 'ETA' or 'iB/s'.
    """

    message_changed = pyqtSignal(str)

    def __init__(self, download_queue: Queue) -> None:
        """
        Initialize the thread with a download queue and set it as a daemon thread.

        Args:
            download_queue (Queue): The queue containing download tasks.
        """
        super().__init__()
        self.downloadQueue = download_queue
        self.daemon = True
        # yt-dlp routes its whole diagnostic stream through this object, so this
        # is the only place it can be captured. Resolved once per logger: the
        # setting cannot change mid-session.
        self._debug_logger = get_ytdlp_debug_logger()

    def _tee(self, level: int, msg: str) -> None:
        """Mirror a yt-dlp message into the verbose capture, when it is enabled."""
        if self._debug_logger is not None:
            self._debug_logger.log(level, msg)

    def debug(self, msg: str) -> None:
        """Log a debug message using the module logger and emits the message via the message_changed signal, unless the message contains 'ETA' or 'iB/s'."""
        logger = logging.getLogger(__name__)
        logger.debug(msg)
        self._tee(logging.DEBUG, msg)
        if "ETA" not in msg and "iB/s" not in msg:
            self.message_changed.emit(msg)

    def warning(self, msg: str) -> None:
        """
        Log a warning message using the module logger and emits the message via the message_changed signal.

        Args:
            msg (str): The warning message to log and emit.
        """
        logger = logging.getLogger(__name__)
        logger.warning(msg)
        self._tee(logging.WARNING, msg)
        self.message_changed.emit(msg)

    def error(self, msg: str) -> None:
        """
        Log an error message using the module logger and emits the message via the message_changed signal.

        Args:
            msg (str): The error message to log and emit.
        """
        logger = logging.getLogger(__name__)
        logger.error(msg)
        self._tee(logging.ERROR, msg)
        self.message_changed.emit(msg)

    def exception(self, msg: str) -> None:
        """
        Log an exception message using the module logger and emits the message via the message_changed signal.

        Args:
            msg (str): The exception message to log and emit.
        """
        logger = logging.getLogger(__name__)
        logger.exception(msg)
        if self._debug_logger is not None:
            # exception() rather than _tee: keep the traceback in the capture.
            self._debug_logger.exception(msg)
        self.message_changed.emit(msg)

    # def download(self, urls, options):
    #     with YoutubeDL(options) as ydl:
    #         ydl.cache.remove()
    #         try:
    #             ydl.download(urls)
    #         except Exception as e:
    #             return f"An error occurred during the download: {str(e)}"
    #     for hook in options.get("progress_hooks", []):
    #         if hasattr(hook, "deleteLater"):
    #             hook.deleteLater()
    #     logger = options.get("logger")
    #     if hasattr(logger, "messageChanged"):
    #         # Reuse logger for subsequent downloads
    #         logger.messageChanged.disconnect()
    #         logger.messageChanged.connect(self.messageChanged.emit)


class QHook(QObject):
    """A class that emits a signal when the info is changed."""

    info_changed = pyqtSignal(dict)

    def __init__(self, parent: QObject = None) -> None:
        """
        Initialize the QHook object.

        Args:
            parent (QObject): The parent object. Default is None.
        """
        super().__init__(parent)

    def __call__(self, d: dict) -> None:
        """
        Call the QHook object.

        Args:
            d (dict): The dictionary containing the info.
        """
        self.info_changed.emit(d.copy())


class HistoryLogger:
    """Writes human-readable download history entries to history_log.txt."""

    HISTORY_PATH = HISTORY_LOG_PATH

    def __init__(self, on_log: Callable[[dict], None] | None = None) -> None:
        self._on_log = on_log

    @staticmethod
    def _format_entry(
        dt: str,
        site: str,
        dtype: str,
        title: str,
        result: str,
        url: str | None = None,
    ) -> str:
        line = (
            f"[{dt}] Site: {site} | Type: {dtype} | Title: {title} | Result: {result}"
        )
        if url:
            line += f" | URL: {url}"
        return line + "\n"

    @staticmethod
    def _write_history_entry(
        dt: str,
        site: str,
        dtype: str,
        title: str,
        result: str,
        url: str | None = None,
    ) -> None:
        """
        Write a formatted entry to history_log.txt.

        Args:
            dt: Formatted datetime string.
            site: The source site (e.g., 'youtube', 'nebula').
            dtype: The download type (e.g., '1080', '720', 'podcast').
            title: The video/content title.
            result: The result string (e.g., 'SUCCESS', 'FAIL', 'SKIPPED (reason)').
        """
        try:
            history_path = HistoryLogger.HISTORY_PATH
            history_path.parent.mkdir(parents=True, exist_ok=True)
            with history_path.open("a", encoding="utf-8") as f:
                f.write(
                    HistoryLogger._format_entry(dt, site, dtype, title, result, url),
                )
        except OSError as exc:
            # Never allow history logging to crash downloading, but record it
            utils.log_exception(exc, "HistoryLogger failed to write to history_log.txt")

    def log(
        self,
        site: str,
        dtype: str,
        title: str,
        *,
        success: bool,
        url: str | None = None,
    ) -> None:
        """
        Log a download result to history_log.txt with timestamp.

        Args:
            site: The source site (e.g., 'youtube', 'nebula').
            dtype: The download type (e.g., '1080', '720', 'podcast').
            title: The video/content title.
            success: Whether the download succeeded.
            url: The video's webpage URL (optional).
        """
        dt = get_local_timestamp()
        result = "SUCCESS" if success else "FAIL"
        HistoryLogger._write_history_entry(dt, site, dtype, title, result, url)
        if self._on_log is not None:
            try:
                self._on_log({"dt": dt, "site": site, "dtype": dtype, "title": title, "result": result, "url": url})
            except (RuntimeError, AttributeError, TypeError, OSError) as exc:
                utils.log_exception(exc, "HistoryLogger: on_log callback failed")

    def log_skip(self, site: str, dtype: str, title: str, reason: str) -> None:
        """
        Log a skip result to history_log.txt with timestamp.

        Args:
            site: The source site (e.g., 'youtube', 'nebula').
            dtype: The download type (e.g., 'audio_playlists').
            title: The video/content title.
            reason: The reason for skipping (e.g., 'Short duration (<3 min)').
        """
        dt = get_local_timestamp()
        result = f"SKIPPED ({reason})"
        HistoryLogger._write_history_entry(dt, site, dtype, title, result)
        if self._on_log is not None:
            try:
                self._on_log({"dt": dt, "site": site, "dtype": dtype, "title": title, "result": result, "url": None})
            except (RuntimeError, AttributeError, TypeError, OSError) as exc:
                utils.log_exception(exc, "HistoryLogger: on_log callback failed")


_HISTORY_RE = re.compile(
    r"^\[(?P<dt>[^\]]+)\] Site: (?P<site>.+?) \| Type: (?P<dtype>.+?) \| Title: (?P<title>.+) \| Result: (?P<result_raw>.+)$",
)


def parse_history_log() -> list[dict]:
    """
    Read history_log.txt and return entries newest-first.

    Each entry is a dict with keys: dt, site, dtype, title, result, url.
    url is None for old entries that predate URL capture.
    """
    path = HistoryLogger.HISTORY_PATH
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open(encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.rstrip("\r\n")
            m = _HISTORY_RE.match(line)
            if not m:
                continue
            result_raw = m.group("result_raw")
            url: str | None = None
            if " | URL: " in result_raw:
                result_part, url_part = result_raw.rsplit(" | URL: ", 1)
                result_raw = result_part
                url = url_part.strip() or None
            entries.append(
                {
                    "dt": m.group("dt"),
                    "site": m.group("site"),
                    "dtype": m.group("dtype"),
                    "title": m.group("title"),
                    "result": result_raw,
                    "url": url,
                },
            )
    entries.reverse()
    return entries


class HistoryHook:
    """
    A yt-dlp progress hook that records per-item success into history_log.txt.

    Expects meta with keys: 'site' and 'type'. Deduplicates by video id and
    prefers logging on postprocessing merger completion; falls back to the
    first 'finished' event for cases without merging (e.g., audio-only).
    """

    def __init__(self, meta: dict | None, logger: HistoryLogger | None = None) -> None:
        """
        .

        Initialize the hook with optional metadata.

        Args:
            meta: Optional metadata dict with 'site' and 'type' keys.
            logger: Optional HistoryLogger instance; a plain one is created if omitted.
        """
        self.meta = meta or {}
        self._seen_ids: set[str] = set()
        self._logger = logger or HistoryLogger()

    def _infer_site(self, info: dict) -> str:
        site = (self.meta.get("site") or "").strip().lower()
        if not site or site == "unknown":
            ek = (info.get("extractor_key") or info.get("extractor") or "").lower()
            if "youtube" in ek:
                site = "youtube"
            elif "nebula" in ek:
                site = "nebula"
            elif ek:
                site = ek
            else:
                site = "unknown"
        return site

    def _vid_id(self, info: dict) -> str:
        return str(
            info.get("id")
            or info.get("_filename")
            or info.get("url")
            or info.get("playlist_id")
            or "unknown",
        )

    def __call__(self, d: dict) -> None:
        """
        .

        Process a download progress event and log to history if finished.

        Args:
            d: The progress event dict from yt-dlp.
        """
        try:
            status = d.get("status")
            info = d.get("info_dict") or {}
            vid = self._vid_id(info)
            # If already recorded for this id, ignore further events
            if vid in self._seen_ids:
                return

            postproc = (d.get("postprocessor") or "").lower()
            title = (
                info.get("title")
                or info.get("_filename")
                or info.get("id")
                or "(unknown title)"
            )
            site = self._infer_site(info)
            dtype = self.meta.get("type") or self.meta.get("source") or "unknown"

            url = info.get("webpage_url") or info.get("url") or None

            if status == "postprocessing":
                # Prefer logging when a merge or audio-extract postprocessor finishes
                if "merger" in postproc or "ffmpegextractaudio" in postproc:
                    self._logger.log(site, dtype, title, success=True, url=url)
                    self._seen_ids.add(vid)
            elif status == "finished":
                # Fallback for non-merged items (e.g., audio-only) or if no postprocessing runs
                self._logger.log(site, dtype, title, success=True, url=url)
                self._seen_ids.add(vid)
        except (AttributeError, TypeError, OSError) as exc:
            # Never let history logging break the download, but capture it
            utils.log_exception(
                exc,
                "HistoryHook failed while logging download history",
            )


class QYTQueue(QThread):
    """
    Manages a threaded download queue using QThread, emitting progress and completion signals.

    Handles download tasks from a queue, emits status updates via messageChanged, and signals when the queue is empty.
    Integrates with yt-dlp for downloading, supports progress hooks, and manages logger connections for error reporting.
    """

    message_changed = pyqtSignal(str)
    queue_empty = pyqtSignal()
    history_entry_added = pyqtSignal(dict)
    download_failed = pyqtSignal(dict)

    def __init__(self, download_queue: Queue) -> None:
        """
        Initialize the object with a given QThread-based download queue and sets the thread as a daemon.

        Args:
            download_queue (QThread): The thread managing the download queue.
        """
        super().__init__()

        self.downloadQueue = download_queue
        self.daemon = True
        self.executor = DownloadExecutor(message_callback=self.message_changed.emit)
        self._announced_deno_wait = False

    def _await_deno_warm(self) -> None:
        """
        Hold the first download until the startup Deno warm-up has finished.

        A download that starts on a cold DENO_DIR races the PO-token plugin's
        hard 15s script probe; the probe loses, raises subprocess.TimeoutExpired
        out of ydl.download(), and the item fails outright. Waiting costs a few
        seconds once per launch and only when the warm-up is still running.
        """
        if not self._announced_deno_wait and deno_warmup_pending():
            self.message_changed.emit("Preparing downloader (warming Deno cache)...")
        self._announced_deno_wait = True
        wait_for_deno_warm()

    def run(self) -> None:
        """Continuously processes download tasks from the queue in a background thread, emitting progress and completion messages, and signals when the queue becomes empty. Keeps the system awake during execution using a wake lock."""
        with keep.running():
            while True:
                item = self.downloadQueue.get()
                self._await_deno_warm()
                self.message_changed.emit(f"------  Downloading  ------\n{item[0]}")
                try:
                    self.download(item[0], item[1])
                # A failed item must never kill the worker: an exception escaping
                # QThread.run() aborts the whole PyQt process. yt-dlp reaches into
                # third-party plugins (e.g. the bgutil PO-token provider, which can
                # raise subprocess.TimeoutExpired), so the set of exception types
                # crossing this boundary is not enumerable in advance.
                except Exception as exc:
                    utils.log_exception(
                        exc, f"QYTQueue.run: unhandled error for {item[0]}"
                    )
                    self.message_changed.emit(
                        f"------  Download error  ------\n{item[0]}"
                    )
                    # Title is the URL here: no network title lookup in the crash path.
                    qmeta = (
                        (item[1] or {}).get("qmeta") if isinstance(item[1], dict) else {}
                    )
                    first_url = item[0][0] if item[0] else "(unknown)"
                    self.download_failed.emit(
                        make_failed_record(
                            item[0], qmeta, first_url, f"{type(exc).__name__}: {exc}"
                        ),
                    )
                else:
                    self.message_changed.emit(
                        f"------  Finished downloading  ------\n{item[0]}",
                    )
                if self.downloadQueue.empty():
                    self.queue_empty.emit()

    # Backward-compatibility wrapper methods that delegate to executor
    def _extract_title(self, urls: list) -> str:
        """Extract video title from first URL for error logging."""
        return self.executor._extract_title(urls)

    def _try_720_fallback(
        self,
        urls: list,
        options: dict,
        title: str,
        site: str,
        error_str: str,
    ) -> tuple[bool, str]:
        """Try downloading at 720p if the requested rung was gated (legacy entry point)."""
        return self.executor._try_720_fallback(urls, options, title, site, error_str)

    def _try_without_sponsorblock(
        self,
        urls: list,
        options: dict,
        title: str,
        site: str,
        dtype: str,
        error_str: str,
    ) -> tuple[bool, str]:
        """Try downloading without SponsorBlock if API unavailable."""
        return self.executor._try_without_sponsorblock(
            urls,
            options,
            title,
            site,
            dtype,
            error_str,
        )

    def download(self, urls: list, options: dict) -> None:
        """
        Download videos from URLs using yt-dlp with fallback strategies.

        Attempts a descent to lower resolution rungs (if the requested one is gated) and a retry
        without SponsorBlock (if API down) before reporting final failure.

        Args:
            urls (list): List of URLs to download.
            options (dict): yt-dlp options, including logger and progress_hooks.
        """
        try:
            # Ensure history hook is attached with metadata
            history_logger = HistoryLogger(on_log=self.history_entry_added.emit)
            progress_hooks = list(options.get("progress_hooks", []))
            progress_hooks.append(HistoryHook(options.get("qmeta"), logger=history_logger))
            # Captures per-entry errors that ignoreerrors="only_download" swallows
            # during playlist runs, so they never reach the `if not success` path.
            failure_hook = FailureHook(
                options.get("qmeta"), on_failure=self.download_failed.emit
            )
            progress_hooks.append(failure_hook)
            options["progress_hooks"] = progress_hooks

            # An entry that dies during *extraction* (unavailable, private,
            # removed) never reaches the progress hooks, so under
            # ignoreerrors="only_download" -- where the run correctly carries on to
            # the next entry -- its ERROR line through the logger is the only
            # signal it failed. Without ignoreerrors the error aborts the run and
            # the `if not success` path below files it, so wrapping there would
            # only file the same video twice under two keys.
            base_logger = options.get("logger")
            capture_extraction_errors = bool(options.get("ignoreerrors")) and (
                base_logger is not None
            )
            if capture_extraction_errors:
                options["logger"] = ErrorCapturingLogger(
                    base_logger, failure_hook.record_log_error
                )

            # Delegate download to executor
            try:
                success, error_message = self.executor.execute(urls, options)
            finally:
                if capture_extraction_errors:
                    options["logger"] = base_logger

            if not success:
                # Log the error if download failed
                self.message_changed.emit(error_message)
                logger = options.get("logger")
                if isinstance(logger, QLogger):
                    logger.exception(error_message)
                meta = options.get("qmeta") or {}
                site = meta.get("site", "unknown")
                dtype = meta.get("type", meta.get("source", "unknown"))
                # Extract title for logging
                title = self.executor._extract_title(urls)
                history_logger.log(site, dtype, title, success=False)
                self.download_failed.emit(
                    make_failed_record(urls, meta, title, error_message),
                )
            # Runs on both paths: a playlist can fail entries while execute()
            # still reports overall success. Phase 2's dedupe-by-key absorbs the
            # overlap when the hook and the batch record catch the same video.
            failure_hook.flush()
        except Exception as exc:
            utils.log_exception(exc, f"QYTQueue.download: unexpected error for {urls}")
            raise
        finally:
            for hook in options.get("progress_hooks", []):
                if hasattr(hook, "infoChanged"):
                    hook.deleteLater()
            logger = options.get("logger")
            if isinstance(logger, QLogger):
                logger.message_changed.disconnect()
                logger.message_changed.connect(self.message_changed.emit)


# class QYT(QObject):
#     def download(self, urls, options):
#         Thread(target=self._execute, args=(urls, options), daemon=True).start()

#     def _execute(self, urls, options):
#         with YoutubeDL(options) as ydl:
#             ydl.download(urls)
#         for hook in options.get("progress_hooks", []):
#             if isinstance(hook, QHook):
#                 hook.deleteLater()
#         logger = options.get("logger")
#         if isinstance(logger, QLogger):
#             logger.deleteLater()

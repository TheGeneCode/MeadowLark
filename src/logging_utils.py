"""Logging utilities for exception handling and diagnostics."""

import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

from genekit.logging import configure_logging

from src.config import ERROR_LOG_PATH, YTDLP_DEBUG_LOG_PATH, YTDLP_VERBOSE

logger = logging.getLogger(__name__)

_YTDLP_DEBUG_LOGGER_NAME = "meadowlark.ytdlp"
# One rotation's worth is plenty: a single verbose download is a few hundred KB,
# and only the most recent failure is ever of interest.
_YTDLP_DEBUG_MAX_BYTES = 5 * 1024 * 1024
_YTDLP_DEBUG_BACKUPS = 2
# QLogger.__init__ calls get_ytdlp_debug_logger() on every download; if two
# downloads start close enough together to run on different threads, both can
# see debug_logger.handlers as empty before either attaches one, doubling every
# subsequent log line (and doubling RotatingFileHandler's open file handles).
# Guards only the check-then-act window below, not logging calls themselves.
_debug_logger_lock = threading.Lock()


def get_local_timestamp() -> str:
    """Return current local timestamp as formatted string 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def log_exception(exc: Exception, context: str | None = None) -> None:
    """
    Log an exception to the global logger configured to write to error_log.txt.

    This helper is used by the app to ensure all swallowed exceptions are
    recorded with a full traceback.

    If logging has not yet been configured elsewhere, configure it via
    ``genekit.logging`` so that errors are still written to the expected file.

    Args:
        exc: The exception to log.
        context: Optional context string to prepend to the exception message.
    """
    if not logging.getLogger().hasHandlers():
        configure_logging("ERROR", log_file=ERROR_LOG_PATH, console="none")
    msg = f"{context}: {exc}" if context else str(exc)
    logger.exception(msg)


def get_ytdlp_debug_logger() -> logging.Logger | None:
    """
    Return the logger that captures yt-dlp's own output, or None when disabled.

    yt-dlp talks to the app through the ``logger`` option (see ``QLogger``), so
    its debug stream never reaches disk: the root logger is configured at ERROR
    and only the final exception string lands in ``error_log.txt``. That is not
    enough to diagnose a media-stage HTTP 403, which turns on facts only the
    debug stream carries -- the player client that served the chosen format and
    whether its media URL included a ``pot=`` token.

    Enabled by ``VID_DL_YTDLP_VERBOSE``. The handler is attached once and the
    logger is detached from the root logger so this capture cannot pollute
    ``error_log.txt``.

    Returns:
        A configured logger writing to ``YTDLP_DEBUG_LOG_PATH``, or None when
        the setting is off or the log file cannot be opened.
    """
    if not YTDLP_VERBOSE:
        return None

    debug_logger = logging.getLogger(_YTDLP_DEBUG_LOGGER_NAME)
    if debug_logger.handlers:
        return debug_logger

    with _debug_logger_lock:
        # Re-check inside the lock: another thread may have attached the
        # handler while this one was waiting.
        if debug_logger.handlers:
            return debug_logger

        try:
            YTDLP_DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                YTDLP_DEBUG_LOG_PATH,
                maxBytes=_YTDLP_DEBUG_MAX_BYTES,
                backupCount=_YTDLP_DEBUG_BACKUPS,
                encoding="utf-8",
            )
        except OSError as exc:
            # Diagnostics must never take the app down with them.
            log_exception(exc, "Could not open the yt-dlp debug log")
            return None

        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        debug_logger.addHandler(handler)
        debug_logger.setLevel(logging.DEBUG)
        debug_logger.propagate = False
        return debug_logger

"""Podcast filtering and categorization helpers for episode processing."""

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

import utils
from src.config import (
    HTTP_OK,
    HTTP_REQUEST_TIMEOUT_SECONDS,
    PODCAST_MIN_DURATION_SECONDS,
)
from src.logging_utils import get_local_timestamp

# Extractor key prefixed to each download-archive line ("youtube <video_id>").
ARCHIVE_EXTRACTOR = "youtube"


def parse_video_timestamp(entry: dict[str, Any]) -> float | None:
    """
    Extract timestamp from entry, trying 'timestamp' field then 'upload_date'.

    Args:
        entry: A yt-dlp entry dict with optional 'timestamp' or 'upload_date' fields.

    Returns:
        Timestamp as a float (seconds since epoch), or None if not found/parseable.
    """
    ts = entry.get("timestamp")
    if not ts and entry.get("upload_date"):
        try:
            ts = (
                datetime.strptime(entry.get("upload_date"), "%Y%m%d")
                .replace(tzinfo=UTC)
                .timestamp()
            )
        except (ValueError, TypeError) as exc:
            ts = None
            utils.log_exception(exc, "Failed to parse upload_date timestamp")
    return ts


def load_downloaded_video_ids(archive_path: str | None) -> set[str]:
    """
    Load set of already-downloaded video IDs from archive file.

    Archive file format: lines like "youtube <video_id>" or similar.
    Only the last space-separated token (video_id) is extracted.

    Args:
        archive_path: Path to archive file, or None if not configured.

    Returns:
        Set of video IDs that have already been downloaded.
    """
    existing_ids: set[str] = set()
    if not archive_path:
        return existing_ids

    archive_file = Path(archive_path)
    if not archive_file.exists():
        return existing_ids

    try:
        with archive_file.open("r", encoding="utf-8") as f:
            for line in f:
                stripped_line = line.strip()
                if not stripped_line:
                    continue
                parts = stripped_line.split()
                if parts:
                    existing_ids.add(parts[-1])
    except (OSError, UnicodeDecodeError) as exc:
        utils.log_exception(
            exc,
            "Failed to read download archive for podcast filtering",
        )

    return existing_ids


def append_downloaded_video_ids(
    archive_path: str | Path,
    video_ids: Iterable[str],
    existing_ids: set[str] | None = None,
) -> list[str]:
    """
    Append video IDs not already in the download archive, in one write.

    The writer matching load_downloaded_video_ids: each ID becomes a
    "youtube <video_id>" line, the format yt-dlp's download_archive option checks.

    Args:
        archive_path: Path to the archive file; created if missing.
        video_ids: IDs to record. Empty and repeated IDs are dropped, order kept.
        existing_ids: Already-archived IDs to dedupe against. Loaded from the
            archive when None; when given, updated in place with the IDs written,
            so a caller's running set stays in sync across calls.

    Returns:
        The IDs actually written, in input order.

    Raises:
        OSError: If the archive cannot be opened or written.
    """
    known = load_downloaded_video_ids(str(archive_path)) if existing_ids is None else existing_ids
    new_ids = list(dict.fromkeys(vid for vid in video_ids if vid and vid not in known))
    if new_ids:
        with Path(archive_path).open("a", encoding="utf-8") as archive:
            archive.writelines(f"{ARCHIVE_EXTRACTOR} {vid}\n" for vid in new_ids)
        known.update(new_ids)
    return new_ids


def format_timestamp_readable(ts: float | None) -> str:
    """
    Convert timestamp to human-readable date string.

    Args:
        ts: Timestamp as float (seconds since epoch), or None.

    Returns:
        Formatted date string like "2025-03-24", or "(unknown)" if ts is None.
    """
    if ts is None:
        return "(unknown)"
    try:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        return dt.strftime("%Y-%m-%d")
    except (OSError, ValueError) as exc:
        utils.log_exception(exc, f"format_timestamp_readable: invalid timestamp {ts}")
        return "(unknown)"


def append_to_archive_and_mark_skipped(
    archive_path: str | None,
    vid: str,
    existing_ids: set[str],
    title: str,
    messages: list[str],
    reason: str = "Update exception",
) -> None:
    """
    Append video to archive and add skip message with customizable reason.

    Skips videos to avoid re-processing them in future checks. Used for videos
    with "(Update)" titles, short duration, or other skip conditions.

    Args:
        archive_path: Path to archive file, or None.
        vid: Video ID to add to archive.
        existing_ids: Set of already-archived IDs (modified in place).
        title: Video title for logging.
        messages: List of messages to append to (modified in place).
        reason: Reason for skipping (e.g., "Update exception", "Short duration").
    """
    if archive_path:
        try:
            append_downloaded_video_ids(archive_path, [vid], existing_ids)
        except OSError as exc:
            utils.log_exception(
                exc,
                "Failed to write skip marker to download archive",
            )

    timestamp_str = get_local_timestamp()
    messages.append(
        f"Video skipped because of {reason}: '{title}' (ID: {vid}) at {timestamp_str}",
    )


def parse_scheduled_time_from_error(error_str: str) -> float | None:
    """
    Extract scheduled start timestamp from error message.

    Handles patterns like:
    - "Premieres in X hours" or "Premieres in X days"
    - "Scheduled to begin YYYY-MM-DD HH:MM:SS UTC" or "YYYY-MM-DD HH:MM UTC"

    Args:
        error_str: Error message string from yt-dlp.

    Returns:
        Timestamp of scheduled start, or None if no pattern matches.
    """
    # Try "will begin in X hours" or "will begin in X days"
    match = re.search(r"will begin in (\d+)\s+(hours?|days?)", error_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        multiplier = 3600 if "hour" in unit else 86400
        return datetime.now(tz=UTC).timestamp() + (value * multiplier)

    # Try "scheduled to begin" with date patterns
    match = re.search(r"scheduled to begin (.+?)(?:\s+UTC)?$", error_str)
    if match:
        date_str = match.group(1).strip()
        # Try date/time formats in order; avoid try-except in loop
        dt = _try_parse_datetime(date_str, ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"))
        if dt:
            return dt.timestamp()

    return None


def parse_video_id_from_error(error_str: str) -> str | None:
    """
    Extract a YouTube video ID from a yt-dlp extraction error message.

    Upcoming premieres / scheduled live events fail extraction before any
    entry is yielded, but yt-dlp's error string still names the offending
    video using its standard ``[youtube] <id>:`` prefix, e.g.::

        ERROR: [youtube] dQw4w9WgXcQ: This live event will begin in 2 hours.

    Video IDs are exactly 11 characters from ``[0-9A-Za-z_-]``. Requiring the
    trailing colon and the ``[youtube]`` (not ``[youtube:tab]``) prefix keeps
    this from matching 34-character playlist IDs.

    Args:
        error_str: Error message string from yt-dlp.

    Returns:
        The 11-character video ID, or None if no pattern matches.
    """
    match = re.search(r"\[youtube\]\s+([0-9A-Za-z_-]{11}):", error_str)
    return match.group(1) if match else None


def _try_parse_datetime(
    date_str: str,
    formats: tuple[str, ...],
) -> datetime | None:
    """
    Try parsing date string with multiple formats.

    Args:
        date_str: Date/time string to parse.
        formats: Tuple of format strings to try in order.

    Returns:
        Parsed datetime with UTC timezone, or None if no format matched.
    """
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(
                tzinfo=UTC,
            )
        except ValueError:
            continue
    return None


def check_sponsorblock_for_video_id(video_id: str) -> bool:
    """
    Check if SponsorBlock has segments for the given YouTube video ID.

    Args:
        video_id: YouTube video ID to check.

    Returns:
        True if SponsorBlock API returns segments (non-empty list), False otherwise.
        Also returns False on API errors.
    """
    try:
        url = f"https://sponsor.ajay.app/api/skipSegments?videoID={video_id}"
        r = requests.get(url, timeout=HTTP_REQUEST_TIMEOUT_SECONDS)
        if r.status_code == HTTP_OK:
            data = r.json()
            return bool(data)
    except Exception as exc:
        # Catch all exceptions to ensure API issues don't crash the download
        utils.log_exception(exc, "SponsorBlock API check failed")
    return False


# Re-export configuration constants for backward compatibility
__all__ = [
    "HTTP_OK",
    "PODCAST_MIN_DURATION_SECONDS",
    "append_downloaded_video_ids",
    "append_to_archive_and_mark_skipped",
    "check_sponsorblock_for_video_id",
    "format_timestamp_readable",
    "load_downloaded_video_ids",
    "parse_scheduled_time_from_error",
    "parse_video_timestamp",
]

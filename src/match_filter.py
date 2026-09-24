"""Factory for building yt-dlp match_filter functions that handle live/upcoming videos."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logging_utils import log_exception

if TYPE_CHECKING:
    from collections.abc import Callable


def build_match_filter(
    source: str,
    add_to_queue_fn: Callable[[str, str, str | None], None],
    log_fn: Callable[[str], None],
) -> Callable[[dict, bool], str | None]:
    """Build a yt-dlp match_filter that skips live/upcoming videos and queues them for later."""

    def _mf(info: dict, incomplete: bool) -> str | None:
        try:
            is_live = info.get("is_live")
            live_status = info.get("live_status")
            availability = info.get("availability")
            if availability == "scheduled":
                return "Skipping: scheduled"
            if is_live or live_status in ("is_live", "is_upcoming"):
                url = info.get("webpage_url") or info.get("original_url") or info.get("url")
                if url:
                    playlist_id = info.get("playlist_id")
                    add_to_queue_fn(url, source, playlist_id)
                    log_fn(f"Queued live for later: {url} [{source}]")
                return "Skipping live; queued for later"
        except Exception as exc:
            log_exception(exc, "Error in match_filter")
            return None
        return None

    return _mf

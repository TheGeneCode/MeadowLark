"""Podcast helper functions for extracting latest accessible entries and caching."""

from typing import Any

from src.exceptions import PodcastResolutionError
from src.ydl_utils import extract_playlist_info

MAX_LOOKAHEAD = 5
"""Max number of lookahead iterations when searching for accessible podcast entries."""


def fetch_latest_accessible_entry(
    url: str,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """
    Fetch the latest accessible (non-private) entry from a playlist URL.

    Return a tuple suitable for podcast filtering operations.

    This function employs an incremental lookahead strategy (increasing
    ``playlistend``) rather than fetching the entire playlist when private
    videos are encountered.

    ``url`` is a playlist or channel URL. First, attempt a lightweight extraction
    limited to the first entry (``playlistend=1``). If the call succeeds but the
    entry's ``title`` starts with ``"Private video"`` or the extraction raises any
    exception whose string contains that phrase, the result is considered a
    "private latest" case.

    In that situation, perform a second extraction with ``ignoreerrors=True``
    (no ``playlistend``) and walk the returned list backwards until finding a
    non-private entry. That entry is returned in a one-item list and ``skipped``
    is set to ``True``. ``skipped`` is ``False`` if no private video was
    encountered. The original ``info`` dict from yt-dlp is also returned so
    that callers can derive a playlist label.

    The original exception is re-raised if the playlist has no accessible
    entries; callers may let it bubble up so the existing error-path logic can apply.

    Args:
        url: A playlist or channel URL to process.

    Returns:
        A tuple containing:
        - entries (list): List containing the latest accessible entry.
        - skipped (bool): True if a private video was skipped, False otherwise.
        - info (dict): The original info dict from yt-dlp for playlist metadata.

    Raises:
        DownloadError: If the playlist cannot be resolved or has no accessible entries.
        ExtractorError: If extraction fails with an error other than private videos.
        OSError: If network or file system errors occur.
        ValueError: If yt-dlp raises a value error during extraction.
    """
    original_exc: Exception | None = None
    private_video_case = False
    # try progressively larger tail slices rather than full scrape
    for n in range(1, MAX_LOOKAHEAD + 1):
        try:
            info = extract_playlist_info(url, extra_opts={"playlist_items": str(n)})
        except Exception as exc:
            original_exc = exc
            if "Private video" in str(exc):
                private_video_case = True
                # try again with larger playlistend
                continue
            # some other error - propagate immediately
            raise

        entries = info.get("entries", [info])
        if not entries:
            # nothing at all; give up
            break
        # examine the *last* entry returned (should be the n'th-from-end)
        cand = entries[-1]
        title = cand.get("title", "")
        if isinstance(title, str) and title.startswith("Private video"):
            # private, keep looking
            private_video_case = True
            original_exc = original_exc or Exception("Private video")
            continue
        # found a non-private entry
        return [cand], bool(n > 1 or original_exc is not None), info

    # exhausted lookahead window or playlist ended
    if original_exc and not private_video_case:
        raise original_exc

    raise PodcastResolutionError(PodcastResolutionError.MSG)

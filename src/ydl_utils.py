"""YDL utility functions for common yt-dlp operations."""

import yt_dlp

from .ydl_options import build_shared_extraction_opts

_QUIET_YDL_OPTS: dict[str, bool] = {"quiet": True, "no_warnings": True}


def extract_playlist_info(
    url: str,
    playlistend: int | None = None,
    ydl_class: type | None = None,
    extra_opts: dict | None = None,
) -> dict:
    """
    Extract playlist/video info with standard quiet options.

    Includes the shared PO-token provider wiring (see
    ``build_shared_extraction_opts``) so metadata-only lookups -- e.g.
    ``meadowlark.pyw``'s on-demand "open latest episode" resolution and
    ``DownloadExecutor._extract_title``'s error-path title lookup -- don't
    fall back to the bgutil provider's stale default server_home and hit its
    cold-cache Deno probe budget.

    Args:
        url: The URL to extract info from.
        playlistend: Optional limit on number of entries to extract.
        ydl_class: Optional custom YoutubeDL class for injection.
        extra_opts: Optional extra options merged after the quiet baseline
            (e.g. ``{"cookiefile": "/path/to/cookies.txt"}``).

    Returns:
        Dictionary containing extracted info.
    """
    if ydl_class is None:
        ydl_class = yt_dlp.YoutubeDL
    opts: dict = {**build_shared_extraction_opts(), **_QUIET_YDL_OPTS}
    if extra_opts:
        opts.update(extra_opts)
    if playlistend:
        opts["playlistend"] = playlistend
    with ydl_class(opts) as ydl:
        return ydl.extract_info(url, download=False)


def extract_video_entries(
    url: str,
    extract_flat: bool | str = True,
    ydl_class: type | None = None,
    cookiefile: str | None = None,
) -> list:
    """
    Extract entries from URL (playlist or video).

    Includes the shared PO-token provider wiring (see
    ``build_shared_extraction_opts``); see ``extract_playlist_info`` for why.

    Args:
        url: The URL to extract entries from.
        extract_flat: Whether to extract flat info (True) or full info (False).
        ydl_class: Optional custom YoutubeDL class for injection.
        cookiefile: Optional cookies file path, so private/unlisted playlists
            enumerate their entries instead of coming back empty.

    Returns:
        List of entry dictionaries.
    """
    if ydl_class is None:
        ydl_class = yt_dlp.YoutubeDL
    opts = {
        **build_shared_extraction_opts(),
        **_QUIET_YDL_OPTS,
        "extract_flat": extract_flat,
    }
    if cookiefile:
        opts["cookiefile"] = str(cookiefile)
    with ydl_class(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("entries", [info])


def extract_release_info(
    url: str,
    cookiefile: str | None = None,
    ydl_class: type | None = None,
) -> dict:
    """
    Metadata-only probe that tolerates a video with no downloadable formats.

    An upcoming premiere has no formats, so a normal extraction dies inside
    ``raise_no_formats`` ("Premieres in 6 hours") before any info_dict is built --
    which is exactly why such items reach the failed-downloads store today.
    ``ignore_no_formats_error`` downgrades that to a warning so the info_dict comes
    back carrying ``live_status`` and ``release_timestamp``. ``noplaylist`` keeps a
    watch URL that also names a list from expanding into the whole playlist.

    Args:
        url: The video URL to probe.
        cookiefile: Optional cookies file path for age-restricted lookups.
        ydl_class: Optional custom YoutubeDL class for injection.

    Returns:
        The extracted info dict, or {} when yt-dlp returns nothing.
    """
    if ydl_class is None:
        ydl_class = yt_dlp.YoutubeDL
    opts: dict = {
        **build_shared_extraction_opts(),
        **_QUIET_YDL_OPTS,
        "skip_download": True,
        "ignore_no_formats_error": True,
        "noplaylist": True,
    }
    if cookiefile:
        opts["cookiefile"] = str(cookiefile)
    with ydl_class(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}

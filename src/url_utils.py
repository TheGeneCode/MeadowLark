"""URL parsing utilities."""

from urllib.parse import parse_qs, urlparse

_YOUTUBE_WATCH_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")
_YOUTUBE_SHORT_HOSTS = ("youtu.be", "www.youtu.be")
_WEB_SCHEMES = ("http://", "https://")


def web_url(value: object) -> str | None:
    """
    Return *value* if it is an http(s) URL, else None.

    Gate for anything handed to ``webbrowser``: on Windows it passes the string
    to ``os.startfile``, and when that fails (a bare video/playlist id is not a
    file) it silently falls through to the next registered browser - msedge.exe
    - instead of the user's default, opening the raw string as a search.
    """
    if isinstance(value, str) and value.strip().lower().startswith(_WEB_SCHEMES):
        return value.strip()
    return None


def extract_video_id(url: str | None) -> str | None:
    """Return the YouTube video ID from a watch URL or youtu.be short URL, or None."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if parsed.netloc in _YOUTUBE_WATCH_HOSTS:
            ids = parse_qs(parsed.query or "").get("v")
            return ids[0] if ids and ids[0] else None
        if parsed.netloc in _YOUTUBE_SHORT_HOSTS:
            first_segment = parsed.path.lstrip("/").split("/")[0]
            return first_segment or None
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def extract_playlist_id(url: str) -> str | None:
    """Return the YouTube playlist ID from a URL's `list` query parameter, or None."""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query or "")
        ids = qs.get("list")
        return ids[0] if ids else None
    except (ValueError, AttributeError, TypeError):
        return None

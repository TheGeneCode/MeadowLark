"""Playlist and URL detection utilities."""

from pathlib import Path

from .config import PLAYLISTS_AUDIO_FILE, playlist_path_for_height
from .logging_utils import log_exception
from .resolutions import height_from_source, playlist_file_key
from .settings_dialog import get_setting
from .url_utils import extract_playlist_id


def detect_site_from_urls(urls: list[str]) -> str:
    """
    Best-effort detection of site from a list of URLs.

    Args:
        urls: List of URLs to analyze.

    Returns:
        Site identifier ('youtube', 'nebula', or 'unknown').
    """
    all_urls = " ".join(urls or []).lower()
    if "youtube.com" in all_urls or "youtu.be" in all_urls:
        return "youtube"
    if "nebula" in all_urls or "watchnebula" in all_urls:
        return "nebula"
    return "unknown"


def is_primitive_technology(info: dict) -> bool:
    """
    Detect Primitive Technology channel videos.

    Prefer exact channel/uploader detection when available, and fall back to
    checking the common title prefix 'Primitive Technology:' when metadata is limited.

    Args:
        info: yt-dlp info dictionary.

    Returns:
        True if video is from Primitive Technology channel, False otherwise.
    """
    try:
        # Channel/uploader info when available
        channel = (info.get("channel") or info.get("channel_id") or "").lower()
        uploader = (info.get("uploader") or info.get("uploader_id") or "").lower()
        if "primitive technology" in channel or "primitive technology" in uploader:
            return True
        # Fallback on the well-known title prefix (case-insensitive, robust to whitespace)
        title = (info.get("title") or "").strip().lower()
        if title.startswith("primitive technology:"):
            return True
    except (AttributeError, TypeError) as exc:
        log_exception(exc, "Error detecting Primitive Technology channel")
        return False
    return False


def get_playlist_file_for_source(source: str) -> str | None:
    """
    Return the on-disk playlist file path for a given source key.

    Args:
        source: Source identifier ('<height>playlists' for any enabled rung, or
            'audio_playlists').

    Returns:
        File path string or None if source is not recognized.
    """
    if source == "audio_playlists":
        return get_setting("VID_DL_PLAYLISTS_AUDIO_FILE") or str(PLAYLISTS_AUDIO_FILE)
    height = height_from_source(source)
    if height is None or not source.endswith("playlists"):
        return None
    configured = get_setting(playlist_file_key(height))
    return str(configured) if configured else str(playlist_path_for_height(height))


_PLAYLIST_TEMPLATE = """\
# MeadowLark Playlist File
# Add one playlist per line. To give a playlist a name, put a #Name line directly above it.
#
# You can paste a full YouTube playlist URL:
#   #My Favorite Series
#   https://www.youtube.com/watch?v=xxxxxxxxxxx&list=PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#
# Or just the playlist ID (the part after "list=" in a YouTube URL):
#   #Another Series
#   PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#
# Lines that start with # are ignored by the app — remove the leading # to activate an entry.
"""


def write_template_playlist_file(path: Path) -> None:
    """Create *path* (and its parent dir) with the boilerplate playlist template."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PLAYLIST_TEMPLATE, encoding="utf-8")


def load_playlist_urls(path: Path) -> list[str]:
    """
    Return all non-blank, non-comment lines from a playlist file as raw URL strings.

    Returns an empty list if the file does not exist or cannot be read.
    """
    if not path.exists():
        write_template_playlist_file(path)
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except (OSError, UnicodeDecodeError) as exc:
        log_exception(exc, f"Failed to read playlist file: {path}")
        return []


def load_playlist_comments_for_source(source: str) -> dict[str, str]:
    """
    Return {playlist_id: comment} for all commented entries in the source playlist file.

    Parses lines of the form:
        #Some Comment
        https://www.youtube.com/playlist?list=PLxxx
    and maps each playlist ID to its preceding comment.

    Args:
        source: Source identifier ('<height>playlists' for any enabled rung, or
            'audio_playlists').

    Returns:
        Dict mapping playlist_id -> comment text. Empty if file missing or no comments.
    """
    playlist_file = get_playlist_file_for_source(source)
    if not playlist_file:
        return {}
    path = Path(playlist_file)
    if not path.exists():
        return {}

    comments: dict[str, str] = {}
    last_comment: str | None = None
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    last_comment = line[1:].strip()
                else:
                    if last_comment:
                        pl_id = extract_playlist_id(line)
                        if not pl_id and not line.startswith("http"):
                            pl_id = (
                                line  # bare playlist ID (e.g. PLRWvNQVqAeWIafhw3XHnmz_EHOp32qoZW)
                            )
                        if pl_id:
                            comments[pl_id] = last_comment
                    last_comment = None
    except OSError as exc:
        log_exception(exc, f"Failed to read playlist file: {playlist_file}")
    return comments

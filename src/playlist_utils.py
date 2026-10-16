"""Playlist and URL detection utilities."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .config import PLAYLISTS_AUDIO_FILE, playlist_path_for_height
from .logging_utils import log_exception
from .resolutions import height_from_source, playlist_file_key
from .settings_dialog import get_str_setting
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
        return get_str_setting("VID_DL_PLAYLISTS_AUDIO_FILE", str(PLAYLISTS_AUDIO_FILE))
    height = height_from_source(source)
    if height is None or not source.endswith("playlists"):
        return None
    return get_str_setting(playlist_file_key(height), str(playlist_path_for_height(height)))


# Previous template text, kept only so its header lines are ignored when parsing old files.
_PLAYLIST_TEMPLATE_V1: Final = """\
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

_PLAYLIST_TEMPLATE: Final = """\
# MeadowLark Playlist File
# One playlist per line: a full YouTube playlist URL, or just the playlist ID
# (the part after "list=" in a YouTube URL).
#
# A line starting with # is a NAME for every playlist below it, up to the next # line.
# In the podcast (audio) file the name is the folder episodes are saved in,
# so several playlists under one name share one folder (a category):
#   #Tech News
#   https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#   PLyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
# Prefer one folder per show? Put a #Name above every link instead.
# A line with just # ends a category. Putting # in front of a link pauses it
# without ending its category. Video files always save under the YouTube title.
#
"""

_TEMPLATE_COMMENT_LINES: Final[frozenset[str]] = frozenset(
    stripped
    for template in (_PLAYLIST_TEMPLATE, _PLAYLIST_TEMPLATE_V1)
    for raw in template.splitlines()
    if (stripped := raw.strip()).startswith("#") and stripped != "#"
)

# yt-dlp's own bare-playlist-id shape. Real ids are random mixed-case base64 and at
# least 18 chars (PL + 16), so a one-word name such as "#PLAYTHROUGHS",
# "#TLDR-weekly-news" or "#LLMresearchpapers" (all-caps, too short, or no digit) stays
# a name rather than a paused entry.
_BARE_PLAYLIST_ID_RE: Final = re.compile(r"(?:PL|LL|EC|UU|FL|RD|UL|TL|PU|OLAK5uy_)[0-9A-Za-z_-]+")
_MIN_BARE_PLAYLIST_ID_LEN: Final = 18


def _is_paused_entry(body: str) -> bool:
    """Return True if a ``#`` line's text is a commented-out link, not a name."""
    if body.lower().startswith(("http://", "https://", "www.")):
        return True
    return (
        len(body) >= _MIN_BARE_PLAYLIST_ID_LEN
        and bool(_BARE_PLAYLIST_ID_RE.fullmatch(body))
        and not body.isupper()
        and any(ch.isdigit() for ch in body)
    )


@dataclass(frozen=True, slots=True)
class PlaylistFileEntry:
    """One active line of a Playlist File and the label (``#Name``) it falls under."""

    url: str
    label: str | None


def parse_playlist_lines(lines: Iterable[str]) -> list[PlaylistFileEntry]:
    """
    Parse Playlist File lines into entries, each carrying the nearest ``#Name`` above it.

    A ``#Name`` line labels every link below it until the next ``#`` line. A lone ``#``
    clears the label; a commented-out link (``#https://…`` / ``#PLxxxx…``) and the file
    template's own header lines are skipped without touching it. Blank lines are ignored.
    """
    entries: list[PlaylistFileEntry] = []
    label: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            entries.append(PlaylistFileEntry(url=line, label=label))
            continue
        body = line[1:].strip()
        if not body:
            label = None
        elif line not in _TEMPLATE_COMMENT_LINES and not _is_paused_entry(body):
            label = body
    return entries


def playlist_id_for_line(line: str) -> str | None:
    """Return the playlist id for a Playlist File line: ``list=`` param, else the bare id itself."""
    pl_id = extract_playlist_id(line)
    if pl_id:
        return pl_id
    return None if line.startswith("http") else line


def labels_by_playlist_id(entries: Iterable[PlaylistFileEntry]) -> dict[str, str]:
    """Return ``{playlist_id: label}`` for every labelled entry with a resolvable playlist id."""
    return {
        pl_id: entry.label
        for entry in entries
        if entry.label and (pl_id := playlist_id_for_line(entry.url))
    }


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
            return [entry.url for entry in parse_playlist_lines(f)]
    except (OSError, UnicodeDecodeError) as exc:
        log_exception(exc, f"Failed to read playlist file: {path}")
        return []


def load_playlist_entries_for_source(source: str) -> list[PlaylistFileEntry] | None:
    """
    Return the parsed entries of the source's Playlist File.

    None when the source has no Playlist File, the file is missing, or it cannot be read
    (logged). Does not create a template; that stays load_playlist_urls' job.
    """
    playlist_file = get_playlist_file_for_source(source)
    if not playlist_file:
        return None
    path = Path(playlist_file)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return parse_playlist_lines(f)
    except (OSError, UnicodeDecodeError) as exc:
        log_exception(exc, f"Failed to read playlist file: {playlist_file}")
        return None


def load_playlist_comments_for_source(source: str) -> dict[str, str]:
    """
    Return {playlist_id: comment} for all commented entries in the source playlist file.

    A ``#Some Comment`` line applies to every link below it until the next ``#`` line:
        #Some Comment
        https://www.youtube.com/playlist?list=PLxxx
        PLyyy
    maps both PLxxx and PLyyy to "Some Comment".

    Args:
        source: Source identifier ('<height>playlists' for any enabled rung, or
            'audio_playlists').

    Returns:
        Dict mapping playlist_id -> comment text. Empty if file missing or no comments.
    """
    return labels_by_playlist_id(load_playlist_entries_for_source(source) or [])

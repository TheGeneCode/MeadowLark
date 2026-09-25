"""Which playlist a single video belongs to, for re-downloading it into its playlist folder."""

from collections.abc import Callable
from dataclasses import dataclass

from yt_dlp.extractor.youtube import YoutubePlaylistIE

from .resolutions import height_from_source, playlist_source_key
from .url_utils import extract_playlist_id, extract_video_id

YOUTUBE_PLAYLIST_URL = "https://www.youtube.com/playlist?list={}"
MatchFilter = Callable[..., str | None]


def playlist_id_of(value: object) -> str | None:
    """
    Return the YouTube playlist id named by *value*, or None.

    Accepts a URL carrying ``list=`` (playlist page or watch URL) and a bare
    playlist id, which is how ``<height>playlists.txt`` files often list them.
    A watch URL without ``list=``, a video id, or a non-string gives None.
    """
    if not isinstance(value, str) or not (value := value.strip()):
        return None
    if playlist_id := extract_playlist_id(value):
        return playlist_id
    return value if YoutubePlaylistIE.suitable(value) else None


@dataclass(frozen=True)
class EntryTarget:
    """A single video to fetch by walking its playlist."""

    playlist_url: str
    video_id: str


def playlist_entry_target(url: object, source: object, playlist_id: object) -> EntryTarget | None:
    """
    Say whether a single-entry re-download must go through its playlist URL.

    Only video playlist sources (``<height>playlists``) need it: their output template is
    ``%(playlist)s/%(playlist_index)s - …``, which a bare watch URL renders as ``NA/NA - …``.
    ``audio_playlists`` has its own fix (``build_podcast_outtmpl``). None keeps the caller's
    old behaviour.
    """
    if not isinstance(source, str) or not isinstance(url, str):
        return None
    height = height_from_source(source)
    if height is None or source != playlist_source_key(height):
        return None
    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return None
    video_id = extract_video_id(url)
    if video_id is None:
        return None
    return EntryTarget(YOUTUBE_PLAYLIST_URL.format(playlist_id.strip()), video_id)


def restrict_to_entry(video_id: str, inner: MatchFilter | None = None) -> MatchFilter:
    """
    Build a match_filter that lets exactly one playlist entry through.

    yt-dlp calls it as ``match_filter(info, incomplete=...)``. It calls it first with the
    playlist itself (no ``id`` key, so it passes), then once per flat entry *before* extracting
    that entry (``incomplete=True``), so non-target entries cost nothing but a log line.
    Anything that passes the id check is handed on to *inner* (the live/upcoming filter).
    """

    def _mf(info: dict, incomplete: bool) -> str | None:
        entry_id = info.get("id")
        if entry_id is not None and entry_id != video_id:
            return f"Skipping {entry_id}: re-downloading only {video_id}"
        return inner(info, incomplete) if inner is not None else None

    return _mf

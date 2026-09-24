"""Persistent store for failed downloads and the progress hook that captures them."""

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from genekit.atomic_write import atomic_write_text
from yt_dlp.extractor.youtube import YoutubePlaylistIE

from .logging_utils import get_local_timestamp, log_exception
from .url_utils import extract_video_id

FailedRecord = dict  # keys: key, urls, source, site, title, failed_at, error

_MAX_ERROR_LEN = 500

# yt-dlp reports a playlist entry that dies during *extraction* (unavailable,
# private, removed, members-only, geo-blocked) only through its logger:
# InfoExtractor.extract stamps the extractor name and video id onto every
# ExtractorError, YoutubeDL.report_error prefixes "ERROR:", and to_stderr hands
# the whole line to params["logger"].error. No progress hook fires, because the
# entry never reached the download stage - so with ignoreerrors="only_download"
# this line is the only evidence the entry existed and failed.
_ENTRY_ERROR_RE = re.compile(
    r"ERROR:\s+\[(?P<ie>[^\]\s]+)\]\s+(?P<vid>[\w-]+):\s+(?P<reason>.+)",
    re.DOTALL,
)
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={}"
_YOUTUBE_PLAYLIST_URL = "https://www.youtube.com/playlist?list={}"
_UNKNOWN_TITLE = "(unknown title)"


def _untitled(ident: str | None) -> str:
    """
    Display title for a failure whose real title is unknowable.

    A private/deleted YouTube video's title is withheld everywhere a non-owner can
    look -- the watch page, the playlist listing (flat entries carry no title), and
    oEmbed (403) -- so the id is shown marked as such rather than posing as a title.
    """
    return f"[Title unavailable] {ident}" if ident else _UNKNOWN_TITLE


def _entry_url(ie: str, vid: str) -> str:
    """
    Rebuild the canonical URL for an ``[extractor] <id>`` pair from an ERROR line.

    The plain "youtube" extractor's id is a video id. A youtube:tab /
    youtube:playlist id may be a playlist id - classified by yt-dlp's own
    playlist-id rule - or a channel id / handle / custom name, which cannot be
    told apart reliably. Anything unrecognised (including other sites) keeps
    the bare id rather than inventing a URL that points somewhere else.
    """
    if ie == "youtube":
        return _YOUTUBE_WATCH_URL.format(vid)
    if ie.startswith("youtube:") and YoutubePlaylistIE.suitable(vid):
        return _YOUTUBE_PLAYLIST_URL.format(vid)
    return vid


def load_failed_downloads(path: Path) -> list[FailedRecord]:
    """Load failed-download records; newest first. Returns [] on missing/corrupt file."""
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_exception(exc, f"load_failed_downloads: unreadable store at {path}")
        return []
    if not isinstance(parsed, list):
        return []
    return [r for r in parsed if isinstance(r, dict) and r.get("key")]


def save_failed_downloads(path: Path, records: list[FailedRecord]) -> None:
    """Write failed-download records atomically; never raises on write failure."""
    try:
        atomic_write_text(path, json.dumps(records, ensure_ascii=False, indent=1), mkdir=True)
    except OSError as exc:
        log_exception(exc, f"save_failed_downloads: could not write {path}")


def add_failed_download(path: Path, record: FailedRecord) -> list[FailedRecord]:
    """Add a record newest-first, replacing any existing record with the same key."""
    records = [r for r in load_failed_downloads(path) if r.get("key") != record.get("key")]
    records.insert(0, record)
    save_failed_downloads(path, records)
    return records


def remove_failed_downloads(path: Path, keys: Iterable[str]) -> list[FailedRecord]:
    """Remove every record whose key is in *keys* in one write; unknown/falsy keys ignored."""
    drop = {key for key in keys if isinstance(key, str) and key}
    records = load_failed_downloads(path)
    # A hand-edited store can hold a non-string key (e.g. a list); testing it for
    # set membership raises TypeError, and an exception escaping a Qt slot aborts
    # the interpreter - so only string keys are ever looked up.
    kept = [r for r in records if not (isinstance(r.get("key"), str) and r["key"] in drop)]
    if len(kept) != len(records):
        save_failed_downloads(path, kept)
    return kept


def record_video_id(record: FailedRecord | None) -> str | None:
    """
    Return the YouTube video id of a record's first URL, or None.

    Gate on the URL resolving to a video id, not on record["site"]: a
    playlist-sourced failure's site is detected from the *playlist file's*
    raw entries (see detect_site_from_urls), which for a bare-playlist-id
    file (e.g. 720playlists.txt) comes back "unknown" even though the
    failed entry's own urls[0] is a real youtube.com watch URL. The video
    id -- what the archive actually keys on -- only needs that URL.
    """
    if not record:
        return None
    urls = record.get("urls")
    if not isinstance(urls, list) or not urls or not isinstance(urls[0], str):
        return None
    return extract_video_id(urls[0])


def keys_resolved_by_download(records: Iterable[FailedRecord], url: str | None) -> list[str]:
    """
    Return keys of failed records that a successful download of *url* resolves.

    Only single-URL records are eligible: a multi-URL batch record failed as a
    whole, and one entry succeeding says nothing about the rest. A record
    matches on exact URL, or on YouTube video id so youtu.be / watch / extra
    query-param spellings of one video resolve each other.
    """
    if not isinstance(url, str) or not url.strip():
        return []
    url = url.strip()
    vid = extract_video_id(url)
    keys: list[str] = []
    for record in records:
        key = record.get("key")
        urls = record.get("urls")
        if not (isinstance(key, str) and key and isinstance(urls, list) and len(urls) == 1):
            continue
        if urls[0] == url or (vid is not None and record_video_id(record) == vid):
            keys.append(key)
    return keys


def make_failed_record(
    urls: list,
    meta: dict | None,
    title: str,
    error: str,
) -> FailedRecord:
    """Normalize a failure into a display-ready record for the store."""
    meta = meta or {}
    return {
        "key": urls[0] if urls else title,
        "urls": list(urls),
        "source": meta.get("type") or meta.get("source") or "unknown",
        "site": meta.get("site") or "unknown",
        "title": title,
        "failed_at": get_local_timestamp(),
        "error": error[:_MAX_ERROR_LEN],
    }


def progress_item_key(info: dict) -> str:
    """Identify the item a yt-dlp progress event's ``info_dict`` belongs to."""
    return str(
        info.get("id")
        or info.get("_filename")
        or info.get("url")
        or info.get("playlist_id")
        or "unknown",
    )


class FailureHook:
    """
    Progress hook buffering per-entry download errors.

    With ignoreerrors="only_download" (playlist sources), a failing entry never
    raises out of DownloadExecutor.execute - the only signal is a progress event
    with status == "error". A later "finished"/postprocessing event for the same
    video id means a fallback (720p / no-SponsorBlock) succeeded, so the buffered
    failure is discarded. flush() reports what remains.
    """

    def __init__(
        self,
        meta: dict | None,
        on_failure: Callable[[FailedRecord], None],
    ) -> None:
        """Initialize the hook with download metadata and a failure callback."""
        self.meta = meta or {}
        self.on_failure = on_failure
        self._buffered: dict[str, FailedRecord] = {}

    def _vid_id(self, info: dict) -> str:
        return progress_item_key(info)

    def __call__(self, d: dict) -> None:
        """Buffer an error event, or discard a buffered failure once the item finishes."""
        try:
            status = d.get("status")
            info = d.get("info_dict") or {}
            vid = self._vid_id(info)

            if status == "error":
                self._buffered[vid] = make_failed_record(
                    urls=[info.get("webpage_url") or info.get("url") or vid],
                    meta=self.meta,
                    title=info.get("title") or _untitled(info.get("id")),
                    error=str(
                        d.get("error") or d.get("fragment_error") or "download error",
                    ),
                )
            elif status == "finished":
                self._buffered.pop(vid, None)
            elif status == "postprocessing":
                postproc = (d.get("postprocessor") or "").lower()
                if "merger" in postproc or "ffmpegextractaudio" in postproc:
                    self._buffered.pop(vid, None)
        except (AttributeError, TypeError, OSError) as exc:
            # Never let failure capture break the download, but capture it
            log_exception(exc, "FailureHook failed while recording a download error")

    def record_log_error(self, message: str) -> None:
        """
        Buffer a per-entry extraction failure parsed out of a yt-dlp ERROR line.

        Shares ``_buffered`` with the progress-hook path, keyed by video id, so a
        video that fails both ways is reported once, and one that later succeeds
        via a fallback still has its buffered failure discarded on "finished".

        Lines without an ``[extractor] <id>:`` prefix are ignored: those are
        run-level errors (e.g. a bare "unable to download video data"), which
        either abort the run and are reported by the caller, or already arrive as
        a progress event.
        """
        match = _ENTRY_ERROR_RE.match(message.strip())
        if match is None:
            return
        vid = match.group("vid")
        if vid in self._buffered:
            return
        # No title is re-fetched here: this line means extraction of the same URL
        # just failed, and re-extracting it fails the same way.
        self._buffered[vid] = make_failed_record(
            urls=[_entry_url(match.group("ie"), vid)],
            meta=self.meta,
            title=_untitled(vid),
            error=match.group("reason").strip(),
        )

    def flush(self) -> None:
        """Report every still-buffered failure, then clear the buffer."""
        for record in self._buffered.values():
            try:
                self.on_failure(record)
            except (AttributeError, TypeError, OSError, RuntimeError) as exc:
                log_exception(exc, "FailureHook: on_failure callback failed")
        self._buffered.clear()


class ErrorCapturingLogger:
    """
    yt-dlp logger proxy that tees per-entry ERROR lines into a FailureHook.

    Install this only when ``ignoreerrors`` is set. Without it, a failing entry
    raises out of the download and the caller files the failure itself; teeing as
    well would file the same video twice under two different keys (the caller
    uses the URL the user supplied, this path the canonical watch URL).

    Every other logger method is delegated untouched, so the wrapped object keeps
    behaving as whatever it is - notably ``src.qyt.QLogger``, whose ``message_changed``
    signal the download thread reconnects after each run.
    """

    def __init__(
        self,
        inner: Any,  # noqa: ANN401 - duck-typed logger proxy target
        on_error_line: Callable[[str], None],
    ) -> None:
        """Wrap *inner*, forwarding each error line to *on_error_line* as well."""
        self._inner = inner
        self._on_error_line = on_error_line

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - delegates to arbitrary logger attrs
        """Delegate debug/warning/and any other logger method to the wrapped logger."""
        return getattr(self._inner, name)

    def error(self, msg: str) -> None:
        """Record the line as a possible per-entry failure, then log it as usual."""
        try:
            self._on_error_line(str(msg))
        except (AttributeError, TypeError, ValueError) as exc:
            # Capturing a failure must never be the reason a download stops.
            log_exception(exc, "ErrorCapturingLogger: could not record an error line")
        self._inner.error(msg)

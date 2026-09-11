"""Persistent store for failed downloads and the progress hook that captures them."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp.extractor.youtube import YoutubePlaylistIE

from .logging_utils import get_local_timestamp, log_exception

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
    tmp_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as exc:
        log_exception(exc, f"save_failed_downloads: could not write {path}")
        tmp_path.unlink(missing_ok=True)


def add_failed_download(path: Path, record: FailedRecord) -> list[FailedRecord]:
    """Add a record newest-first, replacing any existing record with the same key."""
    records = [r for r in load_failed_downloads(path) if r.get("key") != record.get("key")]
    records.insert(0, record)
    save_failed_downloads(path, records)
    return records


def remove_failed_download(path: Path, key: str) -> list[FailedRecord]:
    """Remove the record with the given key; a missing key is a no-op."""
    records = [r for r in load_failed_downloads(path) if r.get("key") != key]
    save_failed_downloads(path, records)
    return records


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
        # Duplicated from QYT.HistoryHook._vid_id rather than imported: importing
        # QYT here would create a src -> root -> src import cycle.
        return str(
            info.get("id")
            or info.get("_filename")
            or info.get("url")
            or info.get("playlist_id")
            or "unknown",
        )

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
    behaving as whatever it is - notably ``QYT.QLogger``, whose ``message_changed``
    signal the download thread reconnects after each run.
    """

    def __init__(
        self,
        inner: Any,
        on_error_line: Callable[[str], None],
    ) -> None:
        """Wrap *inner*, forwarding each error line to *on_error_line* as well."""
        self._inner = inner
        self._on_error_line = on_error_line

    def __getattr__(self, name: str) -> Any:
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

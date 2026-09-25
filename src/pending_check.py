"""The single poll loop that turns parked downloads into queued downloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yt_dlp

from .config import YDL_EXTRACTION_ERRORS
from .logging_utils import get_local_timestamp, log_exception
from .pending_queue import (
    KIND_LIVE,
    KIND_PREMIERE,
    PendingRecord,
    load_pending_queue,
    save_pending_queue,
)
from .playlist_entry import playlist_entry_target, restrict_to_entry
from .release_status import release_at_from_timestamp
from .ydl_options import build_podcast_outtmpl
from .ydl_utils import extract_release_info

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MAX_ERROR_LEN = 500


@dataclass(frozen=True)
class PendingCheckDeps:
    """
    Everything ``check_pending_queue`` needs from its host.

    MyWindow and DownloadService each build one of these; the loop itself stays free of
    Qt and of either class, which is what lets it be tested with a plain fake and what
    keeps the two hosts from drifting apart again.
    """

    path: Path
    cookiefile: str
    get_options: Callable[[list[str], str], dict | None]
    append_properties: Callable[[dict, dict], dict | None]
    create_context: Callable[[], tuple[Any, Any, dict]]
    wire_signals: Callable[[Any, Any], None]
    enqueue: Callable[[list[str], dict], None]
    log: Callable[[str], None]
    set_progress_range: Callable[[int, int], None]
    detect_site: Callable[[list[str]], str]
    load_playlist_comments: Callable[[str], Any]
    ydl_class: type = field(default=yt_dlp.YoutubeDL)


def is_available(info: dict) -> bool:
    """Return True when a probed item can actually be downloaded now."""
    return not (
        info.get("is_live")
        or info.get("live_status") in ("is_live", "is_upcoming")
        or info.get("availability") == "scheduled"
    )


def _refresh(record: PendingRecord, info: dict) -> PendingRecord:
    """Fold a fresh probe result onto a parked record without losing known values."""
    refreshed = {
        **record,
        "last_checked": get_local_timestamp(),
        "last_error": None,
    }
    if title := info.get("title"):
        refreshed["title"] = title
    if release_at := release_at_from_timestamp(info.get("release_timestamp")):
        refreshed["release_at"] = release_at
    if info.get("live_status") == "is_upcoming" and not info.get("is_live"):
        refreshed["kind"] = KIND_PREMIERE
    elif info.get("is_live") or info.get("live_status") == "is_live":
        refreshed["kind"] = KIND_LIVE
    return refreshed


def enqueue_entry(
    deps: PendingCheckDeps,
    url: str,
    source: str,
    *,
    playlist_id: str | None = None,
    label: str | None = None,
    recheck_live: bool = False,
) -> bool:
    """
    Build options for one item and put it on the download queue.

    A video-playlist entry with a known playlist is downloaded through its playlist URL,
    restricted to its own id, so yt-dlp fills %(playlist)s / %(playlist_index)s (BACKLOG #23).

    Args:
        recheck_live: Keep the live/upcoming match_filter (Retry, Download now). The pending
            loop leaves it False because its probe just confirmed availability.

    Returns:
        False when get_options declined (archive-only mode, empty URL, cancelled).
    """
    # Options come from the watch URL even when the playlist is what gets walked:
    # archive-only mode archives every entry of the URL get_options is handed.
    properties = deps.get_options([url], source)
    if not properties:
        return False
    qhook, qlogger, ydl_opts = deps.create_context()
    live_filter = properties.pop("match_filter", None)
    ydl_opts = deps.append_properties(ydl_opts, properties) or ydl_opts
    if source == "audio_playlists":
        # get_options rebuilds the flat misc-directory template; the show folder this
        # episode was bound for survives only as the parked label.
        ydl_opts["outtmpl"] = build_podcast_outtmpl(label)
    target = playlist_entry_target(url, source, playlist_id)
    kept_filter = live_filter if recheck_live else None
    if target is not None:
        ydl_opts["match_filter"] = restrict_to_entry(target.video_id, kept_filter)
    elif kept_filter is not None:
        ydl_opts["match_filter"] = kept_filter
    qmeta: dict = {"site": deps.detect_site([url]), "type": source}
    # The NA-folder rescue is only for a bare watch URL; a retargeted run makes no NA/ folder,
    # and the rescue would rename any stale one it found.
    if (
        target is None
        and playlist_id
        and (playlist_comments := deps.load_playlist_comments(source))
    ):
        qmeta["playlist_comments"] = playlist_comments
        qmeta["playlist_id"] = playlist_id
    ydl_opts["qmeta"] = qmeta
    deps.enqueue([target.playlist_url if target else url], ydl_opts)
    deps.wire_signals(qhook, qlogger)
    deps.set_progress_range(0, 1)
    return True


def _enqueue_record(deps: PendingCheckDeps, record: PendingRecord) -> bool:
    """Queue a now-available parked record (availability was just confirmed by the probe)."""
    return enqueue_entry(
        deps,
        record["url"],
        record["source"],
        playlist_id=record.get("playlist_id"),
        label=record.get("label"),
    )


def check_pending_queue(deps: PendingCheckDeps) -> list[PendingRecord]:
    """
    Probe every parked download; enqueue the ones that became available.

    Returns the records still parked afterwards (also written back to the store), so a
    caller can refresh its UI without re-reading the file.
    """
    records = load_pending_queue(deps.path)
    if not records:
        return []
    remaining: list[PendingRecord] = []
    for record in records:
        url = record.get("url", "")
        try:
            info = extract_release_info(url, cookiefile=deps.cookiefile, ydl_class=deps.ydl_class)
        # KeyboardInterrupt is a BaseException and deliberately still propagates.
        except YDL_EXTRACTION_ERRORS as exc:
            deps.log(f"Error checking pending url {url}: {exc}")
            log_exception(exc, f"Error checking pending url {url}")
            remaining.append(_mark_error(record, exc))
            continue
        except Exception as exc:
            deps.log(f"Unexpected error checking pending url {url}: {exc}")
            log_exception(exc, f"Unexpected error checking pending url {url}")
            remaining.append(_mark_error(record, exc))
            continue
        if not info:
            remaining.append({**record, "last_checked": get_local_timestamp()})
            continue
        refreshed = _refresh(record, info)
        if not is_available(info):
            remaining.append(refreshed)
            continue
        try:
            queued = _enqueue_record(deps, refreshed)
        except Exception as exc:
            log_exception(exc, f"Failed to queue now-available pending url {url}")
            remaining.append(_mark_error(refreshed, exc))
            continue
        if queued:
            deps.log(f"Now available, queued: {url} [{refreshed['source']}]")
        else:
            remaining.append(refreshed)
    save_pending_queue(deps.path, remaining)
    return remaining


def _mark_error(record: PendingRecord, exc: Exception) -> PendingRecord:
    """Stamp a failed check onto a record without dropping it from the queue."""
    return {
        **record,
        "last_checked": get_local_timestamp(),
        "last_error": str(exc)[:_MAX_ERROR_LEN],
    }

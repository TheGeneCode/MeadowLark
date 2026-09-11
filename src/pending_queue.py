"""Persistent store for downloads deferred until they become available."""

import json
from collections.abc import Iterable
from pathlib import Path

from .logging_utils import get_local_timestamp, log_exception

PendingRecord = dict
# keys: url, source, playlist_id, label, kind, title, release_at,
#       first_seen, last_checked, last_error

KIND_LIVE = "live"
KIND_PREMIERE = "premiere"

_MAX_ERROR_LEN = 500
# Fields a refreshed record may leave unset without wiping a better value already on disk.
_PRESERVE_IF_MISSING = ("playlist_id", "label", "release_at", "last_checked", "last_error")


def make_pending_record(
    url: str,
    source: str,
    *,
    playlist_id: str | None = None,
    label: str | None = None,
    kind: str = KIND_LIVE,
    title: str | None = None,
    release_at: str | None = None,
) -> PendingRecord:
    """Build a fully-populated pending record."""
    return {
        "url": url,
        "source": source,
        "playlist_id": playlist_id,
        "label": label,
        "kind": kind,
        "title": title or url,
        "release_at": release_at,
        "first_seen": get_local_timestamp(),
        "last_checked": None,
        "last_error": None,
    }


def load_pending_queue(path: Path) -> list[PendingRecord]:
    """Load pending records in insertion order. Returns [] on missing/corrupt file."""
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_exception(exc, f"load_pending_queue: unreadable store at {path}")
        return []
    if not isinstance(parsed, list):
        return []
    return [r for r in parsed if isinstance(r, dict) and r.get("url") and r.get("source")]


def save_pending_queue(path: Path, records: list[PendingRecord]) -> None:
    """Write pending records atomically; never raises on write failure."""
    tmp_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as exc:
        log_exception(exc, f"save_pending_queue: could not write {path}")
        tmp_path.unlink(missing_ok=True)


def merge_pending(existing: PendingRecord, incoming: PendingRecord) -> PendingRecord:
    """
    Fold an incoming record onto an existing one, keeping the older item's history.

    ``first_seen`` always wins from the existing record so the UI can show how long an
    item has been parked, and a field the incoming record left as None never wipes a
    value already known on disk -- a re-park from the failure path carries no label or
    playlist_id, and must not erase what match_filter recorded earlier.
    """
    merged = {**existing, **incoming}
    merged["first_seen"] = existing.get("first_seen") or incoming.get("first_seen")
    for field in _PRESERVE_IF_MISSING:
        if incoming.get(field) is None and existing.get(field) is not None:
            merged[field] = existing[field]
    if incoming.get("title") in (None, "", incoming.get("url")) and existing.get("title"):
        merged["title"] = existing["title"]
    if merged.get("last_error"):
        merged["last_error"] = str(merged["last_error"])[:_MAX_ERROR_LEN]
    return merged


def upsert_pending(path: Path, record: PendingRecord) -> list[PendingRecord]:
    """Insert or merge a record by URL, preserving list order; returns the new list."""
    records = load_pending_queue(path)
    for index, existing in enumerate(records):
        if existing.get("url") == record.get("url"):
            records[index] = merge_pending(existing, record)
            break
    else:
        records.append(record)
    save_pending_queue(path, records)
    return records


def remove_pending(path: Path, url: str) -> list[PendingRecord]:
    """Remove the record with the given URL; a missing URL is a no-op."""
    records = [r for r in load_pending_queue(path) if r.get("url") != url]
    save_pending_queue(path, records)
    return records


def remove_pending_many(path: Path, urls: Iterable[str]) -> list[PendingRecord]:
    """Remove every record whose URL is in *urls* in one write; unknown URLs are ignored."""
    drop = set(urls)
    records = load_pending_queue(path)
    kept = [r for r in records if r.get("url") not in drop]
    if len(kept) != len(records):
        save_pending_queue(path, kept)
    return kept


def _load_legacy_entries(legacy_path: Path) -> dict[str, tuple[str, str | None, str | None]]:
    """
    Parse the pre-JSON ``source|url[|playlist_id[|label]]`` format into {url: (...)}.

    Inlined from the retired ``src/live_queue.py`` so this store carries no dependency
    on the format it replaces. maxsplit=3 is load-bearing: a podcast show label may
    itself contain '|' and is always the last field -- which is also why the format
    could never grow a 5th column for the release time.
    """
    entries: dict[str, tuple[str, str | None, str | None]] = {}
    with legacy_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) >= 2 and parts[1]:
                playlist_id = parts[2] if len(parts) >= 3 and parts[2] else None
                label = parts[3] if len(parts) == 4 and parts[3] else None
                entries[parts[1]] = (parts[0], playlist_id, label)
    return entries


def migrate_legacy_live_queue(legacy_path: Path, path: Path) -> bool:
    """
    Drain a pre-JSON ``live_queue.txt`` into the pending store exactly once.

    Every legacy entry is a parked live stream (the only thing the old format could
    hold), so each becomes kind="live" with no release time; the first poll fills that
    in. The txt is renamed rather than deleted so a bad migration stays recoverable.
    Returns True when a migration actually ran.
    """
    if not legacy_path.exists():
        return False
    try:
        entries = _load_legacy_entries(legacy_path)
    except OSError as exc:
        log_exception(exc, f"migrate_legacy_live_queue: unreadable {legacy_path}")
        return False
    if entries:
        records = load_pending_queue(path)
        known = {r.get("url") for r in records}
        records.extend(
            make_pending_record(url, source, playlist_id=playlist_id, label=label)
            for url, (source, playlist_id, label) in entries.items()
            if url not in known
        )
        save_pending_queue(path, records)
    try:
        legacy_path.replace(legacy_path.with_suffix(".txt.migrated"))
    except OSError as exc:
        log_exception(exc, f"migrate_legacy_live_queue: could not rename {legacy_path}")
    return bool(entries)

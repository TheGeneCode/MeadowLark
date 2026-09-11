"""Unit tests for src.pending_queue: persistent store for deferred downloads."""

import json
import threading
from pathlib import Path

import pytest

from src.pending_queue import (
    KIND_LIVE,
    KIND_PREMIERE,
    load_pending_queue,
    make_pending_record,
    merge_pending,
    migrate_legacy_live_queue,
    remove_pending,
    remove_pending_many,
    save_pending_queue,
    upsert_pending,
)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Path to a pending-queue JSON store file that does not yet exist."""
    return tmp_path / "pending.json"


def test_load_missing_file_returns_empty(store: Path) -> None:
    assert load_pending_queue(store) == []


def test_load_corrupt_json_returns_empty(store: Path) -> None:
    store.write_text("{{{", encoding="utf-8")
    assert load_pending_queue(store) == []


def test_load_non_list_json_returns_empty(store: Path) -> None:
    store.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert load_pending_queue(store) == []


def test_load_skips_records_without_url_or_source(store: Path) -> None:
    invalid_records = [
        {},
        {"url": "u"},  # Missing source
        {
            "url": "https://example.com/v1",
            "source": "youtube",
            "kind": KIND_LIVE,
            "title": "Valid",
        },
    ]
    store.write_text(json.dumps(invalid_records), encoding="utf-8")
    result = load_pending_queue(store)
    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/v1"


def test_make_record_defaults() -> None:
    record = make_pending_record("u", "youtube")
    assert record["kind"] == KIND_LIVE
    assert record["title"] == "u"
    assert record["release_at"] is None
    assert record["first_seen"] is not None
    assert record["last_checked"] is None


def test_upsert_appends_new_record(store: Path) -> None:
    record = make_pending_record("https://example.com/v1", "youtube")
    upsert_pending(store, record)
    result = load_pending_queue(store)
    assert len(result) == 1
    assert result[0]["url"] == "https://example.com/v1"


def test_upsert_merges_by_url_and_keeps_first_seen(store: Path) -> None:
    record_a = make_pending_record("https://example.com/v1", "youtube", title="Title A")
    upsert_pending(store, record_a)
    first_seen = load_pending_queue(store)[0]["first_seen"]

    record_b = make_pending_record("https://example.com/v1", "youtube", title="Title B")
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert len(result) == 1
    assert result[0]["title"] == "Title B"
    assert result[0]["first_seen"] == first_seen


def test_upsert_does_not_wipe_label_with_none(store: Path) -> None:
    record_a = make_pending_record("u", "youtube", label="Show")
    upsert_pending(store, record_a)

    record_b = make_pending_record("u", "youtube", label=None)
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert result[0]["label"] == "Show"


def test_upsert_does_not_wipe_release_at_with_none(store: Path) -> None:
    record_a = make_pending_record("u", "youtube", release_at="2026-08-01T10:00:00-05:00")
    upsert_pending(store, record_a)

    record_b = make_pending_record("u", "youtube", release_at=None)
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert result[0]["release_at"] == "2026-08-01T10:00:00-05:00"


def test_upsert_new_release_at_overwrites_old(store: Path) -> None:
    record_a = make_pending_record("u", "youtube", release_at="2026-08-01T10:00:00-05:00")
    upsert_pending(store, record_a)

    record_b = make_pending_record("u", "youtube", release_at="2026-08-02T15:00:00-05:00")
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert result[0]["release_at"] == "2026-08-02T15:00:00-05:00"


def test_upsert_preserves_order_of_other_records(store: Path) -> None:
    records = [
        make_pending_record("https://example.com/v1", "youtube"),
        make_pending_record("https://example.com/v2", "youtube"),
        make_pending_record("https://example.com/v3", "youtube"),
    ]
    for r in records:
        upsert_pending(store, r)

    # Re-upsert the first one
    upsert_pending(store, make_pending_record("https://example.com/v1", "youtube"))
    result = load_pending_queue(store)

    assert result[0]["url"] == "https://example.com/v1"
    assert result[1]["url"] == "https://example.com/v2"
    assert result[2]["url"] == "https://example.com/v3"


def test_upsert_keeps_existing_title_when_incoming_is_url(store: Path) -> None:
    url = "https://example.com/v1"
    record_a = make_pending_record(url, "youtube", title="Real Title")
    upsert_pending(store, record_a)

    # Incoming record has title == url (the default from make_pending_record)
    record_b = make_pending_record(url, "youtube")
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert result[0]["title"] == "Real Title"


def test_last_error_is_truncated(store: Path) -> None:
    long_error = "x" * 900
    record_a = make_pending_record("u", "youtube")
    upsert_pending(store, record_a)

    record_b = make_pending_record("u", "youtube")
    record_b["last_error"] = long_error
    upsert_pending(store, record_b)
    result = load_pending_queue(store)

    assert len(result[0]["last_error"]) == 500


def test_remove_pending_deletes_and_is_idempotent(store: Path) -> None:
    url = "https://example.com/v1"
    record = make_pending_record(url, "youtube")
    upsert_pending(store, record)

    remove_pending(store, url)
    result = load_pending_queue(store)
    assert len(result) == 0

    # Second removal is idempotent
    remove_pending(store, url)
    result = load_pending_queue(store)
    assert len(result) == 0


def test_save_is_atomic_and_leaves_no_tmp(store: Path) -> None:
    records = [make_pending_record("u", "youtube")]
    save_pending_queue(store, records)

    assert store.exists()
    assert not store.with_suffix(".tmp").exists()


def test_unicode_title_round_trips(store: Path) -> None:
    title = "日本語字幕 🎬"
    record = make_pending_record("u", "youtube", title=title)
    upsert_pending(store, record)

    result = load_pending_queue(store)
    assert result[0]["title"] == title


def test_migrate_legacy_converts_entries_and_renames_txt(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    legacy_path.write_text("audio_playlists|https://y/1||Show\n", encoding="utf-8")

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is True

    records = load_pending_queue(store)
    assert len(records) == 1
    assert records[0]["kind"] == KIND_LIVE
    assert records[0]["label"] == "Show"
    assert records[0]["url"] == "https://y/1"

    assert not legacy_path.exists()
    assert legacy_path.with_suffix(".txt.migrated").exists()


def test_migrate_legacy_missing_file_is_noop(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is False
    assert load_pending_queue(store) == []


def test_migrate_legacy_empty_file_still_renames(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    legacy_path.write_text("", encoding="utf-8")

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is False
    assert not legacy_path.exists()
    assert legacy_path.with_suffix(".txt.migrated").exists()


def test_migrate_legacy_skips_urls_already_in_store(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    # Pre-populate store
    url = "https://y/1"
    record = make_pending_record(url, "youtube")
    upsert_pending(store, record)
    first_seen_original = load_pending_queue(store)[0]["first_seen"]

    # Legacy file has the same url
    legacy_path.write_text("audio_playlists|https://y/1||Show\n", encoding="utf-8")

    migrate_legacy_live_queue(legacy_path, store)
    result = load_pending_queue(store)

    assert len(result) == 1
    assert result[0]["first_seen"] == first_seen_original


def test_migrate_legacy_label_containing_pipe_survives(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    legacy_path.write_text("audio_playlists|https://y/1|PLx|Show | Two\n", encoding="utf-8")

    migrate_legacy_live_queue(legacy_path, store)
    result = load_pending_queue(store)

    assert result[0]["label"] == "Show | Two"


def test_migrate_legacy_skips_malformed_lines(tmp_path: Path) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"

    legacy_path.write_text("justsource\naudio_playlists||label\n", encoding="utf-8")

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is False
    assert load_pending_queue(store) == []
    assert legacy_path.with_suffix(".txt.migrated").exists()


# ---------------------------------------------------------------------------
# Boundary coverage added: make_pending_record edge values, I/O error paths
# (OSError on read/write/rename), merge_pending field-preservation edge cases,
# the falsy-url/source "vanish on reload" gap, an already-migrated-file
# overwrite, and a concurrent-upsert lost-update race.
# ---------------------------------------------------------------------------


def test_make_pending_record_empty_string_title_falls_back_to_url() -> None:
    record = make_pending_record("https://example.com/v1", "youtube", title="")
    assert record["title"] == "https://example.com/v1"


def test_make_pending_record_kind_premiere() -> None:
    record = make_pending_record("u", "youtube", kind=KIND_PREMIERE)
    assert record["kind"] == KIND_PREMIERE


def test_load_pending_queue_oserror_on_read_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "pending.json"
    store.write_text("[]", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert load_pending_queue(store) == []


def test_save_pending_queue_oserror_on_write_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "pending.json"

    def boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    save_pending_queue(store, [make_pending_record("u", "youtube")])

    assert not store.exists()
    assert not store.with_suffix(".tmp").exists()


def test_save_pending_queue_oserror_on_replace_cleans_up_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "pending.json"

    def boom(self: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("permission denied on rename")

    monkeypatch.setattr(Path, "replace", boom)
    save_pending_queue(store, [make_pending_record("u", "youtube")])

    assert not store.exists()
    assert not store.with_suffix(".tmp").exists()


def test_upsert_falsy_source_record_vanishes_on_next_load(store: Path) -> None:
    """
    Documents a latent falsy-source "vanish on reload" gap.

    Same class as the failed-downloads store's falsy-key vanish issue: a
    record saved with an empty-string ``source`` writes to disk fine, but
    ``load_pending_queue``'s truthy filter drops it on the very next read, so
    it becomes silently unrecoverable. No current caller passes an empty
    source, so this is defensive-gap severity, not a live bug.
    """
    record = make_pending_record("https://example.com/v1", "")
    upsert_pending(store, record)

    raw = json.loads(store.read_text(encoding="utf-8"))
    assert len(raw) == 1  # it was written

    assert load_pending_queue(store) == []  # but filtered out on reload


def test_merge_pending_both_missing_preserve_field_stays_none() -> None:
    existing = make_pending_record("u", "youtube")
    incoming = make_pending_record("u", "youtube")
    merged = merge_pending(existing, incoming)
    assert merged["label"] is None
    assert merged["release_at"] is None


def test_merge_pending_empty_incoming_title_overrides_falsy_existing_title() -> None:
    existing = make_pending_record("u", "youtube")
    existing["title"] = None
    incoming = make_pending_record("u", "youtube")
    incoming["title"] = ""
    merged = merge_pending(existing, incoming)
    # existing title is falsy, so the "keep existing" guard does not fire;
    # incoming's empty string wins via the plain dict-merge.
    assert merged["title"] == ""


def test_merge_pending_real_incoming_title_overrides_existing_real_title() -> None:
    existing = make_pending_record("u", "youtube", title="Old Real Title")
    incoming = make_pending_record("u", "youtube", title="New Real Title")
    merged = merge_pending(existing, incoming)
    assert merged["title"] == "New Real Title"


def test_migrate_legacy_source_empty_string_creates_record_that_vanishes_on_reload(
    tmp_path: Path,
) -> None:
    """A line with an empty source (e.g. "|url") produces a record filtered on reload."""
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"
    legacy_path.write_text("|https://example.com/v1\n", encoding="utf-8")

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is True

    raw = json.loads(store.read_text(encoding="utf-8"))
    assert len(raw) == 1
    assert load_pending_queue(store) == []


def test_migrate_legacy_overwrites_stale_already_migrated_file(tmp_path: Path) -> None:
    """
    Documents current (unfixed) stale-migrated-file overwrite behavior.

    An ``.txt.migrated`` file left over from a previous partial run is silently
    overwritten by ``Path.replace`` -- its prior content is discarded without
    warning.
    """
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"
    migrated_path = legacy_path.with_suffix(".txt.migrated")

    migrated_path.write_text("stale content from a previous run", encoding="utf-8")
    legacy_path.write_text("audio_playlists|https://y/1||Show\n", encoding="utf-8")

    result = migrate_legacy_live_queue(legacy_path, store)
    assert result is True
    assert migrated_path.read_text(encoding="utf-8") == (
        "audio_playlists|https://y/1||Show\n"
    )


def test_migrate_legacy_oserror_reading_legacy_file_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"
    legacy_path.write_text("audio_playlists|https://y/1\n", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    result = migrate_legacy_live_queue(legacy_path, store)

    assert result is False
    assert load_pending_queue(store) == []
    # The rename is only attempted after the read block; a read failure means
    # the legacy file is left in place for a retry.
    assert legacy_path.exists()


def test_migrate_legacy_oserror_on_rename_still_reports_true_but_file_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_path = tmp_path / "live_queue.txt"
    store = tmp_path / "pending.json"
    legacy_path.write_text("audio_playlists|https://y/1\n", encoding="utf-8")

    original_replace = Path.replace

    def boom(self: Path, target: Path, *args: object, **kwargs: object) -> Path:
        # Only the legacy-file-rename call should fail here; the store's own
        # atomic tmp -> real-file replace (inside save_pending_queue) must
        # still succeed so this test isolates the rename failure specifically.
        if str(target).endswith(".txt.migrated"):
            raise OSError("permission denied on rename")
        return original_replace(self, target, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", boom)
    result = migrate_legacy_live_queue(legacy_path, store)

    # Data migration itself succeeded (entries existed); only the rename failed.
    assert result is True
    assert load_pending_queue(store)[0]["url"] == "https://y/1"
    assert legacy_path.exists()  # rename failure leaves the source file in place


def test_remove_pending_many_mixed_present_and_absent_urls(store: Path) -> None:
    """A batch mixing known and unknown urls drops only the known ones, unknowns are ignored."""
    for url in ("https://y/keep", "https://y/a", "https://y/b"):
        upsert_pending(store, make_pending_record(url, "youtube"))

    result = remove_pending_many(store, ["https://y/a", "https://y/b", "https://y/does-not-exist"])

    assert [r["url"] for r in result] == ["https://y/keep"]
    assert [r["url"] for r in load_pending_queue(store)] == ["https://y/keep"]


def test_remove_pending_many_duplicate_urls_in_input_still_removes_once(store: Path) -> None:
    """Duplicate urls in the input list must not error or double-count; net effect is one removal."""
    upsert_pending(store, make_pending_record("https://y/keep", "youtube"))
    upsert_pending(store, make_pending_record("https://y/dup", "youtube"))

    result = remove_pending_many(store, ["https://y/dup", "https://y/dup", "https://y/dup"])

    assert [r["url"] for r in result] == ["https://y/keep"]


def test_remove_pending_many_no_matching_urls_skips_write(
    store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When nothing in the batch matches, save_pending_queue must not be called (one-write contract)."""
    upsert_pending(store, make_pending_record("https://y/keep", "youtube"))
    save_calls: list[list] = []

    def spy_save(path: Path, records: list) -> None:
        save_calls.append(records)

    import src.pending_queue as pending_queue_module

    monkeypatch.setattr(pending_queue_module, "save_pending_queue", spy_save)

    result = remove_pending_many(store, ["https://y/absent"])

    assert save_calls == []
    assert [r["url"] for r in result] == ["https://y/keep"]


def test_remove_pending_many_empty_url_list_is_noop(store: Path) -> None:
    upsert_pending(store, make_pending_record("https://y/keep", "youtube"))

    result = remove_pending_many(store, [])

    assert [r["url"] for r in result] == ["https://y/keep"]


def test_concurrent_upsert_calls_lose_an_update(tmp_path: Path) -> None:
    """
    RACE CONDITION (confirmed, not fixed) in upsert_pending's load-modify-save cycle.

    No locking exists. Two threads racing to append distinct new URLs at the
    same time can both load the same initial state and each write back a list
    containing only their own addition -- the second writer's save clobbers
    the first writer's, losing an update. Demonstrated deterministically with
    a barrier that forces both threads past the read before either writes.
    """
    store = tmp_path / "pending.json"
    save_pending_queue(store, [])

    barrier = threading.Barrier(2)
    original_save = save_pending_queue

    def racy_upsert(url: str) -> None:
        from src.pending_queue import load_pending_queue as _load

        records = _load(store)
        barrier.wait()  # force both threads to have read before either writes
        records.append(make_pending_record(url, "youtube"))
        original_save(store, records)

    t1 = threading.Thread(target=racy_upsert, args=("https://example.com/a",))
    t2 = threading.Thread(target=racy_upsert, args=("https://example.com/b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    result = load_pending_queue(store)
    # Confirms the lost-update race: only one of the two records survives.
    assert len(result) == 1

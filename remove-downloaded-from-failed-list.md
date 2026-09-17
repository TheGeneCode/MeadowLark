# Plan: Drop a Video from Failed Downloads Once It Downloads

## Context
A failed video stays in the Failed Downloads list (and keeps the ⚠ N button lit) even after it later downloads fine, e.g. from a playlist scan, a new drop, or a podcast run. Only **Retry** (removes first) and **Mark as Downloaded** clean up now. After this change, any successful download removes matching failed records right away, so the list only shows items that are still failed.

## Critical Reference Files / Infrastructure to Reuse
| Capability | File or import path |
|---|---|
| Store load / batch remove (one write, skips write if nothing removed, never raises) | `src/failed_downloads.py` — `load_failed_downloads`, `remove_failed_downloads` |
| Video-id identity of a record (same rule as the archive and Mark as Downloaded) | `src/failed_downloads.py` — `record_video_id(record)` |
| YouTube id from watch / youtu.be URL | `src/url_utils.py` — `extract_video_id(url)` (already imported in `failed_downloads.py`) |
| Success signal, per video | `QYT.py` — `HistoryHook` → `HistoryLogger.log(..., success=True, url=webpage_url)` → `on_log` dict `{"dt","site","dtype","title","result","url"}` with `result == "SUCCESS"` → `QYTQueue.history_entry_added` |
| GUI-thread slot already wired to that signal | `meadowlark.pyw:1693` `MyWindow._on_history_entry_added` (connected at `meadowlark.pyw:542`) |
| Remove + refresh button + refresh open dialog | `meadowlark.pyw:1625` `MyWindow._delete_failed_downloads(keys)` |
| Analogous feature | `meadowlark.pyw:1652` `_mark_failed_downloaded`: resolve records → `_delete_failed_downloads` |
| Test patterns | `tests/test_failed_downloads.py` (`store` fixture, `tmp_path / "fd.json"`); `tests/test_mark_failed_downloaded.py` (`_make_window` binds methods onto a `MagicMock`, `_record(**overrides)`) |

## Approach
Hook the existing `history_entry_added` signal on the GUI thread. No new signal and no change to the worker thread. The failed-downloads store is only written from GUI-thread slots, so it stays single-writer. Qt queued signals from one sender thread reach the GUI in order: if a video logs SUCCESS and the run then still reports failure (e.g. merge fails after `finished`), `download_failed` arrives *after* the removal and re-files the record. So the final state is always right.

Matching is a pure function in `src/failed_downloads.py`, so it is easy to test. A record matches when it has **exactly one** URL, and that URL either equals the downloaded URL or resolves to the same YouTube video id (`record_video_id`), so a failed `youtu.be/X` is cleared by a successful `watch?v=X`. Multi-URL batch records are skipped on purpose: `execute()` failed for the batch, and one entry's success does not show the others succeeded. Playlist-URL records never match (no video id, URL differs from the entry's watch URL).

Rejected alternatives:
- Clearing inside `HistoryHook` on the worker thread: two threads would write the store.
- Clearing on `SKIPPED` / archive-hit history entries: that is not a download, and **Mark as Downloaded** already covers it.

## Implementation Steps
**Model profile:** 0 opus · 3 sonnet · 0 haiku

### Step 1 — Pure matcher in the store module
**Model:** sonnet — small pure function with a clear spec.

`C:\Users\etreq\dev\MeadowLark\src\failed_downloads.py`: add after `record_video_id` (after line 128):

```python
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
```

No new imports (`Iterable`, `extract_video_id` already imported).

### Step 2 — Clear resolved failures from the history slot
**Model:** sonnet — wiring onto an existing slot, following the `_mark_failed_downloaded` pattern.

`C:\Users\etreq\dev\MeadowLark\meadowlark.pyw`:
1. Import block at line 114: add `keys_resolved_by_download` to the `from src.failed_downloads import (...)` list.
2. Replace `_on_history_entry_added` (lines 1693–1696) and add a helper right after it:

```python
    def _on_history_entry_added(self, record: dict) -> None:
        dialog = getattr(self, "_history_dialog", None)
        if dialog and dialog.isVisible():
            dialog.prepend_row(record)
        if record.get("result") == "SUCCESS":
            self._clear_resolved_failures(record.get("url"))

    def _clear_resolved_failures(self, url: str | None) -> None:
        """Drop failed-download records that a successful download just resolved."""
        try:
            keys = keys_resolved_by_download(load_failed_downloads(FAILED_DOWNLOADS_FILE), url)
            if keys:
                self._delete_failed_downloads(keys)
        except OSError as exc:
            # An exception escaping a Qt slot aborts the interpreter.
            utils.log_exception(exc, "Failed to clear resolved failed downloads")
```

The `if keys` guard means no button or dialog refresh when nothing matched, which is the common case on every success.

### Step 3 — README note
**Model:** sonnet — one-sentence doc edit (too small for haiku).

`C:\Users\etreq\dev\MeadowLark\README.md` line 253: add to the end of the paragraph: ` An item also leaves the list by itself as soon as that video downloads successfully (from a retry, a playlist scan, or dropping it again).`

## Tests
Shared fixtures: `store` in `tests/test_failed_downloads.py`; `_make_window` / `_record` in `tests/test_mark_failed_downloaded.py`.

**`tests/test_failed_downloads.py`** (add `keys_resolved_by_download` to the import):
| Test | Setup | Expected |
|---|---|---|
| `test_resolved_exact_url_match` | record `key="k", urls=["https://nebula.tv/videos/x"]` | `keys_resolved_by_download([rec], "https://nebula.tv/videos/x") == ["k"]` |
| `test_resolved_by_video_id_across_spellings` | record urls `["https://youtu.be/abc123"]` | url `https://www.youtube.com/watch?v=abc123` → `["k"]` |
| `test_resolved_ignores_multi_url_records` | urls `[watch?v=abc123, watch?v=def456]` | `[]` |
| `test_resolved_ignores_playlist_record` | urls `["https://www.youtube.com/playlist?list=PLx"]`, url `watch?v=abc123` | `[]` |
| `test_resolved_none_or_blank_url` | one matching-ish record | `None` → `[]`; `"  "` → `[]` |
| `test_resolved_skips_malformed_records` | records with `key=None`, `urls="str"`, `key=["x"]` | `[]`, no exception |
| `test_resolved_different_video_no_match` | urls `[watch?v=abc123]`, url `watch?v=zzz999` | `[]` |

**`tests/test_mark_failed_downloaded.py`**:
| Test | Setup | Expected |
|---|---|---|
| `test_history_success_clears_matching_failure` | `_make_window("_on_history_entry_added", "_clear_resolved_failures")`; store with `_record()` patched as `meadowlark.FAILED_DOWNLOADS_FILE`; call with `{"result": "SUCCESS", "url": "https://youtu.be/abc123"}` | `win._delete_failed_downloads.assert_called_once_with([record["key"]])` |
| `test_history_fail_entry_does_not_clear` | same store; `{"result": "FAIL", "url": <same>}` | `_delete_failed_downloads` not called |
| `test_history_success_no_match_no_refresh` | store with `_record()`; url `watch?v=other` | `_delete_failed_downloads` not called |
| `test_history_success_empty_store` | store path that does not exist | not called, no exception |

## Verification
- Tests: `uv run pytest tests/test_failed_downloads.py -q` and `uv run pytest tests/test_mark_failed_downloaded.py -q`, then the full suite `uv run pytest -q`
- Lint: `uv run ruff check` (zero findings)
- Manual smoke (`uv run python meadowlark.pyw`): put a record for a downloadable video in `resources/failed_downloads.json` (or cause a real failure), launch, open ⚠ N, drop the same video's URL (`youtu.be` form is fine). When it finishes, the row leaves the open dialog and the count goes down (the button hides at 0).

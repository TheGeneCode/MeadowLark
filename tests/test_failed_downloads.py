"""Tests for the failed-downloads store, record factory, and FailureHook."""

import json
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from QYT import QYTQueue
from src.failed_downloads import (
    ErrorCapturingLogger,
    FailureHook,
    add_failed_download,
    load_failed_downloads,
    make_failed_record,
    record_video_id,
    remove_failed_downloads,
    save_failed_downloads,
)

_app = QApplication.instance() or QApplication([])

_RECORD_KEYS = {"key", "urls", "source", "site", "title", "failed_at", "error"}
_META = {"site": "youtube", "type": "1080"}


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "fd.json"


def test_load_missing_file_returns_empty(store: Path) -> None:
    assert load_failed_downloads(store) == []


def test_load_corrupt_json_returns_empty(store: Path) -> None:
    store.write_text("{not json", encoding="utf-8")
    assert load_failed_downloads(store) == []


def test_add_and_load_roundtrip(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T", "boom"))

    records = load_failed_downloads(store)
    assert len(records) == 1
    assert set(records[0]) == _RECORD_KEYS
    assert records[0]["error"] == "boom"
    assert records[0]["source"] == "1080"


def test_add_dedupes_by_key_newest_first(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T1", "first"))
    add_failed_download(store, make_failed_record(["u2"], _META, "T2", "second"))
    add_failed_download(store, make_failed_record(["u1"], _META, "T1", "retry"))

    records = load_failed_downloads(store)
    assert [r["key"] for r in records] == ["u1", "u2"]
    assert records[0]["error"] == "retry"


def test_remove_missing_key_noop(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T", "boom"))

    remove_failed_downloads(store, ["nope"])

    assert [r["key"] for r in load_failed_downloads(store)] == ["u1"]


def test_remove_existing_key(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T", "boom"))

    remove_failed_downloads(store, ["u1"])

    assert load_failed_downloads(store) == []


def test_remove_many_drops_all_given_keys(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T1", "e"))
    add_failed_download(store, make_failed_record(["u2"], _META, "T2", "e"))
    add_failed_download(store, make_failed_record(["u3"], _META, "T3", "e"))

    remove_failed_downloads(store, ["u1", "u3"])

    assert [r["key"] for r in load_failed_downloads(store)] == ["u2"]


def test_remove_many_no_match_skips_write(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T", "e"))
    mock_save = MagicMock()
    monkeypatch.setattr("src.failed_downloads.save_failed_downloads", mock_save)

    result = remove_failed_downloads(store, ["nope"])

    assert len(result) == 1
    mock_save.assert_not_called()


def test_remove_many_tolerates_non_string_stored_key(store: Path) -> None:
    store.write_text(
        json.dumps(
            [
                {"key": ["x"], "urls": ["u"], "source": "1080"},
                {"key": "u1", "urls": ["u"], "source": "1080"},
            ],
        ),
        encoding="utf-8",
    )

    result = remove_failed_downloads(store, ["u1"])

    assert any(r.get("key") == ["x"] for r in result)
    assert not any(r.get("key") == "u1" for r in result)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (make_failed_record(["https://www.youtube.com/watch?v=abc123"], _META, "T", "e"), "abc123"),
        (make_failed_record(["https://youtu.be/abc123"], _META, "T", "e"), "abc123"),
        (make_failed_record(["https://www.youtube.com/playlist?list=PLx"], _META, "T", "e"), None),
        ({"urls": "not-a-list"}, None),
        ({"urls": []}, None),
        ({"urls": [None]}, None),
        ({"urls": [123]}, None),
        ({}, None),
        (None, None),
    ],
)
def test_record_video_id(record: dict | None, expected: str | None) -> None:
    assert record_video_id(record) == expected


def test_make_failed_record_empty_urls() -> None:
    record = make_failed_record([], None, "t", "e")

    assert record["key"] == "t"
    assert record["source"] == "unknown"
    assert record["site"] == "unknown"


def test_error_truncated() -> None:
    record = make_failed_record(["u1"], _META, "T", "x" * 1000)

    assert len(record["error"]) == 500


def test_failure_hook_buffers_and_flushes() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u", "title": "T"}})
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["key"] == "u"
    assert captured[0]["title"] == "T"


def test_failure_hook_discards_after_finish() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u", "title": "T"}})
    hook({"status": "finished", "info_dict": {"id": "v1"}})
    hook.flush()

    assert captured == []


def test_failure_hook_discards_after_merger_postprocessing() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u", "title": "T"}})
    hook(
        {
            "status": "postprocessing",
            "postprocessor": "Merger",
            "info_dict": {"id": "v1"},
        },
    )
    hook.flush()

    assert captured == []


def test_failure_hook_flush_clears_buffer() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u", "title": "T"}})
    hook.flush()
    hook.flush()

    assert len(captured) == 1


def test_failure_hook_never_raises() -> None:
    hook = FailureHook(None, on_failure=lambda _record: None)

    hook({})
    hook({"status": "error", "info_dict": None})
    hook.flush()


def test_qytqueue_download_emits_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    queue_obj = QYTQueue(Queue())
    monkeypatch.setattr(queue_obj.executor, "execute", lambda _u, _o: (False, "err msg"))
    monkeypatch.setattr(queue_obj.executor, "_extract_title", lambda _u: "T")
    captured: list[dict] = []
    queue_obj.download_failed.connect(captured.append)

    queue_obj.download(["u"], {"qmeta": _META})

    assert len(captured) == 1
    assert captured[0]["source"] == "1080"
    assert captured[0]["error"] == "err msg"


def test_qytqueue_download_no_emit_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    queue_obj = QYTQueue(Queue())
    monkeypatch.setattr(queue_obj.executor, "execute", lambda _u, _o: (True, ""))
    monkeypatch.setattr(queue_obj.executor, "_extract_title", lambda _u: "T")
    captured: list[dict] = []
    queue_obj.download_failed.connect(captured.append)

    queue_obj.download(["u"], {"qmeta": _META})

    assert captured == []


# --- Store robustness: non-list / malformed JSON payloads ---


@pytest.mark.parametrize("payload", ['{"a": 1}', "42", '"hello"', "null", "true"])
def test_load_non_list_json_returns_empty(store: Path, payload: str) -> None:
    store.write_text(payload, encoding="utf-8")
    assert load_failed_downloads(store) == []


def test_load_filters_non_dict_and_keyless_entries(store: Path) -> None:
    store.write_text(
        json.dumps(
            [
                {"key": "u1", "title": "ok"},
                "not-a-dict",
                123,
                None,
                {"title": "no key field"},
                {"key": ""},
                {"key": None},
                {"key": 0},
            ],
        ),
        encoding="utf-8",
    )

    records = load_failed_downloads(store)

    assert [r["key"] for r in records] == ["u1"]


def test_load_oserror_returns_empty(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    store.write_text("[]", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)

    assert load_failed_downloads(store) == []


def test_save_oserror_swallowed_and_tmp_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    store: Path,
) -> None:
    def boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)

    save_failed_downloads(store, [{"key": "u1"}])  # must not raise

    assert not store.exists()
    assert not store.with_suffix(".tmp").exists()


def test_save_creates_missing_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "fd.json"

    save_failed_downloads(nested, [{"key": "u1"}])

    assert nested.exists()
    assert load_failed_downloads(nested) == [{"key": "u1"}]


def test_unicode_roundtrip(store: Path) -> None:
    record = make_failed_record(["u1"], _META, "日本語タイトル 🎬", "エラー: 失敗しました")
    add_failed_download(store, record)

    loaded = load_failed_downloads(store)

    assert loaded[0]["title"] == "日本語タイトル 🎬"
    assert loaded[0]["error"] == "エラー: 失敗しました"
    assert "\\u" not in store.read_text(encoding="utf-8")


def test_add_falsy_key_record_vanishes_on_reload(store: Path) -> None:
    """
    A record with empty urls AND an empty title gets key == "".

    add_failed_download's own return value still contains it, but the next
    load_failed_downloads call silently drops it (filters falsy keys), so the
    record is unrecoverable after the first reload. No current call site
    passes an empty title, but nothing guards against it either.
    """
    record = make_failed_record([], None, "", "some error")
    assert record["key"] == ""

    returned = add_failed_download(store, record)
    assert returned[0]["key"] == ""

    reloaded = load_failed_downloads(store)
    assert reloaded == []


def test_remove_with_falsy_key_is_noop(store: Path) -> None:
    add_failed_download(store, make_failed_record(["u1"], _META, "T", "e"))

    remove_failed_downloads(store, ["", None])  # type: ignore[list-item]

    assert [r["key"] for r in load_failed_downloads(store)] == ["u1"]


# --- make_failed_record boundary values ---


def test_error_exactly_500_not_truncated() -> None:
    record = make_failed_record(["u1"], _META, "T", "x" * 500)
    assert record["error"] == "x" * 500


def test_error_501_truncated_to_500() -> None:
    record = make_failed_record(["u1"], _META, "T", "x" * 501)
    assert len(record["error"]) == 500
    assert record["error"] == "x" * 500


def test_make_failed_record_empty_type_falls_back_to_source() -> None:
    record = make_failed_record(["u1"], {"type": "", "source": "manual"}, "T", "e")
    assert record["source"] == "manual"


def test_make_failed_record_non_string_title_used_as_key() -> None:
    record = make_failed_record([], None, 12345, "e")  # type: ignore[arg-type]
    assert record["key"] == 12345


def test_make_failed_record_non_string_error_raises_typeerror() -> None:
    with pytest.raises(TypeError):
        make_failed_record(["u1"], _META, "T", None)  # type: ignore[arg-type]


# --- FailureHook buffer/discard ordering ---


def test_failure_hook_error_finish_error_rebuffers() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u1", "title": "T1"}})
    hook({"status": "finished", "info_dict": {"id": "v1"}})
    hook(
        {
            "status": "error",
            "info_dict": {"id": "v1", "webpage_url": "u1", "title": "T1-retry"},
        },
    )
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["title"] == "T1-retry"


def test_failure_hook_two_ids_one_ok_one_failed() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u1", "title": "Fails"}})
    hook({"status": "error", "info_dict": {"id": "v2", "webpage_url": "u2", "title": "Ok"}})
    hook({"status": "finished", "info_dict": {"id": "v2"}})
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["title"] == "Fails"


def test_failure_hook_unrelated_postprocessor_does_not_discard() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u1", "title": "T"}})
    hook(
        {
            "status": "postprocessing",
            "postprocessor": "EmbedThumbnail",
            "info_dict": {"id": "v1"},
        },
    )
    hook.flush()

    assert len(captured) == 1


def test_failure_hook_empty_error_string_falls_back_to_default() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook(
        {
            "status": "error",
            "error": "",
            "fragment_error": "",
            "info_dict": {"id": "v1", "webpage_url": "u1", "title": "T"},
        },
    )
    hook.flush()

    assert captured[0]["error"] == "download error"


def test_failure_hook_non_dict_info_dict_does_not_raise() -> None:
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": ["not", "a", "dict"]})
    hook.flush()

    assert captured == []


def test_failure_hook_missing_vid_id_collision_loses_earlier_failure() -> None:
    """
    Two events lacking an id collide on the same fallback vid key.

    Both lack id/_filename/url/playlist_id and hash to "unknown", so the
    second error silently overwrites the first in the buffer.
    """
    captured: list[dict] = []
    hook = FailureHook(_META, on_failure=captured.append)

    hook({"status": "error", "info_dict": {"title": "First"}})
    hook({"status": "error", "info_dict": {"title": "Second"}})
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["title"] == "Second"


# --- QYTQueue.download: hook/flush interaction with overall success/failure ---


def test_qytqueue_download_flushes_hook_failure_on_overall_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_obj = QYTQueue(Queue())

    def fake_execute(_urls: list, opts: dict) -> tuple[bool, str]:
        for hook in opts.get("progress_hooks", []):
            if isinstance(hook, FailureHook):
                hook(
                    {
                        "status": "error",
                        "info_dict": {"id": "v1", "webpage_url": "u1", "title": "Entry"},
                    },
                )
        return True, ""

    monkeypatch.setattr(queue_obj.executor, "execute", fake_execute)
    monkeypatch.setattr(queue_obj.executor, "_extract_title", lambda _u: "T")
    captured: list[dict] = []
    queue_obj.download_failed.connect(captured.append)

    queue_obj.download(["u"], {"qmeta": _META})

    assert len(captured) == 1
    assert captured[0]["title"] == "Entry"


def test_qytqueue_download_hook_and_batch_both_emit_on_same_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Document the intentional overlap between hook and batch failure capture.

    A single-video failure can be captured by both the hook and the
    batch-level record; Phase 2's dedupe-by-key is what collapses them, not
    this layer.
    """
    queue_obj = QYTQueue(Queue())

    def fake_execute(_urls: list, opts: dict) -> tuple[bool, str]:
        for hook in opts.get("progress_hooks", []):
            if isinstance(hook, FailureHook):
                hook(
                    {
                        "status": "error",
                        "info_dict": {"id": "v1", "webpage_url": "u", "title": "T"},
                    },
                )
        return False, "err msg"

    monkeypatch.setattr(queue_obj.executor, "execute", fake_execute)
    monkeypatch.setattr(queue_obj.executor, "_extract_title", lambda _u: "T")
    captured: list[dict] = []
    queue_obj.download_failed.connect(captured.append)

    queue_obj.download(["u"], {"qmeta": _META})

    assert len(captured) == 2


# --- QYTQueue.run crash path: degenerate item shapes ---


def test_run_crash_path_empty_urls_and_none_item_uses_unknown_placeholders() -> None:
    class _StopLoop(Exception):
        """Breaks out of the otherwise infinite worker loop."""

    queue = MagicMock()
    item = ([], None)
    queue.get.side_effect = [item, _StopLoop()]
    queue.empty.return_value = True

    ydl_queue = QYTQueue(queue)
    captured: list[dict] = []
    ydl_queue.download_failed.connect(captured.append)

    with (
        patch("QYT.keep"),
        patch.object(ydl_queue, "download", side_effect=RuntimeError("boom")),
        pytest.raises(_StopLoop),
    ):
        ydl_queue.run()

    assert len(captured) == 1
    assert captured[0]["title"] == "(unknown)"
    assert captured[0]["key"] == "(unknown)"
    assert captured[0]["urls"] == []
    assert captured[0]["source"] == "unknown"
    assert "RuntimeError: boom" in captured[0]["error"]


def test_run_crash_path_non_dict_item_meta_falls_back_to_empty() -> None:
    class _StopLoop(Exception):
        """Breaks out of the otherwise infinite worker loop."""

    queue = MagicMock()
    item = (["https://example.com/v"], ["not", "a", "dict"])
    queue.get.side_effect = [item, _StopLoop()]
    queue.empty.return_value = True

    ydl_queue = QYTQueue(queue)
    captured: list[dict] = []
    ydl_queue.download_failed.connect(captured.append)

    with (
        patch("QYT.keep"),
        patch.object(ydl_queue, "download", side_effect=RuntimeError("boom")),
        pytest.raises(_StopLoop),
    ):
        ydl_queue.run()

    assert len(captured) == 1
    assert captured[0]["title"] == "https://example.com/v"
    assert captured[0]["key"] == "https://example.com/v"
    assert captured[0]["source"] == "unknown"


# --- Extraction-stage failures (playlist entries that never start downloading) ---
#
# Under ignoreerrors="only_download" a dead playlist entry is skipped and the run
# continues, so nothing raises and no progress hook fires. The yt-dlp ERROR line
# is the only evidence, and it must still reach Failed Downloads.

_UNAVAILABLE_LINE = (
    "ERROR: [youtube] JsxNJgm7VXA: Video unavailable. "
    "This video is no longer available because the uploader has closed their account."
)


def _hook_with_capture() -> tuple[FailureHook, list[dict]]:
    captured: list[dict] = []
    return FailureHook(_META, captured.append), captured


def test_log_error_line_becomes_a_failed_record() -> None:
    hook, captured = _hook_with_capture()
    hook.record_log_error(_UNAVAILABLE_LINE)
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["urls"] == ["https://www.youtube.com/watch?v=JsxNJgm7VXA"]
    assert captured[0]["error"].startswith("Video unavailable.")
    assert captured[0]["source"] == "1080"


def test_extraction_failure_labels_the_title_unavailable_instead_of_showing_the_id() -> None:
    """
    A bare id must never pose as a title.

    YouTube withholds a private/deleted video's title from the watch page, the
    playlist listing, and oEmbed alike, so there is no real title to show.
    """
    hook, captured = _hook_with_capture()
    hook.record_log_error(_UNAVAILABLE_LINE)
    hook.flush()

    assert captured[0]["title"] == "[Title unavailable] JsxNJgm7VXA"


def test_progress_error_without_a_title_labels_it_instead_of_showing_the_id() -> None:
    hook, captured = _hook_with_capture()
    hook({"status": "error", "info_dict": {"id": "v1", "webpage_url": "u"}})
    hook.flush()

    assert captured[0]["title"] == "[Title unavailable] v1"


def test_progress_error_with_neither_title_nor_id_keeps_the_unknown_placeholder() -> None:
    hook, captured = _hook_with_capture()
    hook({"status": "error", "info_dict": {"webpage_url": "u"}})
    hook.flush()

    assert captured[0]["title"] == "(unknown title)"


def test_log_error_line_does_not_overwrite_a_progress_hook_failure() -> None:
    """The richer progress-hook record wins; the same video is reported once."""
    hook, captured = _hook_with_capture()
    hook(
        {
            "status": "error",
            "info_dict": {
                "id": "JsxNJgm7VXA",
                "title": "Real Title",
                "webpage_url": "https://youtu.be/JsxNJgm7VXA",
            },
        },
    )
    hook.record_log_error(_UNAVAILABLE_LINE)
    hook.flush()

    assert len(captured) == 1
    assert captured[0]["title"] == "Real Title"


def test_log_error_line_is_discarded_when_the_video_later_finishes() -> None:
    """A fallback that succeeds must not leave the video filed as failed."""
    hook, captured = _hook_with_capture()
    hook.record_log_error(_UNAVAILABLE_LINE)
    hook({"status": "finished", "info_dict": {"id": "JsxNJgm7VXA"}})
    hook.flush()

    assert captured == []


@pytest.mark.parametrize(
    "line",
    [
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        "ERROR: Requested format is not available",
        "[download] Destination: C:/vid/some video.mp4",
        "",
    ],
)
def test_non_entry_error_lines_are_ignored(line: str) -> None:
    """Run-level errors abort the run and are filed by the caller, not here."""
    hook, captured = _hook_with_capture()
    hook.record_log_error(line)
    hook.flush()

    assert captured == []


def test_non_youtube_extractor_keeps_the_bare_id_rather_than_inventing_a_url() -> None:
    hook, captured = _hook_with_capture()
    hook.record_log_error("ERROR: [nebula] some-slug: Video unavailable")
    hook.flush()

    assert captured[0]["urls"] == ["some-slug"]


@pytest.mark.parametrize("ie", ["youtube:tab", "youtube:playlist"])
def test_playlist_level_error_records_the_playlist_url_not_the_bare_id(ie: str) -> None:
    """A bare playlist id handed to webbrowser misses the default browser on Windows."""
    hook, captured = _hook_with_capture()
    hook.record_log_error(
        f"ERROR: [{ie}] PLRWvNQVqAeWKt7kCUfEMdJi40m7H58CJd: "
        "YouTube said: The playlist does not exist.",
    )
    hook.flush()

    assert captured[0]["urls"] == [
        "https://www.youtube.com/playlist?list=PLRWvNQVqAeWKt7kCUfEMdJi40m7H58CJd",
    ]
    assert captured[0]["title"] == "[Title unavailable] PLRWvNQVqAeWKt7kCUfEMdJi40m7H58CJd"


def test_tab_error_for_a_non_playlist_id_keeps_the_bare_id() -> None:
    """A channel/handle tab id is not a playlist id, so no playlist URL is invented."""
    hook, captured = _hook_with_capture()
    hook.record_log_error("ERROR: [youtube:tab] UCuAXFkgsw1L7xaCfnd5JJOw: This channel does not exist")
    hook.flush()

    assert captured[0]["urls"] == ["UCuAXFkgsw1L7xaCfnd5JJOw"]


def test_video_id_with_underscore_is_captured_and_builds_a_watch_url() -> None:
    """Real YouTube ids may contain underscores as well as hyphens."""
    hook, captured = _hook_with_capture()
    hook.record_log_error("ERROR: [youtube] a_B-9_XyzQw: Video unavailable")
    hook.flush()

    assert captured[0]["urls"] == ["https://www.youtube.com/watch?v=a_B-9_XyzQw"]


def test_log_error_line_is_discarded_after_merger_postprocessing() -> None:
    """A fallback that succeeds must discard a log-captured failure too, not just a progress one."""
    hook, captured = _hook_with_capture()
    hook.record_log_error(_UNAVAILABLE_LINE)
    hook(
        {
            "status": "postprocessing",
            "postprocessor": "Merger",
            "info_dict": {"id": "JsxNJgm7VXA"},
        },
    )
    hook.flush()

    assert captured == []


def test_error_capturing_logger_tees_and_delegates() -> None:
    inner = MagicMock()
    recorded: list[str] = []
    logger = ErrorCapturingLogger(inner, recorded.append)

    logger.error(_UNAVAILABLE_LINE)
    logger.warning("just a warning")
    logger.debug("[download] 5%")

    assert recorded == [_UNAVAILABLE_LINE]
    inner.error.assert_called_once_with(_UNAVAILABLE_LINE)
    inner.warning.assert_called_once_with("just a warning")
    inner.debug.assert_called_once_with("[download] 5%")


def test_error_capturing_logger_still_logs_when_capture_raises() -> None:
    """Capturing a failure must never be the reason a download stops."""
    inner = MagicMock()
    logger = ErrorCapturingLogger(inner, MagicMock(side_effect=TypeError("boom")))

    logger.error("ERROR: something")

    inner.error.assert_called_once_with("ERROR: something")


def _run_download_capturing_logger(
    monkeypatch: pytest.MonkeyPatch,
    options: dict,
    error_line: str | None = None,
) -> tuple[list, list[dict], dict]:
    """Drive QYTQueue.download, recording the logger yt-dlp would have been handed."""
    queue_obj = QYTQueue(Queue())
    seen_loggers: list = []

    def _execute(_urls: list, opts: dict) -> tuple[bool, str]:
        logger = opts.get("logger")
        seen_loggers.append(logger)
        if error_line is not None and logger is not None:
            logger.error(error_line)
        return True, ""

    monkeypatch.setattr(queue_obj.executor, "execute", _execute)
    monkeypatch.setattr(queue_obj.executor, "_extract_title", lambda _u: "T")
    captured: list[dict] = []
    queue_obj.download_failed.connect(captured.append)

    queue_obj.download(["u"], options)
    return seen_loggers, captured, options


def test_playlist_run_files_an_unavailable_entry_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The reported bug, end to end at the wiring layer.

    A playlist run carries ignoreerrors="only_download", so an unavailable entry
    is skipped rather than raised. The run still reports success, and the entry
    still lands in Failed Downloads.
    """
    inner_logger = MagicMock()
    options = {
        "qmeta": _META,
        "logger": inner_logger,
        "ignoreerrors": "only_download",
    }
    seen, captured, options = _run_download_capturing_logger(
        monkeypatch, options, error_line=_UNAVAILABLE_LINE
    )

    assert isinstance(seen[0], ErrorCapturingLogger)
    assert len(captured) == 1
    assert captured[0]["urls"] == ["https://www.youtube.com/watch?v=JsxNJgm7VXA"]
    # The wrapper is transient: later code reads options["logger"] expecting the
    # real QLogger (to reconnect its message_changed signal).
    assert options["logger"] is inner_logger
    inner_logger.error.assert_called_once_with(_UNAVAILABLE_LINE)


def test_single_video_run_is_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ignoreerrors the error aborts and the caller files it; no double filing."""
    inner_logger = MagicMock()
    seen, _captured, _options = _run_download_capturing_logger(
        monkeypatch, {"qmeta": _META, "logger": inner_logger}
    )

    assert seen[0] is inner_logger


def test_logger_is_restored_even_when_execute_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner_logger = MagicMock()
    queue_obj = QYTQueue(Queue())
    monkeypatch.setattr(
        queue_obj.executor, "execute", MagicMock(side_effect=RuntimeError("boom"))
    )
    options = {"qmeta": _META, "logger": inner_logger, "ignoreerrors": "only_download"}

    with pytest.raises(RuntimeError):
        queue_obj.download(["u"], options)

    assert options["logger"] is inner_logger

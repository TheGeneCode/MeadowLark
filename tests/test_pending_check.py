"""Unit tests for the shared pending-queue poll loop (src/pending_check.py)."""

from collections.abc import Callable
from pathlib import Path
from typing import Self
from unittest.mock import Mock

import pytest
from yt_dlp.utils import DownloadError

from src.pending_check import PendingCheckDeps, check_pending_queue
from src.pending_queue import (
    KIND_LIVE,
    KIND_PREMIERE,
    load_pending_queue,
    make_pending_record,
    save_pending_queue,
)
from src.release_status import release_at_from_timestamp


def make_ydl_class(extract_info_fn: Callable[[str], dict]) -> type:
    """Build a fake YoutubeDL class whose extract_info delegates to extract_info_fn."""
    captured_opts: list[dict] = []

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            captured_opts.append(opts)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = False) -> dict:
            return extract_info_fn(url)

    FakeYDL.captured_opts = captured_opts  # type: ignore[attr-defined]
    return FakeYDL


def const(value: dict) -> Callable[[str], dict]:
    """extract_info_fn that always returns the same canned info dict."""
    return lambda _url: value


def raiser(exc: Exception) -> Callable[[str], dict]:
    """extract_info_fn that always raises the given exception."""

    def fn(_url: str) -> dict:
        raise exc

    return fn


def make_deps(tmp_path: Path, **overrides: object) -> PendingCheckDeps:
    defaults: dict = {
        "path": tmp_path / "pending_queue.json",
        "cookiefile": "",
        "get_options": lambda _urls, _source: {"format": "x"},
        "append_properties": lambda opts, props: (opts.update(props), opts)[1],
        "create_context": lambda: (Mock(), Mock(), {}),
        "wire_signals": Mock(),
        "enqueue": Mock(),
        "log": Mock(),
        "set_progress_range": Mock(),
        "detect_site": Mock(return_value="youtube"),
        "load_playlist_comments": Mock(return_value=None),
        "ydl_class": make_ydl_class(const({})),
    }
    defaults.update(overrides)
    return PendingCheckDeps(**defaults)


def seed(path: Path, *records: dict) -> None:
    save_pending_queue(path, list(records))


def test_empty_store_returns_empty_and_does_not_probe(tmp_path: Path) -> None:
    ydl_class = Mock()
    deps = make_deps(tmp_path, ydl_class=ydl_class)

    result = check_pending_queue(deps)

    assert result == []
    ydl_class.assert_not_called()


def test_upcoming_premiere_stays_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    record = make_pending_record("https://yt.com/watch?v=x", "1080playlists")
    seed(path, record)
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(
            const(
                {
                    "live_status": "is_upcoming",
                    "release_timestamp": 1785110400,
                    "title": "T",
                }
            )
        ),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    enqueue.assert_not_called()


def test_upcoming_premiere_refreshes_release_at_and_title(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    record = make_pending_record(
        "https://yt.com/watch?v=x", "1080playlists", title=None, release_at=None
    )
    record["title"] = "https://yt.com/watch?v=x"
    record["release_at"] = None
    seed(path, record)
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(
            const(
                {
                    "live_status": "is_upcoming",
                    "release_timestamp": 1785110400,
                    "title": "T",
                }
            )
        ),
    )

    result = check_pending_queue(deps)

    assert result[0]["release_at"] == release_at_from_timestamp(1785110400)
    assert result[0]["title"] == "T"
    assert result[0]["kind"] == KIND_PREMIERE


def test_live_stream_stays_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(const({"is_live": True})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["kind"] == KIND_LIVE
    enqueue.assert_not_called()


def test_availability_scheduled_stays_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(const({"availability": "scheduled"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    enqueue.assert_not_called()


def test_available_item_is_enqueued_and_dropped(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        get_options=lambda _urls, _source: {"format": "x"},
        ydl_class=make_ydl_class(const({"live_status": "was_live", "title": "T"})),
    )

    result = check_pending_queue(deps)

    assert result == []
    enqueue.assert_called_once()
    urls, _opts = enqueue.call_args[0]
    assert urls == ["https://yt.com/watch?v=x"]
    assert load_pending_queue(path) == []


def test_enqueue_drops_match_filter(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        get_options=lambda _urls, _source: {"match_filter": object()},
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    check_pending_queue(deps)

    _urls, opts = enqueue.call_args[0]
    assert "match_filter" not in opts


def test_enqueue_sets_qmeta_site_and_type(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        detect_site=Mock(return_value="youtube"),
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    check_pending_queue(deps)

    _urls, opts = enqueue.call_args[0]
    assert opts["qmeta"] == {"site": "youtube", "type": "1080playlists"}


def test_audio_playlists_uses_podcast_outtmpl_from_label(tmp_path: Path) -> None:
    from src.ydl_options import build_podcast_outtmpl

    path = tmp_path / "pending_queue.json"
    seed(
        path,
        make_pending_record(
            "https://yt.com/watch?v=x", "audio_playlists", label="My Show"
        ),
    )
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    check_pending_queue(deps)

    _urls, opts = enqueue.call_args[0]
    assert opts["outtmpl"] == build_podcast_outtmpl("My Show")


def test_non_podcast_source_keeps_get_options_outtmpl(tmp_path: Path) -> None:
    """A video source's own %(playlist)s template must survive re-queueing untouched."""
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        get_options=lambda _urls, _source: {
            "outtmpl": "/videos/%(playlist)s/%(title)s.%(ext)s"
        },
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    check_pending_queue(deps)

    _urls, opts = enqueue.call_args[0]
    assert opts["outtmpl"] == "/videos/%(playlist)s/%(title)s.%(ext)s"


def test_playlist_comments_added_only_when_playlist_id_present(tmp_path: Path) -> None:
    path_with = tmp_path / "with.json"
    seed(
        path_with,
        make_pending_record(
            "https://yt.com/watch?v=x", "1080playlists", playlist_id="PLxyz"
        ),
    )
    enqueue_with = Mock()
    deps_with = make_deps(
        tmp_path,
        path=path_with,
        enqueue=enqueue_with,
        load_playlist_comments=Mock(return_value={"PLxyz": "Show"}),
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )
    check_pending_queue(deps_with)
    _urls, opts_with = enqueue_with.call_args[0]
    assert opts_with["qmeta"]["playlist_comments"] == {"PLxyz": "Show"}
    assert opts_with["qmeta"]["playlist_id"] == "PLxyz"

    path_without = tmp_path / "without.json"
    seed(path_without, make_pending_record("https://yt.com/watch?v=y", "1080playlists"))
    enqueue_without = Mock()
    deps_without = make_deps(
        tmp_path,
        path=path_without,
        enqueue=enqueue_without,
        load_playlist_comments=Mock(return_value={"PLxyz": "Show"}),
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )
    check_pending_queue(deps_without)
    _urls, opts_without = enqueue_without.call_args[0]
    assert "playlist_comments" not in opts_without["qmeta"]
    assert "playlist_id" not in opts_without["qmeta"]


def test_get_options_returning_none_keeps_record_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        get_options=lambda _urls, _source: None,
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    enqueue.assert_not_called()


def test_extraction_error_keeps_record_and_stamps_last_error(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    log = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        log=log,
        ydl_class=make_ydl_class(raiser(DownloadError("boom"))),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["last_error"] == "boom"
    assert result[0]["last_checked"] is not None
    log.assert_called()


def test_generic_exception_keeps_record_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(raiser(RuntimeError("x"))),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["last_error"] == "x"


def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    before = path.read_text(encoding="utf-8")
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(raiser(KeyboardInterrupt())),
    )

    with pytest.raises(KeyboardInterrupt):
        check_pending_queue(deps)

    assert path.read_text(encoding="utf-8") == before


def test_enqueue_failure_keeps_record_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=Mock(side_effect=RuntimeError("enqueue failed")),
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["last_error"] == "enqueue failed"


def test_empty_info_dict_keeps_record_parked(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(const({})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["last_checked"] is not None
    assert result[0]["last_error"] is None


def test_one_failure_does_not_abort_the_other_records(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(
        path,
        make_pending_record("https://yt.com/watch?v=first", "1080playlists"),
        make_pending_record("https://yt.com/watch?v=second", "1080playlists"),
    )
    enqueue = Mock()

    def fn(url: str) -> dict:
        if "first" in url:
            raise DownloadError("boom")
        return {"live_status": "was_live"}

    deps = make_deps(tmp_path, path=path, enqueue=enqueue, ydl_class=make_ydl_class(fn))

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["url"] == "https://yt.com/watch?v=first"
    enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# Boundary coverage added: contradictory live-signal precedence, falsy-value
# traps (empty-dict get_options, epoch-zero timestamp, empty-string title),
# a missing podcast label reaching the full check_pending_queue path, and
# KeyboardInterrupt propagation from the enqueue side (only the extraction
# side was previously covered).
# ---------------------------------------------------------------------------


def test_contradictory_is_live_true_with_was_live_status_stays_parked_as_live(
    tmp_path: Path,
) -> None:
    """is_live=True wins over a stale/contradictory live_status="was_live"."""
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(const({"is_live": True, "live_status": "was_live"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    assert result[0]["kind"] == KIND_LIVE
    enqueue.assert_not_called()


def test_contradictory_is_live_true_overrides_upcoming_status_kind_classification(
    tmp_path: Path,
) -> None:
    """
    live_status="is_upcoming" combined with is_live=True.

    _refresh's premiere branch requires ``not info.get("is_live")``, so this
    combination falls through to the live branch instead -- documents the
    precedence rather than asserting it is "correct" (yt-dlp should never
    actually emit this combination, but the code must not crash on it).
    """
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(
            const({"is_live": True, "live_status": "is_upcoming"})
        ),
    )

    result = check_pending_queue(deps)

    assert result[0]["kind"] == KIND_LIVE


def test_get_options_returning_empty_dict_keeps_record_parked_silently(
    tmp_path: Path,
) -> None:
    """
    An empty dict from get_options is falsy, same as None (falsy-value trap).

    Unlike the enqueue-failure path, this leaves no ``last_error`` -- the item
    silently stays parked forever with no signal that anything went wrong.
    """
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        get_options=lambda _urls, _source: {},
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 1
    enqueue.assert_not_called()
    assert result[0]["last_error"] is None


def test_audio_playlists_no_label_falls_back_to_misc_outtmpl(tmp_path: Path) -> None:
    """A podcast record parked with no label (never set) still gets a home in misc."""
    from src.ydl_options import build_podcast_outtmpl

    path = tmp_path / "pending_queue.json"
    seed(
        path,
        make_pending_record("https://yt.com/watch?v=x", "audio_playlists"),
    )
    enqueue = Mock()
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=enqueue,
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    check_pending_queue(deps)

    _urls, opts = enqueue.call_args[0]
    assert opts["outtmpl"] == build_podcast_outtmpl(None)


def test_refresh_applies_epoch_zero_release_timestamp(tmp_path: Path) -> None:
    """release_timestamp=0 is a legitimate (if unusual) timestamp, not a missing value."""
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(
            const({"live_status": "is_upcoming", "release_timestamp": 0, "title": "T"})
        ),
    )

    result = check_pending_queue(deps)

    assert result[0]["release_at"] == release_at_from_timestamp(0)
    assert result[0]["release_at"] is not None


def test_refresh_empty_string_title_does_not_clobber_existing_title(
    tmp_path: Path,
) -> None:
    """An empty-string title from the probe is falsy and must not overwrite a real title."""
    path = tmp_path / "pending_queue.json"
    record = make_pending_record(
        "https://yt.com/watch?v=x", "1080playlists", title="Real Title"
    )
    seed(path, record)
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(const({"live_status": "is_upcoming", "title": ""})),
    )

    result = check_pending_queue(deps)

    assert result[0]["title"] == "Real Title"


def test_keyboard_interrupt_during_enqueue_propagates_and_store_untouched(
    tmp_path: Path,
) -> None:
    """
    KeyboardInterrupt raised from deps.enqueue (not just extraction) must also escape.

    ``_enqueue_record`` is invoked under a bare ``except Exception``, which does
    not catch BaseException subclasses -- confirms the second of the two
    escape points named in the handoff, not just the extraction-side one.
    """
    path = tmp_path / "pending_queue.json"
    seed(path, make_pending_record("https://yt.com/watch?v=x", "1080playlists"))
    before = path.read_text(encoding="utf-8")
    deps = make_deps(
        tmp_path,
        path=path,
        enqueue=Mock(side_effect=KeyboardInterrupt()),
        ydl_class=make_ydl_class(const({"live_status": "was_live"})),
    )

    with pytest.raises(KeyboardInterrupt):
        check_pending_queue(deps)

    # save_pending_queue only runs after the loop finishes; store must be untouched.
    assert path.read_text(encoding="utf-8") == before


def test_duplicate_url_entries_in_store_are_each_processed_independently(
    tmp_path: Path,
) -> None:
    """
    Two records sharing the same URL (e.g. from a corrupted/hand-edited store).

    check_pending_queue has no url-level dedup; both are probed and both can
    survive independently. Documents current behavior rather than asserting
    it is desirable.
    """
    path = tmp_path / "pending_queue.json"
    seed(
        path,
        make_pending_record("https://yt.com/watch?v=dup", "1080playlists"),
        make_pending_record("https://yt.com/watch?v=dup", "720playlists"),
    )
    deps = make_deps(
        tmp_path,
        path=path,
        ydl_class=make_ydl_class(const({"live_status": "is_upcoming"})),
    )

    result = check_pending_queue(deps)

    assert len(result) == 2
    assert {r["source"] for r in result} == {"1080playlists", "720playlists"}


def test_store_is_rewritten_with_survivors_only(tmp_path: Path) -> None:
    path = tmp_path / "pending_queue.json"
    seed(
        path,
        make_pending_record("https://yt.com/watch?v=parked", "1080playlists"),
        make_pending_record("https://yt.com/watch?v=available", "1080playlists"),
        make_pending_record("https://yt.com/watch?v=erroring", "1080playlists"),
    )

    def fn(url: str) -> dict:
        if "parked" in url:
            return {"live_status": "is_upcoming"}
        if "available" in url:
            return {"live_status": "was_live"}
        raise RuntimeError("boom")

    deps = make_deps(tmp_path, path=path, ydl_class=make_ydl_class(fn))

    check_pending_queue(deps)

    urls = {r["url"] for r in load_pending_queue(path)}
    assert urls == {
        "https://yt.com/watch?v=parked",
        "https://yt.com/watch?v=erroring",
    }

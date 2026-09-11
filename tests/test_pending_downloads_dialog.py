"""
Tests for PendingDownloadsDialog.

MyWindow itself stays untested here: importing meadowlark.pyw spins up timers
and keyring access as a side effect of module import, and no existing test in
this suite does that either.
"""

from datetime import UTC, datetime, timedelta

from PyQt6.QtWidgets import QApplication

from src.pending_downloads_dialog import PendingDownloadsDialog
from src.release_status import format_release_at, to_release_at

_app = QApplication.instance() or QApplication([])


def _record(
    url: str = "https://example.com/video",
    source: str = "1080",
    playlist_id: str | None = None,
    label: str | None = None,
    kind: str = "live",
    title: str = "Test Title",
    release_at: str | None = None,
    first_seen: str | None = None,
    last_checked: str | None = None,
    last_error: str | None = None,
) -> dict:
    return {
        "url": url,
        "source": source,
        "playlist_id": playlist_id,
        "label": label,
        "kind": kind,
        "title": title,
        "release_at": release_at,
        "first_seen": first_seen,
        "last_checked": last_checked,
        "last_error": last_error,
    }


def test_rows_match_records() -> None:
    record_a = _record(title="First Video")
    record_b = _record(title="Second Video")
    dialog = PendingDownloadsDialog([record_a, record_b])

    assert dialog._table.rowCount() == 2
    assert dialog._table.item(0, 3).text() == "First Video"
    assert dialog._table.item(1, 3).text() == "Second Video"


def test_available_at_column_uses_format_release_at() -> None:
    future_time = datetime.now().astimezone() + timedelta(hours=2)
    release_at_str = to_release_at(future_time)
    record = _record(release_at=release_at_str)
    dialog = PendingDownloadsDialog([record])

    item_text = dialog._table.item(0, 0).text()
    formatted = format_release_at(release_at_str)
    assert item_text == formatted
    assert ")" in item_text


def test_unknown_release_at_renders_time_unknown() -> None:
    record = _record(release_at=None)
    dialog = PendingDownloadsDialog([record])

    assert dialog._table.item(0, 0).text() == "(time unknown)"


def test_kind_and_type_columns() -> None:
    record = _record(kind="premiere", source="1080playlists")
    dialog = PendingDownloadsDialog([record])

    assert dialog._table.item(0, 1).text() == "premiere"
    assert dialog._table.item(0, 2).text() == "1080playlists"


def test_records_sorted_soonest_first() -> None:
    now = datetime.now().astimezone()
    near_time = to_release_at(now + timedelta(minutes=30))
    far_time = to_release_at(now + timedelta(hours=2))

    record_near = _record(title="Soon", release_at=near_time)
    record_far = _record(title="Later", release_at=far_time)
    record_unknown = _record(title="Unknown", release_at=None)

    dialog = PendingDownloadsDialog([record_far, record_unknown, record_near])

    assert dialog._table.item(0, 3).text() == "Soon"
    assert dialog._table.item(1, 3).text() == "Later"
    assert dialog._table.item(2, 3).text() == "Unknown"


def test_malformed_release_at_string_sorts_last_without_raising() -> None:
    """parse_release_at swallows bad ISO strings; _sorted must not propagate a crash."""
    now = datetime.now().astimezone()
    good_time = to_release_at(now + timedelta(hours=1))

    record_good = _record(title="Good", release_at=good_time)
    record_bad = _record(title="Malformed", release_at="not-a-real-timestamp")

    dialog = PendingDownloadsDialog([record_bad, record_good])

    assert dialog._table.item(0, 3).text() == "Good"
    assert dialog._table.item(1, 3).text() == "Malformed"


def test_records_with_duplicate_release_at_preserve_relative_order() -> None:
    """Equal sort keys -> Python's stable sort must preserve input order (no crash/swap)."""
    same_time = to_release_at(datetime.now().astimezone() + timedelta(hours=1))
    record_a = _record(title="A", release_at=same_time)
    record_b = _record(title="B", release_at=same_time)

    dialog = PendingDownloadsDialog([record_a, record_b])

    assert dialog._table.item(0, 3).text() == "A"
    assert dialog._table.item(1, 3).text() == "B"


def test_all_unknown_release_at_records_preserve_relative_order() -> None:
    record_a = _record(title="First", release_at=None)
    record_b = _record(title="Second", release_at=None)

    dialog = PendingDownloadsDialog([record_a, record_b])

    assert dialog._table.item(0, 3).text() == "First"
    assert dialog._table.item(1, 3).text() == "Second"


def test_tooltip_prefers_last_error_then_url() -> None:
    record_with_error = _record(
        url="https://example.com/video1", last_error="some error"
    )
    record_with_url = _record(
        url="https://example.com/video2", last_error=None
    )
    dialog = PendingDownloadsDialog([record_with_error, record_with_url])

    for col in range(dialog._table.columnCount()):
        assert dialog._table.item(0, col).toolTip() == "some error"
        assert dialog._table.item(1, col).toolTip() == "https://example.com/video2"


def test_buttons_disabled_with_no_selection() -> None:
    dialog = PendingDownloadsDialog([_record()])

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_buttons_enabled_on_selection() -> None:
    dialog = PendingDownloadsDialog([_record()])
    dialog._table.selectRow(0)

    assert dialog._download_btn.isEnabled() is True
    assert dialog._remove_btn.isEnabled() is True


def test_record_without_url_keeps_buttons_disabled() -> None:
    record = _record(url="")
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_record_with_none_url_keeps_buttons_disabled() -> None:
    record = _record(url=None)
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_record_missing_url_key_entirely_keeps_buttons_disabled() -> None:
    record = _record()
    del record["url"]
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_can_act_none_record_is_falsy() -> None:
    """No selection -> _selected_records() is empty -> _can_act(None) must not raise."""
    dialog = PendingDownloadsDialog([_record()])

    assert dialog._can_act(None) is False


def test_remove_selected_with_missing_url_key_does_not_emit() -> None:
    """_remove_selected reads record.get('url'); a missing key must not emit an empty signal."""
    record = _record()
    del record["url"]
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    captured: list[str] = []
    dialog.remove_requested.connect(captured.append)
    dialog._remove_selected()

    assert captured == []


def test_download_now_emits_full_record() -> None:
    record = _record()
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    captured: list[list[dict]] = []
    dialog.download_now_requested.connect(captured.append)

    dialog._download_btn.click()

    assert captured == [[record]]


def test_remove_emits_url() -> None:
    record = _record(url="https://example.com/video")
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    captured: list[list[str]] = []
    dialog.remove_requested.connect(captured.append)

    dialog._remove_btn.click()

    assert captured == [["https://example.com/video"]]


def test_multi_select_download_now_emits_every_actionable_record() -> None:
    """The core regression: selecting several rows must act on all of them, not just the last."""
    record_a = _record(url="https://example.com/a", title="A")
    record_b = _record(url="https://example.com/b", title="B")
    dialog = PendingDownloadsDialog([record_a, record_b])
    dialog._table.selectAll()

    assert dialog._download_btn.text() == "Download Now (2)"

    captured: list[list[dict]] = []
    dialog.download_now_requested.connect(captured.append)
    dialog._download_btn.click()

    assert captured == [[record_a, record_b]]


def test_multi_select_remove_emits_every_actionable_url() -> None:
    record_a = _record(url="https://example.com/a")
    record_b = _record(url="https://example.com/b")
    dialog = PendingDownloadsDialog([record_a, record_b])
    dialog._table.selectAll()

    assert dialog._remove_btn.text() == "Remove (2)"

    captured: list[list[str]] = []
    dialog.remove_requested.connect(captured.append)
    dialog._remove_btn.click()

    assert captured == [["https://example.com/a", "https://example.com/b"]]


def test_multi_select_skips_records_without_url() -> None:
    """A mixed selection acts on the actionable rows and silently skips the rest."""
    record_a = _record(url="https://example.com/a")
    record_no_url = _record(url=None)
    dialog = PendingDownloadsDialog([record_a, record_no_url])
    dialog._table.selectAll()

    assert dialog._download_btn.text() == "Download Now"  # only 1 actionable -> no count suffix

    captured: list[list[dict]] = []
    dialog.download_now_requested.connect(captured.append)
    dialog._download_btn.click()

    assert captured == [[record_a]]


def test_selection_survives_refresh_by_url_not_row_index() -> None:
    """set_records must re-select by URL; a reorder/shrink must not retarget the wrong row."""
    record_a = _record(url="https://example.com/a", title="A")
    record_b = _record(url="https://example.com/b", title="B")
    dialog = PendingDownloadsDialog([record_a, record_b])
    dialog._table.selectRow(1)  # select B

    dialog.set_records([record_b, record_a])  # order flipped, B still present

    captured: list[list[str]] = []
    dialog.remove_requested.connect(captured.append)
    dialog._remove_btn.click()

    assert captured == [["https://example.com/b"]]


def test_selection_dropped_when_record_removed_by_refresh() -> None:
    record_a = _record(url="https://example.com/a")
    record_b = _record(url="https://example.com/b")
    dialog = PendingDownloadsDialog([record_a, record_b])
    dialog._table.selectRow(1)  # select B

    dialog.set_records([record_a])  # B is gone

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_set_records_replaces_contents_and_count_label() -> None:
    record_a = _record()
    record_b = _record()
    dialog = PendingDownloadsDialog([record_a, record_b])

    dialog.set_records([])

    assert dialog._table.rowCount() == 0
    assert dialog._count_label.text() == "0 pending download(s)"


def test_set_records_clears_stale_selection_state() -> None:
    record = _record()
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    dialog.set_records([])

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


def test_unicode_title_renders() -> None:
    record = _record(title="配信中番組 🎥")
    dialog = PendingDownloadsDialog([record])

    assert dialog._table.item(0, 3).text() == "配信中番組 🎥"


# --- _sorted(): boundary matrix on the (int, float) tuple-key sort ---
# See release_status.parse_release_at / format_release_at for the parsing this
# sort key depends on; a bad value there must degrade to "unknown" (last), not raise.


def test_sorted_all_unknown_release_at_is_stable_insertion_order() -> None:
    """
    All records key to the same (1, 0.0) tuple; Python's sort is stable.

    Original insertion order must survive unchanged when nothing distinguishes
    the records on release time.
    """
    record_a = _record(title="A", release_at=None)
    record_b = _record(title="B", release_at=None)
    record_c = _record(title="C", release_at=None)
    dialog = PendingDownloadsDialog([record_a, record_b, record_c])

    titles = [dialog._table.item(row, 3).text() for row in range(3)]
    assert titles == ["A", "B", "C"]


def test_sorted_duplicate_timestamps_are_stable() -> None:
    """Two records with the identical release_at must keep their relative input order."""
    same_time = to_release_at(datetime.now().astimezone() + timedelta(hours=1))
    record_first = _record(title="First", release_at=same_time)
    record_second = _record(title="Second", release_at=same_time)
    dialog = PendingDownloadsDialog([record_first, record_second])

    titles = [dialog._table.item(row, 3).text() for row in range(2)]
    assert titles == ["First", "Second"]


def test_sorted_negative_pre_epoch_timestamp_sorts_by_real_chronology() -> None:
    """
    A pre-1970 release_at must sort as a known time (before everything else), not "unknown".

    (0, negative_timestamp) still compares less than any (0, positive_timestamp) tuple,
    and both compare less than the (1, 0.0) "unknown" bucket -- confirms the sort key
    doesn't accidentally collide negative timestamps with the unknown sentinel.
    """
    pre_epoch = to_release_at(datetime(1969, 6, 1, tzinfo=UTC))
    near_future = to_release_at(datetime.now().astimezone() + timedelta(hours=1))
    record_ancient = _record(title="Ancient", release_at=pre_epoch)
    record_soon = _record(title="Soon", release_at=near_future)
    record_unknown = _record(title="Unknown", release_at=None)

    dialog = PendingDownloadsDialog([record_soon, record_unknown, record_ancient])

    titles = [dialog._table.item(row, 3).text() for row in range(3)]
    assert titles == ["Ancient", "Soon", "Unknown"]


def test_sorted_zero_timestamp_epoch_sorts_before_positive_timestamps() -> None:
    """release_at exactly at the Unix epoch is a real (falsy-float) timestamp, not "unknown"."""
    epoch = to_release_at(datetime(1970, 1, 1, tzinfo=UTC))
    later = to_release_at(datetime.now().astimezone() + timedelta(hours=1))
    record_epoch = _record(title="Epoch", release_at=epoch)
    record_later = _record(title="Later", release_at=later)

    dialog = PendingDownloadsDialog([record_later, record_epoch])

    titles = [dialog._table.item(row, 3).text() for row in range(2)]
    assert titles == ["Epoch", "Later"]


def test_sorted_malformed_release_at_string_does_not_raise_and_sorts_last() -> None:
    """
    A malformed release_at (hand-edited/corrupt JSON) must degrade to "unknown", not crash.

    parse_release_at catches (TypeError, ValueError) from datetime.fromisoformat and
    returns None; _sorted's key() must turn that into the same (1, 0.0) bucket as an
    actually-missing release_at rather than letting the exception escape a Qt slot.
    """
    record_garbage = _record(title="Garbage", release_at="not-a-real-timestamp")
    record_known = _record(
        title="Known", release_at=to_release_at(datetime.now().astimezone() + timedelta(hours=1))
    )

    dialog = PendingDownloadsDialog([record_garbage, record_known])

    titles = [dialog._table.item(row, 3).text() for row in range(2)]
    assert titles == ["Known", "Garbage"]


def test_sorted_does_not_mutate_caller_list() -> None:
    """set_records must sort a copy; the caller's original list/order must be untouched."""
    near = to_release_at(datetime.now().astimezone() + timedelta(minutes=30))
    far = to_release_at(datetime.now().astimezone() + timedelta(hours=2))
    record_far = _record(title="Later", release_at=far)
    record_near = _record(title="Soon", release_at=near)
    caller_list = [record_far, record_near]

    PendingDownloadsDialog(caller_list)

    assert [r["title"] for r in caller_list] == ["Later", "Soon"]


# --- _can_act(): button-enablement boundary beyond url="" ---


def test_can_act_false_for_record_missing_url_key_entirely() -> None:
    """A record dict with no "url" key at all (schema-drifted JSON) must disable both buttons."""
    record = {"source": "1080", "kind": "live", "title": "t"}
    dialog = PendingDownloadsDialog([_record()])

    assert dialog._can_act(record) is False


def test_can_act_false_for_url_none() -> None:
    """url=None (explicit null in stored JSON) must disable both buttons, not raise."""
    dialog = PendingDownloadsDialog([_record()])

    assert dialog._can_act({"url": None, "source": "1080"}) is False


def test_can_act_false_for_none_record() -> None:
    """No selection (empty _selected_records()) must disable both buttons."""
    dialog = PendingDownloadsDialog([_record()])

    assert dialog._can_act(None) is False


def test_selection_of_record_missing_url_key_keeps_buttons_disabled() -> None:
    """End-to-end: selecting a row whose stashed record has no "url" key disables both buttons."""
    record = {"source": "1080", "kind": "live", "title": "no-url-key", "release_at": None}
    dialog = PendingDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._download_btn.isEnabled() is False
    assert dialog._remove_btn.isEnabled() is False


# --- row-rendering: entirely-missing keys (not just empty strings) ---


def test_row_renders_blank_for_completely_missing_optional_keys() -> None:
    """A record missing "kind"/"source"/"title" keys outright must render as empty cells, not raise."""
    record = {"url": "https://example.com/v", "release_at": None}
    dialog = PendingDownloadsDialog([record])

    assert dialog._table.item(0, 1).text() == ""
    assert dialog._table.item(0, 2).text() == ""
    assert dialog._table.item(0, 3).text() == ""


def test_tooltip_empty_when_no_error_and_no_url() -> None:
    """Tooltip fallback chain (last_error or url) must land on "" rather than "None"."""
    record = {"kind": "live", "source": "1080", "title": "t", "release_at": None}
    dialog = PendingDownloadsDialog([record])

    assert dialog._table.item(0, 0).toolTip() == ""

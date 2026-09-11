"""
Tests for FailedDownloadsDialog.

MyWindow itself stays untested here: importing meadowlark.pyw spins up timers
and keyring access as a side effect of module import, and no existing test in
this suite does that either.
"""

from unittest.mock import patch

import pytest
from PyQt6.QtCore import QItemSelectionModel, QPoint
from PyQt6.QtWidgets import QApplication, QMenu

from src.failed_downloads import record_video_id
from src.failed_downloads_dialog import FailedDownloadsDialog

_app = QApplication.instance() or QApplication([])


def _record(
    key: str = "key1",
    urls: list[str] | None = None,
    source: str = "1080",
    site: str = "youtube",
    title: str = "Test Title",
    failed_at: str = "2025-01-01 12:00:00",
    error: str = "",
) -> dict:
    return {
        "key": key,
        "urls": urls if urls is not None else ["https://example.com/video"],
        "source": source,
        "site": site,
        "title": title,
        "failed_at": failed_at,
        "error": error,
    }


def _select_rows(dialog: FailedDownloadsDialog, *rows: int) -> None:
    """Select multiple rows at once (selectRow() replaces the selection each call)."""
    model = dialog._table.model()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows:
        dialog._table.selectionModel().select(model.index(row, 0), flags)


def test_rows_match_records() -> None:
    record_a = _record(key="a", site="youtube", title="First Video")
    record_b = _record(key="b", site="twitch", title="Second Video")
    dialog = FailedDownloadsDialog([record_a, record_b])

    assert dialog._table.rowCount() == 2
    assert dialog._table.item(0, 1).text() == record_a["site"]
    assert dialog._table.item(0, 3).text() == record_a["title"]


def test_error_shown_as_tooltip() -> None:
    record = _record(error="HTTP 403")
    dialog = FailedDownloadsDialog([record])

    for col in range(dialog._table.columnCount()):
        item = dialog._table.item(0, col)
        assert item is not None
        assert item.toolTip() == "HTTP 403"


def test_buttons_disabled_without_selection() -> None:
    dialog = FailedDownloadsDialog([_record()])

    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is False
    assert dialog._mark_downloaded_btn.isEnabled() is False


def test_retry_emits_full_record() -> None:
    record = _record()
    with patch(
        "src.failed_downloads_dialog.get_source_options", return_value={"format": "best"}
    ):
        dialog = FailedDownloadsDialog([record])
        dialog._table.selectRow(0)

        captured: list[list[dict]] = []
        dialog.retry_requested.connect(captured.append)

        dialog._retry_btn.click()

    assert captured == [[record]]


def test_delete_emits_key() -> None:
    record = _record(key="delete-me")
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    captured: list[list[str]] = []
    dialog.delete_requested.connect(captured.append)

    dialog._delete_btn.click()

    assert captured == [[record["key"]]]


def test_mark_downloaded_enabled_for_youtube_with_extractable_id() -> None:
    record = _record(site="youtube", urls=["https://www.youtube.com/watch?v=abc123"])
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._mark_downloaded_btn.isEnabled() is True


def test_mark_downloaded_disabled_for_non_youtube_url() -> None:
    record = _record(site="twitch", urls=["https://www.twitch.tv/videos/12345"])
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._mark_downloaded_btn.isEnabled() is False


def test_mark_downloaded_enabled_despite_unknown_site_tag() -> None:
    """
    A record with site="unknown" but a real YouTube URL still enables the button.

    A playlist-sourced failure's "site" can be wrongly "unknown" even for a real
    YouTube URL: detect_site_from_urls tags site from the *playlist file's* raw
    entries, and a bare-playlist-id file (e.g. 720playlists.txt) never contains
    "youtube.com", so every failure sourced from it is mistagged. The gate must
    not depend on that tag -- only on whether the failure's own URL resolves.
    """
    record = _record(site="unknown", urls=["https://www.youtube.com/watch?v=abc123"])
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._mark_downloaded_btn.isEnabled() is True


def test_mark_downloaded_disabled_for_playlist_level_failure() -> None:
    """A playlist/channel-level failure has no per-video ID to archive."""
    record = _record(site="youtube", urls=["https://www.youtube.com/playlist?list=PLx"])
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._mark_downloaded_btn.isEnabled() is False


def test_mark_downloaded_disabled_when_urls_missing() -> None:
    record = _record(site="youtube")
    del record["urls"]
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._mark_downloaded_btn.isEnabled() is False


def test_mark_downloaded_emits_full_record() -> None:
    record = _record(site="youtube", urls=["https://www.youtube.com/watch?v=abc123"])
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    captured: list[list[dict]] = []
    dialog.mark_downloaded_requested.connect(captured.append)

    dialog._mark_downloaded_btn.click()

    assert captured == [[record]]


@pytest.mark.parametrize(
    "urls",
    [
        "https://www.youtube.com/watch?v=abc123",  # a bare string, not a list
        [],
    ],
    ids=["urls-is-a-string-not-a-list", "urls-is-an-empty-list"],
)
def test_video_id_none_for_malformed_urls_shapes(urls: object) -> None:
    """
    ``record_video_id`` must reject both sides of its ``isinstance``/truthiness guard.

    A stored record's ``urls`` field could plausibly be a bare string rather
    than a list (a hand-edited or schema-drifted JSON file), or an explicit
    empty list. Both must disable the action rather than index into the
    wrong type or raise.
    """
    record = _record(site="youtube", urls=urls)

    assert record_video_id(record) is None


def test_retry_disabled_for_unknown_source() -> None:
    record = _record(source="unknown")
    with patch("src.failed_downloads_dialog.get_source_options", return_value={}):
        dialog = FailedDownloadsDialog([record])
        dialog._table.selectRow(0)

    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is True


def test_set_records_refreshes() -> None:
    record_a = _record(key="a")
    record_b = _record(key="b")
    dialog = FailedDownloadsDialog([record_a, record_b])

    dialog.set_records([record_a])

    assert dialog._table.rowCount() == 1
    assert "1" in dialog._count_label.text()


def test_empty_records() -> None:
    dialog = FailedDownloadsDialog([])

    assert dialog._table.rowCount() == 0
    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is False
    assert dialog._mark_downloaded_btn.isEnabled() is False


# --- _can_retry / _delete_selected: malformed-record boundary (real, unmocked
# get_source_options). A malformed record must never raise out of these: they
# run from Qt slots, and PyQt6 aborts the whole interpreter (no traceback, no
# catchable exception) when an exception escapes a signal-invoked slot, so a
# raise here would kill the running app rather than log an error. ---


def test_can_retry_false_for_missing_source_key() -> None:
    """
    A record missing "source" must disable Retry, not raise.

    Reachable in production any time a stored record is missing this field
    (e.g. hand-edited or schema-drifted JSON) and the dialog is opened -
    _on_selection_changed calls _can_retry on every selection change, so
    merely selecting the row (not clicking anything) reaches this.
    """
    record = {"key": "k", "urls": ["u"], "site": "s", "title": "t", "failed_at": "t", "error": ""}
    dialog = FailedDownloadsDialog([])

    assert dialog._can_retry(record) is False


def test_can_retry_false_for_none_source() -> None:
    """A record with source=None must disable Retry rather than reach int(None)."""
    record = {"key": "k", "source": None, "urls": ["u"], "site": "s", "title": "t"}
    dialog = FailedDownloadsDialog([])

    assert dialog._can_retry(record) is False


def test_malformed_record_selection_does_not_raise() -> None:
    """Selecting a malformed row must be inert: Retry and Delete both disabled."""
    record = {"key": "", "source": None, "urls": [], "title": "t", "site": "s", "failed_at": "t"}
    dialog = FailedDownloadsDialog([record])

    dialog._table.selectRow(0)

    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is False


@pytest.mark.parametrize("source", ["unknown", "garbage-source", "1080", "audio"])
def test_can_retry_true_for_any_nonempty_string_source(source: str) -> None:
    """
    Document that the "unknown source disables Retry" gate rarely fires in production.

    get_source_options() has a catch-all fallback (any string that doesn't
    parse as a resolution still returns a full options dict), so it never
    returns falsy for a real non-empty string - including "unknown". Retry
    stays enabled there by design: the fallback still yields a usable download.
    Only a missing/None/empty source disables it.
    """
    dialog = FailedDownloadsDialog([])

    assert dialog._can_retry({"key": "k", "source": source}) is True


def test_delete_disabled_for_record_without_key() -> None:
    """A key-less record cannot be deleted (nothing to address) and must not raise."""
    record = _record()
    del record["key"]
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    assert dialog._delete_btn.isEnabled() is False

    received: list[list[str]] = []
    dialog.delete_requested.connect(received.append)
    dialog._delete_selected()

    assert received == []


def test_selected_records_missing_urls_key_returns_none_for_urls() -> None:
    """A record with no "urls" key must not crash selection or context-menu gating."""
    record = _record()
    del record["urls"]
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    selected = dialog._selected_records()
    assert len(selected) == 1
    assert selected[0].get("urls") is None


# --- Open in Browser: only web URLs may reach webbrowser. On Windows a non-URL
# string makes os.startfile fail, and webbrowser silently falls through to the
# next registered browser (msedge.exe) instead of the user's default. ---


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/playlist?list=PLx", "http://example.com/v", "HTTPS://EXAMPLE.COM/v"],
)
def test_browser_url_offers_web_urls(url: str) -> None:
    assert FailedDownloadsDialog._browser_url(_record(urls=[url])) == url


@pytest.mark.parametrize(
    "urls",
    [
        ["PLRWvNQVqAeWKt7kCUfEMdJi40m7H58CJd"],
        ["some-slug"],
        ["C:/Videos/clip.mp4"],
        ["javascript:alert(1)"],
        [],
        [None],
        [12345],
    ],
)
def test_browser_url_rejects_non_web_urls(urls: list) -> None:
    assert FailedDownloadsDialog._browser_url(_record(urls=urls)) is None


@pytest.mark.parametrize("record", [None, {"key": "k"}, {"key": "k", "urls": "https://x.com"}])
def test_browser_url_tolerates_malformed_records(record: dict | None) -> None:
    assert FailedDownloadsDialog._browser_url(record) is None


# --- set_records / selection interaction across a shrinking table ---


def test_selection_drops_records_removed_by_refresh() -> None:
    """
    Selection is restored by record key, not row index.

    Selecting row 2 (key "c") of [a, b, c], then refreshing to just [a],
    must leave the selection empty rather than silently retargeting row 0
    (record_a) the way the old index-based behavior would have -- a record
    that left the store must never stay implicitly "selected".
    """
    record_a, record_b, record_c = _record(key="a"), _record(key="b"), _record(key="c")
    dialog = FailedDownloadsDialog([record_a, record_b, record_c])
    dialog._table.selectRow(2)
    assert [r["key"] for r in dialog._selected_records()] == ["c"]

    dialog.set_records([record_a])

    assert dialog._selected_records() == []
    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is False
    assert dialog._mark_downloaded_btn.isEnabled() is False


def test_selection_cleared_when_shrunk_to_empty() -> None:
    record_a, record_b = _record(key="a"), _record(key="b")
    dialog = FailedDownloadsDialog([record_a, record_b])
    dialog._table.selectRow(1)

    dialog.set_records([])

    assert dialog._table.currentRow() == -1
    assert dialog._selected_records() == []
    assert dialog._retry_btn.isEnabled() is False
    assert dialog._delete_btn.isEnabled() is False


# --- count label / record-count boundary ---


@pytest.mark.parametrize(("count", "expected"), [(0, "0 failed download(s)"), (1, "1 failed download(s)"), (3, "3 failed download(s)")])
def test_count_label_text(count: int, expected: str) -> None:
    records = [_record(key=str(i)) for i in range(count)]
    dialog = FailedDownloadsDialog(records)

    assert dialog._count_label.text() == expected


# --- display-value tolerance: non-string / unicode / very long fields ---


def test_retry_emits_all_selected_records() -> None:
    record_a, record_b, record_c = _record(key="a"), _record(key="b"), _record(key="c")
    with patch(
        "src.failed_downloads_dialog.get_source_options", return_value={"format": "best"}
    ):
        dialog = FailedDownloadsDialog([record_a, record_b, record_c])
        _select_rows(dialog, 0, 2)

        captured: list[list[dict]] = []
        dialog.retry_requested.connect(captured.append)

        assert dialog._retry_btn.text() == "Retry (2)"
        dialog._retry_btn.click()

    assert captured == [[record_a, record_c]]


def test_select_all_delete_emits_every_key() -> None:
    record_a, record_b, record_c = _record(key="a"), _record(key="b"), _record(key="c")
    dialog = FailedDownloadsDialog([record_a, record_b, record_c])
    dialog._table.selectAll()

    assert dialog._delete_btn.text() == "Delete (3)"

    captured: list[list[str]] = []
    dialog.delete_requested.connect(captured.append)
    dialog._delete_btn.click()

    assert captured == [["a", "b", "c"]]


def test_mark_downloaded_acts_on_eligible_subset() -> None:
    record_a = _record(key="a", site="youtube", urls=["https://www.youtube.com/watch?v=abc123"])
    record_b = _record(key="b", site="twitch", urls=["https://www.twitch.tv/videos/12345"])
    dialog = FailedDownloadsDialog([record_a, record_b])
    _select_rows(dialog, 0, 1)

    assert dialog._mark_downloaded_btn.isEnabled() is True
    assert dialog._mark_downloaded_btn.text() == "Mark as Downloaded"
    assert dialog._mark_downloaded_btn.toolTip().startswith("1 of 2 selected will be skipped")

    captured: list[list[dict]] = []
    dialog.mark_downloaded_requested.connect(captured.append)
    dialog._mark_downloaded_btn.click()

    assert captured == [[record_a]]


def test_single_selection_labels_have_no_count() -> None:
    record = _record()
    with patch(
        "src.failed_downloads_dialog.get_source_options", return_value={"format": "best"}
    ):
        dialog = FailedDownloadsDialog([record])
        dialog._table.selectRow(0)

        assert dialog._retry_btn.text() == "Retry"
        assert dialog._delete_btn.text() == "Delete"
        assert dialog._delete_btn.toolTip() == ""


def test_selection_follows_keys_when_rows_shift() -> None:
    record_a, record_b, record_c = _record(key="a"), _record(key="b"), _record(key="c")
    record_new = _record(key="new")
    dialog = FailedDownloadsDialog([record_a, record_b, record_c])
    _select_rows(dialog, 1, 2)

    dialog.set_records([record_new, record_a, record_b, record_c])

    assert [r["key"] for r in dialog._selected_records()] == ["b", "c"]
    selected_rows = {index.row() for index in dialog._table.selectionModel().selectedRows()}
    assert selected_rows == {2, 3}


def test_refresh_with_non_string_key_does_not_raise() -> None:
    record = _record()
    record["key"] = ["x"]
    dialog = FailedDownloadsDialog([record])
    dialog._table.selectRow(0)

    dialog.set_records([record])

    assert dialog._delete_btn.isEnabled() is False
    assert dialog._selected_records() == []


def test_non_string_title_rendered_via_str() -> None:
    record = _record(title=12345)  # type: ignore[arg-type]
    dialog = FailedDownloadsDialog([record])

    assert dialog._table.item(0, 3).text() == "12345"


def test_unicode_and_long_title_rendered_and_tooltip_preserved() -> None:
    long_title = "日本語タイトル 🎬" * 50
    error = "エラー: " + "x" * 1000
    record = _record(title=long_title, error=error)
    dialog = FailedDownloadsDialog([record])

    assert dialog._table.item(0, 3).text() == long_title
    assert dialog._table.item(0, 3).toolTip() == error


# --- Context menu: separate wiring path from the button row (_show_context_menu
# builds and connects its own QMenu), so a swap between action label and slot
# there would not be caught by any button-click test. ---


def test_context_menu_action_triggers_the_matching_signal() -> None:
    record = _record(key="ctx", urls=["https://www.youtube.com/watch?v=abc123"])
    with patch(
        "src.failed_downloads_dialog.get_source_options", return_value={"format": "best"}
    ):
        dialog = FailedDownloadsDialog([record])
        dialog._table.selectRow(0)

        captured_menu: dict[str, QMenu] = {}
        with patch.object(
            QMenu, "exec", lambda self, *_a, **_kw: captured_menu.setdefault("menu", self)
        ):
            dialog._show_context_menu(QPoint(0, 0))

    actions = {action.text(): action for action in captured_menu["menu"].actions()}
    assert set(actions) == {"Retry", "Mark as Downloaded", "Delete", "Open in Browser"}

    retry_calls: list[list[dict]] = []
    delete_calls: list[list[str]] = []
    dialog.retry_requested.connect(retry_calls.append)
    dialog.delete_requested.connect(delete_calls.append)

    actions["Delete"].trigger()

    assert delete_calls == [["ctx"]]
    assert retry_calls == []

"""Non-blocking dialog for reviewing, retrying, and deleting failed downloads."""

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .failed_downloads import record_video_id
from .logging_utils import log_exception
from .url_utils import web_url
from .ydl_options import get_source_options

_COLUMNS = ("Failed At", "Site", "Type", "Title")
_RECORD_ROLE = Qt.ItemDataRole.UserRole + 1
_UNKNOWN_SOURCE_TOOLTIP = "Unknown source type — cannot rebuild download options"
_NOT_YOUTUBE_TOOLTIP = "Only available for YouTube videos with a resolvable video ID"


@dataclass(frozen=True)
class _Targets:
    """The current selection, partitioned by which action can act on each record."""

    selected: int = 0
    retry: list[dict] = field(default_factory=list)
    mark_downloaded: list[dict] = field(default_factory=list)
    delete_keys: list[str] = field(default_factory=list)
    browser_urls: list[str] = field(default_factory=list)


class _ActionSpec(NamedTuple):
    """One row of the button row / context menu: its label, targets, slot and skip reason."""

    label: str
    count: int
    slot: Callable[[], None]
    reason: str  # why a record is skipped; "" = no tooltip


def _label(base: str, count: int) -> str:
    """Suffix the action label with its target count once it covers more than one row."""
    return f"{base} ({count})" if count > 1 else base


def _skip_tooltip(count: int, selected: int, reason: str) -> str:
    """Explain why an action is disabled, or how much of the selection it will skip."""
    if not reason:
        return ""
    if count == 0:
        return reason
    if count < selected:
        return f"{selected - count} of {selected} selected will be skipped - {reason}"
    return ""


class FailedDownloadsDialog(QDialog):
    """
    Non-blocking dialog listing failed downloads.

    The dialog is deliberately dumb: it renders whatever records it is handed and
    emits intent signals. All store mutation is owned by MyWindow, which pushes
    refreshed record lists back via set_records(). Every action applies to all
    selected rows; a row an action cannot handle is skipped and never blocks the
    rest.
    """

    retry_requested = pyqtSignal(list)  # list[dict]: every retryable selected record
    delete_requested = pyqtSignal(list)  # list[str]: key of every deletable selected record
    mark_downloaded_requested = pyqtSignal(list)  # list[dict]: every markable selected record

    def __init__(self, records: list[dict], parent: QWidget | None = None) -> None:
        """Build the dialog layout and populate it with the given records."""
        super().__init__(parent)
        self.setWindowTitle("Failed Downloads")
        self.resize(900, 400)
        self.setModal(False)

        self._records: list[dict] = []

        layout = QVBoxLayout()
        self._table = self._build_table()
        layout.addWidget(self._table)
        layout.addLayout(self._build_button_row())
        self.setLayout(layout)

        self.set_records(records)

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_COLUMNS))
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Qt's default, stated so the multi-row contract is explicit in the code.
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.itemSelectionChanged.connect(self._on_selection_changed)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        return table

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setEnabled(False)
        self._retry_btn.clicked.connect(self._retry_selected)
        row.addWidget(self._retry_btn)

        self._mark_downloaded_btn = QPushButton("Mark as Downloaded")
        self._mark_downloaded_btn.setEnabled(False)
        self._mark_downloaded_btn.clicked.connect(self._mark_downloaded_selected)
        row.addWidget(self._mark_downloaded_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        row.addWidget(self._delete_btn)

        row.addStretch()

        self._count_label = QLabel()
        row.addWidget(self._count_label)

        return row

    def set_records(self, records: list[dict]) -> None:
        """Replace the table contents with the given records (newest-first)."""
        # Re-select by record key, never by row index: every refresh can shift
        # rows (_on_download_failed inserts new failures at row 0; a delete
        # shrinks the table), and Qt's index-based selection would otherwise stay
        # on the old row numbers - silently pointing a multi-row Delete/Retry at
        # records the user never highlighted.
        selected_keys = {
            key for r in self._selected_records() if isinstance(key := r.get("key"), str) and key
        }
        self._records = records
        self._table.setRowCount(len(records))

        for row, record in enumerate(records):
            values = (
                record.get("failed_at", ""),
                record.get("site", ""),
                record.get("source", ""),
                record.get("title", ""),
            )
            error = record.get("error", "")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                # The error string is the only place the user learns *why* it
                # failed; put it on every cell so any hover reveals it.
                item.setToolTip(error)
                if col == 0:
                    item.setData(_RECORD_ROLE, record)
                self._table.setItem(row, col, item)

        self._restore_selection(selected_keys)
        self._count_label.setText(f"{len(records)} failed download(s)")
        self._on_selection_changed()

    def _restore_selection(self, keys: set[str]) -> None:
        model = self._table.model()
        last_col = model.columnCount() - 1
        selection = QItemSelection()
        for row, record in enumerate(self._records):
            key = record.get("key") if isinstance(record, dict) else None
            if isinstance(key, str) and key in keys:
                selection.select(model.index(row, 0), model.index(row, last_col))
        # ClearAndSelect with an empty selection clears it - records that left
        # the store (deleted, retried, marked) drop out of the selection.
        self._table.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )

    def _selected_records(self) -> list[dict]:
        """Return a record for every selected row, top-to-bottom; rows without one are skipped."""
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        items = [self._table.item(row, 0) for row in rows]
        records = [item.data(_RECORD_ROLE) for item in items if item is not None]
        return [record for record in records if isinstance(record, dict)]

    def _targets(self) -> _Targets:
        records = self._selected_records()
        return _Targets(
            selected=len(records),
            retry=[r for r in records if self._can_retry(r)],
            mark_downloaded=[r for r in records if record_video_id(r) is not None],
            delete_keys=[r["key"] for r in records if self._can_delete(r)],
            browser_urls=[url for r in records if (url := self._browser_url(r)) is not None],
        )

    def _action_specs(self, targets: _Targets) -> list[_ActionSpec]:
        # Order matches the button row: Retry, Mark as Downloaded, Delete.
        return [
            _ActionSpec(
                "Retry",
                len(targets.retry),
                self._retry_selected,
                _UNKNOWN_SOURCE_TOOLTIP,
            ),
            _ActionSpec(
                "Mark as Downloaded",
                len(targets.mark_downloaded),
                self._mark_downloaded_selected,
                _NOT_YOUTUBE_TOOLTIP,
            ),
            _ActionSpec("Delete", len(targets.delete_keys), self._delete_selected, ""),
        ]

    def _can_retry(self, record: dict | None) -> bool:
        # Records come from a user-editable JSON file on disk, so a malformed or
        # old-schema entry must never reach get_source_options: an exception
        # escaping a Qt slot aborts the interpreter rather than being catchable.
        if not record:
            return False
        source = record.get("source")
        if not isinstance(source, str) or not source:
            return False
        try:
            return bool(get_source_options(source))
        except (TypeError, ValueError, OSError) as exc:
            log_exception(exc, f"Cannot rebuild download options for source {source!r}")
            return False

    def _can_delete(self, record: dict) -> bool:
        # A truthy non-string key (a hand-edited store can hold a list) must not
        # land in _Targets.delete_keys, which is typed and emitted as list[str].
        key = record.get("key")
        return isinstance(key, str) and bool(key)

    def _on_selection_changed(self) -> None:
        targets = self._targets()
        buttons = (self._retry_btn, self._mark_downloaded_btn, self._delete_btn)
        for button, spec in zip(buttons, self._action_specs(targets), strict=True):
            button.setText(_label(spec.label, spec.count))
            button.setEnabled(spec.count > 0)
            button.setToolTip(_skip_tooltip(spec.count, targets.selected, spec.reason))

    def _retry_selected(self) -> None:
        records = self._targets().retry
        if records:
            self.retry_requested.emit(records)

    def _mark_downloaded_selected(self) -> None:
        records = self._targets().mark_downloaded
        if records:
            self.mark_downloaded_requested.emit(records)

    def _delete_selected(self) -> None:
        keys = self._targets().delete_keys
        if keys:
            self.delete_requested.emit(keys)

    @staticmethod
    def _browser_url(record: dict | None) -> str | None:
        # Records come from a user-editable JSON file, and older ones may hold a
        # bare id rather than a URL, so every shape is checked, never trusted.
        urls = record.get("urls") if record else None
        if not isinstance(urls, list) or not urls:
            return None
        return web_url(urls[0])

    def _show_context_menu(self, pos: QPoint) -> None:
        # Qt keeps a multi-row selection when the right-click lands on an
        # already-selected row, so the menu acts on the same rows as the buttons.
        targets = self._targets()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for spec in self._action_specs(targets):
            action = menu.addAction(_label(spec.label, spec.count))
            action.setEnabled(spec.count > 0)
            action.setToolTip(_skip_tooltip(spec.count, targets.selected, spec.reason))
            if spec.count > 0:
                action.triggered.connect(spec.slot)

        urls = targets.browser_urls
        open_action = menu.addAction(_label("Open in Browser", len(urls)))
        open_action.setEnabled(bool(urls))
        if urls:
            open_action.triggered.connect(lambda: self._open_urls(urls))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    @staticmethod
    def _open_urls(urls: list[str]) -> None:
        for url in urls:
            webbrowser.open_new_tab(url)

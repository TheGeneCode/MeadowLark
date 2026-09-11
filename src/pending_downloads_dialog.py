"""Non-blocking dialog for reviewing downloads parked until they become available."""

import webbrowser

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
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

from .release_status import format_release_at, parse_release_at
from .url_utils import web_url

_COLUMNS = ("Available At", "Kind", "Type", "Title")
_RECORD_ROLE = Qt.ItemDataRole.UserRole + 1


class PendingDownloadsDialog(QDialog):
    """
    Non-blocking dialog listing pending downloads.

    The dialog is deliberately dumb: it renders whatever records it is handed and
    emits intent signals. All store mutation is owned by MyWindow, which pushes
    refreshed record lists back via set_records().
    """

    download_now_requested = pyqtSignal(dict)  # full record
    remove_requested = pyqtSignal(str)  # record url

    def __init__(self, records: list[dict], parent: QWidget | None = None) -> None:
        """Build the dialog layout and populate it with the given records."""
        super().__init__(parent)
        self.setWindowTitle("Pending Downloads")
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

        self._download_btn = QPushButton("Download Now")
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._download_selected)
        row.addWidget(self._download_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(self._remove_btn)

        row.addStretch()

        self._count_label = QLabel()
        row.addWidget(self._count_label)

        return row

    def _sorted(self, records: list[dict]) -> list[dict]:
        """Soonest release first; records with no known release time go last."""

        def key(record: dict) -> tuple[int, float]:
            parsed = parse_release_at(record.get("release_at"))
            if parsed is None:
                return (1, 0.0)
            return (0, parsed.timestamp())

        return sorted(records, key=key)

    def set_records(self, records: list[dict]) -> None:
        """Replace the table contents with the given records (soonest-release-first)."""
        self._records = self._sorted(records)
        self._table.setRowCount(len(self._records))

        for row, record in enumerate(self._records):
            values = (
                format_release_at(record.get("release_at")),
                record.get("kind", ""),
                record.get("source", ""),
                record.get("title", ""),
            )
            tooltip = record.get("last_error") or record.get("url", "")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                # The error string is the only place the user learns *why*
                # nothing has downloaded yet; put it on every cell so any
                # hover reveals it.
                item.setToolTip(tooltip)
                if col == 0:
                    item.setData(_RECORD_ROLE, record)
                self._table.setItem(row, col, item)

        self._count_label.setText(f"{len(self._records)} pending download(s)")
        self._on_selection_changed()

    def _selected_record(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        return item.data(_RECORD_ROLE)  # type: ignore[return-value]

    def _can_act(self, record: dict | None) -> bool:
        return bool(record) and bool(record.get("url"))  # type: ignore[union-attr]

    def _on_selection_changed(self) -> None:
        record = self._selected_record()
        can_act = self._can_act(record)
        self._download_btn.setEnabled(can_act)
        self._remove_btn.setEnabled(can_act)

    def _download_selected(self) -> None:
        record = self._selected_record()
        if record is not None:
            self.download_now_requested.emit(record)

    def _remove_selected(self) -> None:
        record = self._selected_record()
        url = record.get("url") if record else None
        if url:
            self.remove_requested.emit(url)

    def _show_context_menu(self, pos: QPoint) -> None:
        record = self._selected_record()
        can_act = self._can_act(record)
        url = record.get("url") if record else None

        menu = QMenu(self)

        download_action = menu.addAction("Download Now")
        download_action.setEnabled(can_act)
        if can_act:
            download_action.triggered.connect(self._download_selected)

        remove_action = menu.addAction("Remove")
        remove_action.setEnabled(can_act)
        if can_act:
            remove_action.triggered.connect(self._remove_selected)

        browser_url = web_url(url)
        open_action = menu.addAction("Open in Browser")
        open_action.setEnabled(browser_url is not None)
        if browser_url is not None:
            open_action.triggered.connect(lambda: webbrowser.open_new_tab(browser_url))

        menu.exec(self._table.viewport().mapToGlobal(pos))

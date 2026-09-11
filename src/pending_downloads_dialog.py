"""Non-blocking dialog for reviewing downloads parked until they become available."""

import webbrowser

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

from .release_status import format_release_at, parse_release_at
from .url_utils import web_url

_COLUMNS = ("Available At", "Kind", "Type", "Title")
_RECORD_ROLE = Qt.ItemDataRole.UserRole + 1


def _label(base: str, count: int) -> str:
    """Suffix the action label with its target count once it covers more than one row."""
    return f"{base} ({count})" if count > 1 else base


class PendingDownloadsDialog(QDialog):
    """
    Non-blocking dialog listing pending downloads.

    The dialog is deliberately dumb: it renders whatever records it is handed and
    emits intent signals. All store mutation is owned by MyWindow, which pushes
    refreshed record lists back via set_records(). Every action applies to all
    selected rows; a row without a usable URL is skipped and never blocks the rest.
    """

    download_now_requested = pyqtSignal(list)  # list[dict]: every actionable selected record
    remove_requested = pyqtSignal(list)  # list[str]: url of every actionable selected record

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
        # Re-select by URL, never by row index: a refresh can reorder rows (release
        # time changes move a record's sort position) or shrink the table (a remove
        # drops rows), and Qt's index-based selection would otherwise stay on the old
        # row numbers - silently pointing a multi-row Download Now/Remove at records
        # the user never highlighted.
        selected_urls = {
            url for r in self._selected_records() if isinstance(url := r.get("url"), str) and url
        }
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

        self._restore_selection(selected_urls)
        self._count_label.setText(f"{len(self._records)} pending download(s)")
        self._on_selection_changed()

    def _restore_selection(self, urls: set[str]) -> None:
        model = self._table.model()
        last_col = model.columnCount() - 1
        selection = QItemSelection()
        for row, record in enumerate(self._records):
            url = record.get("url") if isinstance(record, dict) else None
            if isinstance(url, str) and url in urls:
                selection.select(model.index(row, 0), model.index(row, last_col))
        # ClearAndSelect with an empty selection clears it - records that left the
        # store (removed, downloaded) drop out of the selection.
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

    def _can_act(self, record: dict | None) -> bool:
        return bool(record) and bool(record.get("url"))  # type: ignore[union-attr]

    def _actionable_records(self) -> list[dict]:
        return [r for r in self._selected_records() if self._can_act(r)]

    def _on_selection_changed(self) -> None:
        count = len(self._actionable_records())
        self._download_btn.setText(_label("Download Now", count))
        self._download_btn.setEnabled(count > 0)
        self._remove_btn.setText(_label("Remove", count))
        self._remove_btn.setEnabled(count > 0)

    def _download_selected(self) -> None:
        records = self._actionable_records()
        if records:
            self.download_now_requested.emit(records)

    def _remove_selected(self) -> None:
        records = self._actionable_records()
        if records:
            self.remove_requested.emit([r["url"] for r in records])

    def _show_context_menu(self, pos: QPoint) -> None:
        # Qt keeps a multi-row selection when the right-click lands on an
        # already-selected row, so the menu acts on the same rows as the buttons.
        records = self._actionable_records()
        count = len(records)

        menu = QMenu(self)

        download_action = menu.addAction(_label("Download Now", count))
        download_action.setEnabled(count > 0)
        if count > 0:
            download_action.triggered.connect(self._download_selected)

        remove_action = menu.addAction(_label("Remove", count))
        remove_action.setEnabled(count > 0)
        if count > 0:
            remove_action.triggered.connect(self._remove_selected)

        browser_urls = [url for r in records if (url := web_url(r.get("url"))) is not None]
        open_action = menu.addAction(_label("Open in Browser", len(browser_urls)))
        open_action.setEnabled(bool(browser_urls))
        if browser_urls:
            open_action.triggered.connect(lambda: self._open_urls(browser_urls))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    @staticmethod
    def _open_urls(urls: list[str]) -> None:
        for url in urls:
            webbrowser.open_new_tab(url)

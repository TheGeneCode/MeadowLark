"""Non-blocking dialog for browsing download history."""

import webbrowser

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from QYT import parse_history_log

from .config import ARCHIVE_PATH
from .logging_utils import log_exception
from .podcast_filtering import load_downloaded_video_ids
from .url_utils import extract_video_id, web_url

_COLUMNS = ("Datetime", "Site", "Type", "Title", "Result")
_RESULT_OPTIONS = ("All", "SUCCESS", "FAIL", "SKIPPED")
_NO_URL_TOOLTIP = "URL not available for older log entries"
_VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_ARCHIVED_FG = QColor(100, 149, 237)


class HistoryDialog(QDialog):
    """Non-blocking dialog showing download history in a table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Load history and build the dialog layout."""
        super().__init__(parent)
        self.setWindowTitle("Download History")
        self.resize(900, 500)
        self.setModal(False)

        self._all_records = parse_history_log()
        self._archive_ids: set[str] = load_downloaded_video_ids(str(ARCHIVE_PATH))

        self._layout = QVBoxLayout()

        if not self._all_records:
            self._empty_label: QLabel | None = QLabel("No download history found.")
            self._layout.addWidget(self._empty_label)
        else:
            self._empty_label = None
            self._layout.addLayout(self._build_filter_bar())
            self._table = self._build_table()
            self._layout.addWidget(self._table)
            self._count_label = QLabel()
            self._layout.addWidget(self._count_label)
            self._apply_filters()

        self.setLayout(self._layout)

    def _build_filter_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search title…")
        self._search.textChanged.connect(self._apply_filters)
        bar.addWidget(self._search)

        sites = sorted({r["site"] for r in self._all_records})
        types = sorted({r["dtype"] for r in self._all_records})

        bar.addWidget(QLabel("Site:"))
        self._site_combo = self._make_combo(["All", *sites])
        bar.addWidget(self._site_combo)

        bar.addWidget(QLabel("Type:"))
        self._type_combo = self._make_combo(["All", *types])
        bar.addWidget(self._type_combo)

        bar.addWidget(QLabel("Result:"))
        self._result_combo = self._make_combo(list(_RESULT_OPTIONS))
        bar.addWidget(self._result_combo)

        self._open_btn = QPushButton("Open in Browser")
        self._open_btn.setEnabled(False)
        self._open_btn.setToolTip(_NO_URL_TOOLTIP)
        self._open_btn.clicked.connect(self._open_selected)
        bar.addWidget(self._open_btn)

        return bar

    def _make_combo(self, items: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.currentTextChanged.connect(self._apply_filters)
        return combo

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(len(self._all_records), len(_COLUMNS))
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.itemSelectionChanged.connect(self._on_selection_changed)

        for row, entry in enumerate(self._all_records):
            table.setItem(row, 0, QTableWidgetItem(entry["dt"]))
            table.setItem(row, 1, QTableWidgetItem(entry["site"]))
            table.setItem(row, 2, QTableWidgetItem(entry["dtype"]))

            title_item = QTableWidgetItem(entry["title"])
            title_item.setData(Qt.ItemDataRole.UserRole, entry["url"])
            video_id = extract_video_id(entry["url"])
            title_item.setData(_VIDEO_ID_ROLE, video_id)
            table.setItem(row, 3, title_item)

            table.setItem(row, 4, QTableWidgetItem(entry["result"]))

            if video_id and video_id in self._archive_ids:
                self._apply_archive_style_to(table, row, in_archive=True)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        return table

    def _apply_archive_style_to(
        self, table: QTableWidget, row: int, *, in_archive: bool
    ) -> None:
        brush = QBrush(_ARCHIVED_FG) if in_archive else QBrush()
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                item.setForeground(brush)

    def _apply_archive_style(self, row: int, *, in_archive: bool) -> None:
        self._apply_archive_style_to(self._table, row, in_archive=in_archive)

    def _refresh_archive_styles_for(self, video_id: str) -> None:
        in_archive = video_id in self._archive_ids
        for row in range(self._table.rowCount()):
            title_item = self._table.item(row, 3)
            if title_item is not None and title_item.data(_VIDEO_ID_ROLE) == video_id:
                self._apply_archive_style_to(self._table, row, in_archive=in_archive)

    def _get_selected_url(self) -> str | None:
        row = self._table.currentRow()
        if row < 0 or self._table.isRowHidden(row):
            return None
        title_item = self._table.item(row, 3)
        if title_item is None:
            return None
        return web_url(title_item.data(Qt.ItemDataRole.UserRole))

    def _get_selected_video_id(self) -> str | None:
        row = self._table.currentRow()
        if row < 0 or self._table.isRowHidden(row):
            return None
        title_item = self._table.item(row, 3)
        if title_item is None:
            return None
        return title_item.data(_VIDEO_ID_ROLE)  # type: ignore[return-value]

    def _on_selection_changed(self) -> None:
        url = self._get_selected_url()
        self._open_btn.setEnabled(bool(url))
        self._open_btn.setToolTip("" if url else _NO_URL_TOOLTIP)

    def _open_selected(self) -> None:
        url = self._get_selected_url()
        if url:
            webbrowser.open_new_tab(url)

    def _show_context_menu(self, pos: QPoint) -> None:
        url = self._get_selected_url()
        video_id = self._get_selected_video_id()
        in_archive = bool(video_id and video_id in self._archive_ids)

        menu = QMenu(self)

        open_action = menu.addAction("Open in Browser")
        open_action.setEnabled(bool(url))
        if not url:
            open_action.setToolTip(_NO_URL_TOOLTIP)
        if url:
            open_action.triggered.connect(lambda: webbrowser.open_new_tab(url))

        del_action = menu.addAction("Delete from Archive")
        del_action.setEnabled(in_archive)
        if not in_archive:
            del_action.setToolTip("Not in archive" if video_id else _NO_URL_TOOLTIP)
        if in_archive:
            del_action.triggered.connect(lambda: self._delete_from_archive(video_id))  # type: ignore[arg-type]

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _delete_from_archive(self, video_id: str) -> None:
        try:
            with ARCHIVE_PATH.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            log_exception(exc, "HistoryDialog: read archive")
            QMessageBox.warning(self, "Archive Error", f"Could not read archive:\n{exc}")
            return

        new_lines = [ln for ln in lines if not _archive_line_matches(ln, video_id)]

        # Atomic write: write to a sibling temp file then replace to avoid truncation
        # on write failure.
        tmp_path = ARCHIVE_PATH.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                fh.writelines(new_lines)
            tmp_path.replace(ARCHIVE_PATH)
        except OSError as exc:
            log_exception(exc, "HistoryDialog: write archive")
            QMessageBox.warning(
                self, "Archive Error", f"Could not update archive:\n{exc}"
            )
            tmp_path.unlink(missing_ok=True)
            return

        self._archive_ids.discard(video_id)
        self._refresh_archive_styles_for(video_id)

    def prepend_row(self, record: dict) -> None:
        """Insert a new history record at the top of the table (newest-first)."""
        if not hasattr(self, "_table"):
            # Dialog was opened while history was empty — bootstrap the full UI now.
            if self._empty_label is not None:
                self._layout.removeWidget(self._empty_label)
                self._empty_label.deleteLater()
                self._empty_label = None
            self._archive_ids = load_downloaded_video_ids(str(ARCHIVE_PATH))
            video_id_boot = extract_video_id(record.get("url"))
            if video_id_boot and record.get("result") == "SUCCESS":
                self._archive_ids.add(video_id_boot)
            self._all_records = [record]
            self._layout.addLayout(self._build_filter_bar())
            self._table = self._build_table()
            self._layout.addWidget(self._table)
            self._count_label = QLabel()
            self._layout.addWidget(self._count_label)
            self._apply_filters()
            return

        self._archive_ids = load_downloaded_video_ids(str(ARCHIVE_PATH))
        self._all_records.insert(0, record)
        self._table.insertRow(0)

        self._table.setItem(0, 0, QTableWidgetItem(record["dt"]))
        self._table.setItem(0, 1, QTableWidgetItem(record["site"]))
        self._table.setItem(0, 2, QTableWidgetItem(record["dtype"]))

        title_item = QTableWidgetItem(record["title"])
        title_item.setData(Qt.ItemDataRole.UserRole, record["url"])
        video_id = extract_video_id(record["url"])
        title_item.setData(_VIDEO_ID_ROLE, video_id)
        self._table.setItem(0, 3, title_item)

        self._table.setItem(0, 4, QTableWidgetItem(record["result"]))

        if video_id and record.get("result") == "SUCCESS":
            self._archive_ids.add(video_id)
        if video_id:
            self._refresh_archive_styles_for(video_id)

        for combo, value in (
            (self._site_combo, record["site"]),
            (self._type_combo, record["dtype"]),
        ):
            if combo.findText(value) == -1:
                combo.blockSignals(True)
                combo.addItem(value)
                combo.blockSignals(False)

        self._apply_filters()

    def _apply_filters(self) -> None:
        title_q = self._search.text().lower()
        site_f = self._site_combo.currentText()
        type_f = self._type_combo.currentText()
        result_f = self._result_combo.currentText()

        visible = 0
        for row, entry in enumerate(self._all_records):
            show = (
                (not title_q or title_q in entry["title"].lower())
                and (site_f == "All" or entry["site"] == site_f)
                and (type_f == "All" or entry["dtype"] == type_f)
                and _result_matches(entry["result"], result_f)
            )
            if show:
                self._table.showRow(row)
                visible += 1
            else:
                self._table.hideRow(row)

        self._count_label.setText(f"{visible} of {len(self._all_records)} records")
        self._on_selection_changed()


def _result_matches(result: str, filter_val: str) -> bool:
    """Return True if result string satisfies the chosen filter option."""
    if filter_val == "All":
        return True
    if filter_val == "SKIPPED":
        return result.startswith("SKIPPED")
    return result == filter_val


def _archive_line_matches(line: str, video_id: str) -> bool:
    """Return True if this archive line records the given video_id."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return stripped.split()[-1] == video_id

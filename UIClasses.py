"""
Defines custom PyQt6 widgets for playlist selection and drag-and-drop functionality.

PlaylistDialog provides a dialog for users to specify which videos from a playlist to select, supporting both manual input and drag-and-drop of URLs.

DropLabel is a QLabel subclass that accepts dropped URLs, emits a signal when URLs are dropped, and provides visual feedback.

"""

from os import startfile
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

import utils
from src.playlist_utils import write_template_playlist_file
from src.settings_dialog import get_setting


class PlaylistDialog(QDialog):
    """
    A dialog for selecting specific videos from a playlist, supporting manual input and drag-and-drop of URLs.

    Provides a text input for specifying video indices and an OK button to confirm selection. Emits a signal when URLs are dropped.
    """

    ADDED_TEXT = "Added!!!"
    urls_dropped = pyqtSignal(list, str)

    def __init__(self, playlist_count: int, parent: QWidget = None) -> None:
        """
        Initialize the playlist selection dialog, setting up the window title, input field, and OK button.

        Displays the total number of videos in the playlist and allows users to specify which videos to select.
        """
        super().__init__(parent)

        self.setWindowTitle(self.tr("Playlist Dialog"))
        if get_setting("VID_DL_ALWAYS_ON_TOP"):
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        label = QLabel(
            self.tr(
                f"There are {playlist_count} videos in the playlist. Which do you want? Blank = all or format like (3,5,7-9)",
            ),
        )
        label.setFont(QFont(QFont().defaultFamily(), 12))
        self.playlistInput = QLineEdit()
        ok_button = QPushButton()
        ok_button.setText("OK")
        ok_button.clicked.connect(self.accept)

        layout = QGridLayout()
        layout.addWidget(label, 0, 0, 1, 2)
        layout.addWidget(self.playlistInput, 1, 0)
        layout.addWidget(ok_button, 1, 1)

        self.setLayout(layout)

    def get_playlist_input(self) -> str:
        """
        Return the current text entered in the playlist input field.

        Whitespace is stripped everywhere, not just at the ends: yt-dlp's
        ``playlist_items`` parser matches each comma-separated segment against a
        regex with no whitespace tolerance, so a naturally-typed selector like
        "1-3, 5 - 10" would otherwise pass this dialog and fail deep inside
        yt-dlp's option validation instead.

        Returns:
            str: The text from the playlist input, with all whitespace removed.
        """
        return "".join(self.playlistInput.text().split())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """
        Handle the drag enter event.

        Args:
            event (QDragEnterEvent): The drag enter event.
        """
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()


class DropLabel(QLabel):
    """
    .

    A QLabel subclass that accepts drag-and-drop of URLs, emits a signal when URLs are dropped, and provides visual feedback by temporarily changing its text.

    Args:
        text (str): The label's initial text.
        color (str): The background color for the label.
        connection (callable): Slot to connect to the urls_dropped signal.
        source_key (str | None): Stable routing key emitted on drop; defaults to text if not provided.
        text_color (str): Foreground text color.
        min_size (int): Minimum width and height of the label, in pixels.
        font_size (int): Point size of the label's font.

    Signals:
        urls_dropped (list, str): Emitted with a list of dropped URLs and the original label text.
    """

    ADDED_TEXT = "Added!!!"
    urls_dropped = pyqtSignal(list, str)

    def __init__(
        self,
        text: str,
        color: str,
        connection: Any,
        source_key: str | None = None,
        *,
        text_color: str = "#FFFFFF",
        min_size: int = 150,
        font_size: int = 32,
    ) -> None:
        """
        Initialize the label with custom text, background color, and a connection for the URLs dropped signal.

        Args:
            text (str): The label text.
            color (str): The background color.
            connection (callable): Slot to connect to the urls_dropped signal.
            source_key (str | None): Stable routing key emitted on drop; defaults to text if not provided.
            text_color (str): Foreground text color.
            min_size (int): Minimum width and height of the label, in pixels.
            font_size (int): Point size of the label's font.
        """
        super().__init__(text)
        font_family = "Arial"
        self.originalText = text
        self.source_key = source_key if source_key is not None else text
        # Foreground must be explicit. The app runs the Fusion style with no custom
        # palette, so an unstyled QLabel inherits the *system* text colour — near
        # black under Windows light mode, which is unreadable on the dark rungs and
        # was already marginal for the two colours that shipped before presets.
        self.setStyleSheet(f"background-color:{color};color:{text_color};")
        self.setMinimumSize(min_size, min_size)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(QFont(font_family, font_size))
        self.urls_dropped.connect(connection)
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._revert_text)

    def dragEnterEvent(self, event: QDragEnterEvent) -> bool:
        """
        Handle the drag enter event.

        Args:
            event (QDragEnterEvent): The drag enter event.
        """
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def _revert_text(self) -> None:
        self.setText(self.originalText)

    def dropEvent(self, event: QDropEvent) -> None:
        """
        Handle the drop event by updating the label text, starting a timer to revert the text, and emitting the dropped URLs via the urls_dropped signal.

        Args:
            event (QDropEvent): The drop event containing the dropped data.
        """
        self.setText(self.ADDED_TEXT)
        self.timer.start(2000)
        urls = event.mimeData().urls()
        self.urls_dropped.emit([url.toString() for url in urls], self.source_key)

    # def dropEvent(self, event):
    #     def timeout():
    #         self.setText(self.originalText)

    #     self.setText("Added!!!")
    #     t = Timer(2, timeout)
    #     t.start()
    #     urls = event.mimeData().urls()
    #     self.urlsDropped.emit([url.toString() for url in urls], self.originalText)


class PlaylistButton(QPushButton):
    """
    .

    A QPushButton subclass that opens a playlist file on right-click.

    Displays a button that opens the associated playlist file when right-clicked.
    """

    def __init__(
        self,
        text: str,
        playlist_path: str | Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        .

        Initialize the button with text, playlist path, and optional Qt arguments.

        Args:
            text: The button label text.
            playlist_path: Path to the playlist file.
            *args: Additional positional arguments for QPushButton.
            **kwargs: Additional keyword arguments for QPushButton.
        """
        super().__init__(text, *args, **kwargs)
        self.playlist_path = Path(playlist_path)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        .

        Handle mouse press events to open playlist on right-click.

        Args:
            event: The mouse press event.
        """
        if event.button() == Qt.MouseButton.RightButton:
            if not self.playlist_path.exists():
                try:
                    write_template_playlist_file(self.playlist_path)
                except (OSError, ValueError) as exc:
                    # ValueError: Path.exists() swallows an embedded-null-byte path and
                    # returns False, but mkdir/write_text raise ValueError for the same path.
                    utils.log_exception(
                        exc,
                        "Failed to create template playlist file on right-click",
                    )
                    return
            startfile(self.playlist_path)  # noqa: S606  (os.startfile, no shell/subprocess)
        else:
            super().mousePressEvent(event)

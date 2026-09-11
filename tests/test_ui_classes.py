"""Unit tests for UIClasses module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from UIClasses import DropLabel, PlaylistButton, PlaylistDialog

# Ensure QApplication exists for Qt testing
_app = QApplication.instance() or QApplication([])


class TestPlaylistDialog:
    """Tests for PlaylistDialog class."""

    def test_playlist_dialog_initialization(self) -> None:
        """Test PlaylistDialog initializes with playlist count."""
        dialog = PlaylistDialog(50)
        assert dialog.windowTitle() == "Playlist Dialog"
        assert hasattr(dialog, "playlistInput")

    def test_get_playlist_input_empty(self) -> None:
        """Test get_playlist_input returns empty string by default."""
        dialog = PlaylistDialog(10)
        assert dialog.get_playlist_input() == ""

    def test_get_playlist_input_with_text(self) -> None:
        """Test get_playlist_input returns entered text."""
        dialog = PlaylistDialog(10)
        dialog.playlistInput.setText("1,3,5-7")
        assert dialog.get_playlist_input() == "1,3,5-7"

    def test_get_playlist_input_strips_interior_whitespace(self) -> None:
        """
        A naturally-typed selector with spaces must reach yt-dlp whitespace-free.

        yt-dlp's playlist_items parser fullmatches each comma-separated segment
        against a regex with zero whitespace tolerance, so "1-3, 5 - 10" (a
        perfectly readable selector) would otherwise raise ValueError deep in
        yt-dlp's option validation.
        """
        dialog = PlaylistDialog(10)
        dialog.playlistInput.setText("1-3, 5 - 10")
        assert dialog.get_playlist_input() == "1-3,5-10"

    def test_drag_enter_event_with_urls(self) -> None:
        """Test dragEnterEvent accepts URLs."""
        dialog = PlaylistDialog(10)
        event = MagicMock(spec=QDragEnterEvent)
        event.mimeData.return_value.hasUrls.return_value = True

        dialog.dragEnterEvent(event)
        event.accept.assert_called_once()

    def test_drag_enter_event_without_urls(self) -> None:
        """Test dragEnterEvent ignores non-URL drops."""
        dialog = PlaylistDialog(10)
        event = MagicMock(spec=QDragEnterEvent)
        event.mimeData.return_value.hasUrls.return_value = False

        dialog.dragEnterEvent(event)
        event.ignore.assert_called_once()


class TestDropLabel:
    """Tests for DropLabel class."""

    def test_drop_label_initialization(self) -> None:
        """Test DropLabel initializes with text, color, and connection."""
        connection = MagicMock()
        label = DropLabel("Drop Files", "#FFFFFF", connection)

        assert label.originalText == "Drop Files"
        assert label.text() == "Drop Files"
        assert label.acceptDrops() is True

    def test_drop_label_drag_enter_with_urls(self) -> None:
        """Test DropLabel accepts drag enter for URLs."""
        connection = MagicMock()
        label = DropLabel("Drop", "#FFF", connection)
        event = MagicMock(spec=QDragEnterEvent)
        event.mimeData.return_value.hasUrls.return_value = True

        label.dragEnterEvent(event)
        event.accept.assert_called_once()

    def test_drop_label_drag_enter_without_urls(self) -> None:
        """Test DropLabel ignores drag enter for non-URLs."""
        connection = MagicMock()
        label = DropLabel("Drop", "#FFF", connection)
        event = MagicMock(spec=QDragEnterEvent)
        event.mimeData.return_value.hasUrls.return_value = False

        label.dragEnterEvent(event)
        event.ignore.assert_called_once()

    def test_drop_event_emits_signal(self) -> None:
        """Test dropEvent emits urls_dropped signal."""
        connection = MagicMock()
        label = DropLabel("Original", "#FFF", connection)
        captured = []
        label.urls_dropped.connect(lambda urls, text: captured.append((urls, text)))

        # Create mock URL objects
        url_mock = MagicMock()
        url_mock.toString.return_value = "https://example.com/video"

        event = MagicMock(spec=QDropEvent)
        event.mimeData.return_value.urls.return_value = [url_mock]

        label.dropEvent(event)

        # Verify signal was emitted with correct data
        assert captured == [(["https://example.com/video"], "Original")]

    def test_drop_event_changes_text_temporarily(self) -> None:
        """Test dropEvent changes text to ADDED_TEXT then reverts."""
        connection = MagicMock()
        label = DropLabel("Original", "#FFF", connection)
        mock_signal = MagicMock()
        label.urls_dropped.connect(mock_signal)

        url_mock = MagicMock()
        url_mock.toString.return_value = "https://example.com"

        event = MagicMock(spec=QDropEvent)
        event.mimeData.return_value.urls.return_value = [url_mock]

        label.dropEvent(event)

        # Text should be changed to ADDED_TEXT immediately
        assert label.text() == "Added!!!"
        # Timer should be set up
        assert label.timer.isSingleShot() is True

    def test_droplabel_defaults_unchanged(self) -> None:
        """Test DropLabel keeps its pre-Phase-4 defaults when no keyword args are given."""
        connection = MagicMock()
        label = DropLabel("Drop", "#424769", connection)

        assert label.minimumWidth() == 150
        assert label.font().pointSize() == 32
        stylesheet = label.styleSheet()
        assert "background-color:#424769" in stylesheet
        assert "color:#FFFFFF" in stylesheet

    def test_droplabel_applies_text_color(self) -> None:
        """Test DropLabel applies a custom text_color to its stylesheet."""
        connection = MagicMock()
        label = DropLabel("x", "#CBCEE2", connection, text_color="#1A1B2E")

        assert "color:#1A1B2E" in label.styleSheet()

    def test_droplabel_applies_min_size_and_font(self) -> None:
        """Test DropLabel applies custom min_size and font_size."""
        connection = MagicMock()
        label = DropLabel("x", "#FFF", connection, min_size=100, font_size=20)

        assert label.minimumWidth() == 100
        assert label.minimumHeight() == 100
        assert label.font().pointSize() == 20

    def test_droplabel_positional_construction_still_works(self) -> None:
        """Test the pre-Phase-4 three-positional-arg construction still works unmodified."""
        connection = MagicMock()

        label_a = DropLabel("Drop Files", "#FFFFFF", connection)
        assert label_a.originalText == "Drop Files"
        assert label_a.text() == "Drop Files"
        assert label_a.acceptDrops() is True

        label_b = DropLabel("Drop", "#FFF", connection)
        assert label_b.originalText == "Drop"

        label_c = DropLabel("Original", "#FFF", connection)
        assert label_c.originalText == "Original"

        label_d = DropLabel("Original", "#FFF", connection)
        assert label_d.text() == "Original"


class TestPlaylistButton:
    """Tests for PlaylistButton class."""

    def test_playlist_button_initialization(self) -> None:
        """Test PlaylistButton initializes with text and path."""
        button = PlaylistButton("My Playlist", "/path/to/playlist.txt")
        assert button.text() == "My Playlist"
        assert button.playlist_path == Path("/path/to/playlist.txt")

    def test_playlist_button_mouse_press_non_right_click(self) -> None:
        """Test non-right-click mouse press uses default handling."""
        button = PlaylistButton("Playlist", "/path/to/file.txt")

        # Mock the parent class method
        with patch.object(button.__class__.__bases__[0], "mousePressEvent"):
            event = MagicMock(spec=QMouseEvent)
            event.button.return_value = Qt.MouseButton.LeftButton

            button.mousePressEvent(event)
            # Should call parent implementation for non-right-click

    @patch("UIClasses.startfile")
    def test_playlist_button_mouse_press_right_click_exists(
        self,
        mock_startfile: MagicMock,
    ) -> None:
        """Test right-click opens playlist file if it exists."""
        with patch("UIClasses.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            mock_path_class.return_value = mock_path

            button = PlaylistButton("Playlist", "/path/to/file.txt")
            button.playlist_path = mock_path

            event = MagicMock(spec=QMouseEvent)
            event.button.return_value = Qt.MouseButton.RightButton

            button.mousePressEvent(event)

            mock_startfile.assert_called_once_with(mock_path)

    @patch("UIClasses.startfile")
    @patch("UIClasses.write_template_playlist_file")
    def test_playlist_button_mouse_press_right_click_not_exists(
        self,
        mock_write_template: MagicMock,
        mock_startfile: MagicMock,
    ) -> None:
        """Right-click on a missing playlist file creates a template then opens it."""
        with patch("UIClasses.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_path_class.return_value = mock_path

            button = PlaylistButton("Playlist", "/path/to/file.txt")
            button.playlist_path = mock_path

            event = MagicMock(spec=QMouseEvent)
            event.button.return_value = Qt.MouseButton.RightButton

            button.mousePressEvent(event)

            mock_write_template.assert_called_once_with(mock_path)
            mock_startfile.assert_called_once_with(mock_path)

    @patch("UIClasses.startfile")
    @patch("UIClasses.write_template_playlist_file")
    def test_playlist_button_mouse_press_right_click_template_write_fails(
        self,
        mock_write_template: MagicMock,
        mock_startfile: MagicMock,
    ) -> None:
        """If template creation fails, the error is logged and the file is not opened."""
        mock_write_template.side_effect = OSError("disk full")
        with patch("UIClasses.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            mock_path_class.return_value = mock_path

            button = PlaylistButton("Playlist", "/path/to/file.txt")
            button.playlist_path = mock_path

            event = MagicMock(spec=QMouseEvent)
            event.button.return_value = Qt.MouseButton.RightButton

            with patch("UIClasses.utils.log_exception") as mock_log:
                button.mousePressEvent(event)
                mock_log.assert_called_once()

            mock_startfile.assert_not_called()

    @patch("UIClasses.startfile")
    def test_playlist_button_mouse_press_right_click_null_byte_path_logged(
        self,
        mock_startfile: MagicMock,
    ) -> None:
        """
        An embedded null byte in playlist_path is logged, not raised.

        Path.exists() swallows the ValueError for a null-byte path and reports
        False, but write_template_playlist_file's real mkdir/write_text calls
        raise ValueError (not OSError) for that same path. mousePressEvent
        catches both so the failure is logged like other failures instead of
        escaping the Qt event handler.
        """
        button = PlaylistButton("Playlist", "/path/to/file.txt")
        button.playlist_path = Path("evil\x00name/playlist.txt")

        event = MagicMock(spec=QMouseEvent)
        event.button.return_value = Qt.MouseButton.RightButton

        with patch("UIClasses.utils.log_exception") as mock_log:
            button.mousePressEvent(event)
            mock_log.assert_called_once()

        mock_startfile.assert_not_called()

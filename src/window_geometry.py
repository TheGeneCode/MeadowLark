"""
Dialogs that reopen at the size and position they were last closed at.

Qt's ``QWidget.saveGeometry()`` blob encodes size, position, screen and maximised
state, and ``restoreGeometry()`` validates it against the screens attached *now* --
so it survives unplugging a monitor in a way a stored ``width x height`` pair does
not.  The blob is binary, so it is base64-encoded before it goes into the
line-oriented AppData ``.env`` store that already backs the Settings dialog and the
app-update timestamp.
"""

import base64
import binascii
import logging

from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QDialog, QWidget

from .settings_dialog import _persist_setting, get_setting

logger = logging.getLogger(__name__)


def encode_geometry(widget: QWidget) -> str:
    """Return *widget*'s current geometry as a base64 string fit for the .env store."""
    return base64.b64encode(bytes(widget.saveGeometry())).decode("ascii")


def apply_geometry(
    widget: QWidget,
    saved: object,
    default_size: tuple[int, int],
) -> bool:
    """
    Restore *saved* geometry onto *widget*, falling back to *default_size*.

    Returns True when the stored blob was applied.  Saved geometry is advisory
    state, not configuration: an absent value (first run), a non-string, a
    corrupt blob, or one Qt rejects because it no longer fits the current screens
    all mean "we don't know better than the default", so each leaves the widget
    at *default_size* rather than raising.

    Args:
        widget: The window to position and size.
        saved: Value read from the settings store; anything but a non-empty
            base64 string is treated as absent.
        default_size: ``(width, height)`` used when nothing usable was stored.
    """
    if isinstance(saved, str) and saved:
        try:
            blob = base64.b64decode(saved.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError):
            logger.warning("Ignoring unreadable saved window geometry")
        else:
            if widget.restoreGeometry(QByteArray(blob)):
                return True
            logger.info("Saved window geometry no longer fits this display")
    widget.resize(*default_size)
    return False


class GeometryMemoryDialog(QDialog):
    """A dialog that restores its last geometry on first show and stores it when dismissed."""

    def __init__(
        self,
        parent: QWidget | None,
        settings_key: str,
        default_size: tuple[int, int],
    ) -> None:
        super().__init__(parent)
        self._geometry_key = settings_key
        self._default_size = default_size
        self._geometry_restored = False

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802
        """Apply the stored geometry the first time this dialog is shown."""
        # First show only.  Re-applying on every show would undo a resize the user
        # made while the dialog stayed open and was merely hidden and shown again.
        if not self._geometry_restored:
            self._geometry_restored = True
            apply_geometry(
                self,
                get_setting(self._geometry_key),
                self._default_size,
            )
        super().showEvent(event)

    def done(self, result: int) -> None:
        """Store the geometry the user is dismissing the dialog at."""
        # done() -- not closeEvent() -- is where every dismissal converges: the
        # title-bar X reaches closeEvent, which QDialog turns into reject() and then
        # done(), while a RejectRole/AcceptRole button calls reject()/accept()
        # directly and never sends a close event at all.  Saving here also runs
        # before super() hides the widget and before WA_DeleteOnClose destroys it.
        self.save_geometry()
        super().done(result)

    def save_geometry(self) -> None:
        """Write the current geometry to the settings store."""
        try:
            _persist_setting(self._geometry_key, encode_geometry(self))
        except OSError as exc:
            # A window that cannot record its size must still close.
            logger.warning("Could not save window geometry: %s", exc)

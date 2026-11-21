"""
Live always-on-top toggling must not hide or recreate the main window's native window.

``QWidget.setWindowFlags()`` on a shown window goes through ``setParent()``: it hides the
window and recreates its native handle. The Settings dialog -- an owned window of the main
window -- is always open when the toggle is applied, and recreating the owner under it left
the main window permanently unexposed: it never repainted and showed white once Settings
closed.
"""

from collections.abc import Iterator

import pytest
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from src.window_settings import SettingsUpdatesMixin

_app = QApplication.instance() or QApplication([])
_ON_TOP = Qt.WindowType.WindowStaysOnTopHint


class _Window(QWidget):
    _apply_always_on_top = SettingsUpdatesMixin._apply_always_on_top


class _HideCounter(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.hides = 0

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Hide:
            self.hides += 1
        return False


@pytest.fixture
def shown_window_with_owned_dialog() -> Iterator[_Window]:
    window = _Window()
    window.show()
    dialog = QDialog(window)
    dialog.show()
    _app.processEvents()
    yield window
    dialog.close()
    window.close()


@pytest.mark.parametrize("initially_on_top", [False, True])
def test_toggle_keeps_native_window_and_applies_flag(
    shown_window_with_owned_dialog: _Window, initially_on_top: bool
) -> None:
    window = shown_window_with_owned_dialog
    window._apply_always_on_top(initially_on_top)
    _app.processEvents()
    win_id = int(window.winId())
    counter = _HideCounter()
    window.installEventFilter(counter)

    window._apply_always_on_top(not initially_on_top)
    _app.processEvents()

    assert counter.hides == 0
    assert int(window.winId()) == win_id
    assert window.isVisible()
    assert bool(window.windowFlags() & _ON_TOP) is (not initially_on_top)
    handle = window.windowHandle()
    assert handle is not None
    assert bool(handle.flags() & _ON_TOP) is (not initially_on_top)


def test_toggle_before_first_show_takes_effect_on_show() -> None:
    window = _Window()
    window._apply_always_on_top(True)
    assert not window.isVisible()
    window.show()
    _app.processEvents()
    handle = window.windowHandle()
    assert handle is not None
    assert handle.flags() & _ON_TOP
    window.close()

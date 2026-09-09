"""
Lifetime tests for the main window's reopenable non-modal dialogs.

Invariant under test: closing one of these dialogs destroys it, so reopening
never accumulates hidden dialogs parented to the main window.  Without
``WA_DeleteOnClose`` a closed dialog stays alive as an invisible child, every
reopen builds another one, and each one's ``destroyed`` handler only runs at
app teardown -- where it clears the reference belonging to the *live* dialog.
"""

from collections.abc import Iterator

import meadowlark
import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from src import history_dialog as _history_mod
from src import window_geometry as _geometry_mod

_app = QApplication.instance() or QApplication([])


class _StubWindow(QWidget):
    """A real QWidget parent wired to the real MyWindow dialog openers."""

    def __init__(self) -> None:
        super().__init__()
        self._podcast_status_dialog: QDialog | None = None
        self._podcast_status_table = None
        self._podcast_last_statuses: list[dict] = []
        self._history_dialog: QDialog | None = None
        self._settings_dialog: QDialog | None = None

    _show_podcast_status = meadowlark.MyWindow._show_podcast_status
    _on_podcast_status_dialog_destroyed = (
        meadowlark.MyWindow._on_podcast_status_dialog_destroyed
    )
    _show_history = meadowlark.MyWindow._show_history
    _on_history_dialog_destroyed = meadowlark.MyWindow._on_history_dialog_destroyed
    _open_settings = meadowlark.MyWindow._open_settings

    def reload_settings(self, changes: dict) -> None:
        """Stand in for the real slot; the dialogs only need it to be connectable."""


def _settle() -> None:
    """Run the deferred deletions a close() schedules, as the event loop would."""
    _app.processEvents()
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _app.processEvents()


@pytest.fixture
def win(monkeypatch: pytest.MonkeyPatch) -> Iterator[_StubWindow]:
    """Return a stub window whose dialogs touch neither the settings store nor the logs."""
    monkeypatch.setattr(_geometry_mod, "_persist_setting", lambda *_args: None)
    monkeypatch.setattr(_geometry_mod, "get_setting", lambda *_args: None)
    monkeypatch.setattr(_history_mod, "parse_history_log", list)
    monkeypatch.setattr(_history_mod, "load_downloaded_video_ids", lambda _p: set())
    window = _StubWindow()
    yield window
    window.deleteLater()
    _settle()


@pytest.mark.parametrize(
    ("opener", "ref_attr"),
    [
        ("_show_podcast_status", "_podcast_status_dialog"),
        ("_show_history", "_history_dialog"),
        ("_open_settings", "_settings_dialog"),
    ],
)
# Both ways a user can dismiss these dialogs: the window's X button reaches
# close(), while a "Close"/"Cancel" button with RejectRole reaches reject().
@pytest.mark.parametrize("closer", ["close", "reject"])
def test_reopening_a_dialog_leaves_exactly_one_alive(
    win: _StubWindow, opener: str, ref_attr: str, closer: str
) -> None:
    """Open/close/open must destroy the first dialog rather than hide it forever."""
    getattr(win, opener)()
    first = getattr(win, ref_attr)
    assert first is not None

    getattr(first, closer)()
    _settle()
    assert getattr(win, ref_attr) is None, f"{closer}() must clear the cached reference"

    getattr(win, opener)()
    assert len(win.findChildren(QDialog)) == 1


def test_closing_a_dialog_does_not_orphan_the_live_one(win: _StubWindow) -> None:
    """The destroyed handler must not clear a reference that points elsewhere."""
    win._show_podcast_status()
    stale = win._podcast_status_dialog
    stale.close()
    _settle()

    win._show_podcast_status()
    current = win._podcast_status_dialog
    _settle()

    assert current is not None
    assert current is not stale
    assert win._podcast_status_dialog is current

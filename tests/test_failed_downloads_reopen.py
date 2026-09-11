"""
Tests for MyWindow._show_failed_downloads reopen behaviour.

The dialog has no WA_DeleteOnClose, so closing it hides the widget without
destroying it. The cached instance must therefore be re-shown, not merely
raised, or the button silently no-ops after the first close.
"""

from unittest.mock import MagicMock

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

from src.failed_downloads_dialog import FailedDownloadsDialog

_app = QApplication.instance() or QApplication([])


def _make_window(existing: MagicMock | None) -> MagicMock:
    """Return a stub window wired to the real _show_failed_downloads body."""
    import meadowlark

    win = MagicMock()
    win._failed_dialog = existing
    win._show_failed_downloads = meadowlark.MyWindow._show_failed_downloads.__get__(win)
    return win


def test_cached_hidden_dialog_is_shown_again() -> None:
    """A closed-but-alive dialog must be re-shown, raised, and focused."""
    dialog = MagicMock()
    win = _make_window(dialog)

    win._show_failed_downloads()

    dialog.show.assert_called_once()
    dialog.raise_.assert_called_once()
    dialog.activateWindow.assert_called_once()
    assert win._failed_dialog is dialog


def test_dead_cached_dialog_falls_through_to_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the cached C++ object is gone, a fresh dialog replaces it."""
    import meadowlark

    dialog = MagicMock()
    dialog.show.side_effect = RuntimeError("wrapped C/C++ object has been deleted")
    win = _make_window(dialog)

    replacement = MagicMock()
    monkeypatch.setattr(
        meadowlark, "FailedDownloadsDialog", lambda *_args, **_kwargs: replacement
    )
    monkeypatch.setattr(meadowlark, "load_failed_downloads", lambda _path: [])

    win._show_failed_downloads()

    replacement.show.assert_called_once()
    assert win._failed_dialog is replacement


def test_new_dialog_signals_wire_to_the_matching_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Each of the three signals must reach its own handler, not a swapped one.

    _show_failed_downloads wires three signals to three differently-shaped
    handlers in one block; a copy-paste swap (e.g. delete_requested wired to
    _retry_failed_downloads) would still type-check and would only surface as
    the wrong action running in production.
    """
    import meadowlark

    win = _make_window(None)
    monkeypatch.setattr(meadowlark, "load_failed_downloads", lambda _path: [])
    monkeypatch.setattr(
        meadowlark,
        "FailedDownloadsDialog",
        lambda records, *_args, **_kwargs: FailedDownloadsDialog(records),
    )
    # _show_failed_downloads calls the real .show(), which would otherwise open a
    # real top-level window with no parent.
    monkeypatch.setattr(FailedDownloadsDialog, "show", lambda _self: None)

    win._show_failed_downloads()
    dialog = win._failed_dialog

    dialog.retry_requested.emit([{"key": "r"}])
    dialog.delete_requested.emit(["d"])
    dialog.mark_downloaded_requested.emit([{"key": "m"}])

    win._retry_failed_downloads.assert_called_once_with([{"key": "r"}])
    win._delete_failed_downloads.assert_called_once_with(["d"])
    win._mark_failed_downloaded.assert_called_once_with([{"key": "m"}])

    # dialog.destroyed is connected to win._on_failed_dialog_destroyed (a
    # MagicMock attribute). If the real QDialog is instead left for Python's
    # garbage collector, its C++ destructor -- and the destroyed signal it
    # fires -- runs at interpreter shutdown, when calling back into Python is
    # no longer safe and crashes the process natively. Destroying it here,
    # while the interpreter is still fully alive, fires that signal safely.
    sip.delete(dialog)

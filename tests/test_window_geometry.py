"""
Tests for window geometry persistence (src/window_geometry.py).

Invariant under test: a GeometryMemoryDialog reopens at the geometry it was
closed at, and falls back to its caller's default size whenever nothing usable
is stored.  The last class covers the wiring that made this visible -- the
Podcast Status window, which used to open at its layout's size hint every time.

Sizes stay inside 800x600 because CI runs with QT_QPA_PLATFORM=offscreen, whose
virtual screen is that size; restoreGeometry() legitimately shrinks a window
that no longer fits the current screen, which would make larger sizes flaky.
"""

import importlib
import os
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest
from dotenv import dotenv_values
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from src import config as _config_mod
from src import settings_dialog as _sd_mod
from src.window_geometry import (
    GeometryMemoryDialog,
    apply_geometry,
    encode_geometry,
)

_app = QApplication.instance() or QApplication([])

_KEY = "VID_DL_TEST_GEOMETRY"
_DEFAULT = (700, 450)


class TestApplyGeometry:
    """Boundary tests for apply_geometry()'s fallback contract."""

    @pytest.mark.parametrize(
        "saved",
        [None, "", 12345, b"AAAA", "!!!not base64!!!"],
    )
    def test_unusable_value_falls_back_to_default(self, saved: object) -> None:
        widget = QWidget()
        assert apply_geometry(widget, saved, _DEFAULT) is False
        assert (widget.width(), widget.height()) == _DEFAULT

    def test_valid_base64_that_is_not_a_geometry_blob_falls_back(self) -> None:
        # Decodes cleanly but Qt rejects it (wrong magic number) -- the widget must
        # still end up usable rather than at whatever size Qt left it.
        widget = QWidget()
        assert apply_geometry(widget, "AAAAAAAAAAA=", _DEFAULT) is False
        assert (widget.width(), widget.height()) == _DEFAULT

    def test_round_trip_restores_saved_size(self) -> None:
        source = QWidget()
        source.resize(640, 480)
        blob = encode_geometry(source)

        target = QWidget()
        assert apply_geometry(target, blob, _DEFAULT) is True
        assert (target.width(), target.height()) == (640, 480)


class TestGeometryMemoryDialog:
    """The dialog saves on every dismissal path and restores on first show."""

    def _dialog(self) -> GeometryMemoryDialog:
        return GeometryMemoryDialog(None, _KEY, _DEFAULT)

    def test_first_run_uses_default_size(self) -> None:
        store: dict = {}
        with (
            patch("src.window_geometry.get_setting", store.get),
            patch("src.window_geometry._persist_setting", store.__setitem__),
        ):
            dialog = self._dialog()
            dialog.show()
            assert (dialog.width(), dialog.height()) == _DEFAULT
            dialog.close()

    def test_reopens_at_size_it_was_closed_at(self) -> None:
        store: dict = {}
        with (
            patch("src.window_geometry.get_setting", store.get),
            patch("src.window_geometry._persist_setting", store.__setitem__),
        ):
            first = self._dialog()
            first.show()
            first.resize(640, 480)
            first.close()

            assert _KEY in store, "closing the dialog must persist its geometry"

            second = self._dialog()
            second.show()
            assert (second.width(), second.height()) == (640, 480)
            second.close()

    def test_reject_also_persists_geometry(self) -> None:
        # A RejectRole button calls reject() directly, which never sends a close
        # event -- yet WA_DeleteOnClose destroys the dialog just the same, so the
        # save has to happen on this path too or the size is lost for good.
        store: dict = {}
        with (
            patch("src.window_geometry.get_setting", store.get),
            patch("src.window_geometry._persist_setting", store.__setitem__),
        ):
            dialog = self._dialog()
            dialog.show()
            dialog.resize(640, 480)
            dialog.reject()

            assert _KEY in store, "rejecting the dialog must persist its geometry"

            second = self._dialog()
            second.show()
            assert (second.width(), second.height()) == (640, 480)
            second.close()

    def test_reshow_does_not_undo_a_resize(self) -> None:
        # Geometry is restored on the first show only; a hide/show cycle on a
        # still-open dialog must keep whatever size the user dragged it to.
        store: dict = {}
        with (
            patch("src.window_geometry.get_setting", store.get),
            patch("src.window_geometry._persist_setting", store.__setitem__),
        ):
            dialog = self._dialog()
            dialog.show()
            dialog.resize(640, 480)
            dialog.hide()
            dialog.show()
            assert (dialog.width(), dialog.height()) == (640, 480)
            dialog.close()

    def test_unwritable_store_does_not_block_closing(self) -> None:
        def _boom(_key: str, _value: object) -> None:
            raise OSError("AppData is read-only")

        with (
            patch("src.window_geometry.get_setting", return_value=None),
            patch("src.window_geometry._persist_setting", _boom),
        ):
            dialog = GeometryMemoryDialog(None, _KEY, _DEFAULT)
            dialog.show()
            dialog.close()
            assert dialog.isVisible() is False


class TestPodcastStatusWindowGeometry:
    """Regression: the Podcast Status window reopens at its last size."""

    @staticmethod
    def _stub_window(vd) -> QWidget:
        """Build the smallest real QWidget that MyWindow._show_podcast_status needs."""

        class _StubWindow(QWidget):
            _podcast_last_statuses = [
                {
                    "podcast": f"Show {i}",
                    "latest_date": "2026-01-01",
                    "status": "up to date",
                }
                for i in range(8)
            ]
            _podcast_status_dialog: QDialog | None = None
            _podcast_status_table = None

            _create_podcast_status_table = vd.MyWindow._create_podcast_status_table
            _on_podcast_status_context_menu = vd.MyWindow._on_podcast_status_context_menu
            _show_podcast_status = vd.MyWindow._show_podcast_status
            _on_podcast_status_dialog_destroyed = (
                vd.MyWindow._on_podcast_status_dialog_destroyed
            )

        return _StubWindow()

    def test_dialog_reopens_at_last_closed_size(self) -> None:
        from tests.test_cache_early_exit import import_vid_module

        vd = import_vid_module()
        store: dict = {}
        with (
            patch("src.window_geometry.get_setting", store.get),
            patch("src.window_geometry._persist_setting", store.__setitem__),
        ):
            window = self._stub_window(vd)
            window._show_podcast_status()
            first = window._podcast_status_dialog
            assert (first.width(), first.height()) == vd._PODCAST_STATUS_DEFAULT_SIZE
            first.resize(640, 480)
            first.close()

            window._podcast_status_dialog = None
            window._show_podcast_status()
            second = window._podcast_status_dialog
            assert (second.width(), second.height()) == (640, 480)
            second.close()


class TestSaveOpenDialogGeometry:
    """
    MyWindow._save_open_dialog_geometry -- the quit-with-dialog-open path.

    Qt tears a child dialog down without a close event when the app quits, so
    this method is the only thing that saves geometry on that path.  Driven
    against a bare stub (not a real MyWindow) per the handoff.
    """

    @staticmethod
    def _method() -> object:
        from tests.test_cache_early_exit import import_vid_module

        return import_vid_module().MyWindow._save_open_dialog_geometry

    @pytest.mark.parametrize("set_attr_to_none", [False, True])
    def test_no_or_none_dialog_is_a_noop(self, set_attr_to_none: bool) -> None:
        # getattr(self, "_podcast_status_dialog", None) takes the same early-return
        # branch whether the attribute was never set (before first open) or was
        # explicitly cleared to None (_on_podcast_status_dialog_destroyed) -- both
        # must be safe to call from closeEvent.
        method = self._method()

        class Stub:
            pass

        stub = Stub()
        if set_attr_to_none:
            stub._podcast_status_dialog = None
        method(stub)  # must not raise

    def test_hidden_dialog_is_not_saved(self) -> None:
        method = self._method()
        calls: list[bool] = []

        class FakeDialog:
            def isVisible(self) -> bool:
                return False

            def save_geometry(self) -> None:
                calls.append(True)

        class Stub:
            _podcast_status_dialog = FakeDialog()

        method(Stub())
        assert calls == []

    def test_visible_dialog_is_saved(self) -> None:
        method = self._method()
        calls: list[bool] = []

        class FakeDialog:
            def isVisible(self) -> bool:
                return True

            def save_geometry(self) -> None:
                calls.append(True)

        class Stub:
            _podcast_status_dialog = FakeDialog()

        method(Stub())
        assert calls == [True]

    def test_deleted_cpp_object_is_logged_not_raised(self) -> None:
        # A cached dialog reference whose underlying C++ object Qt already tore
        # down raises RuntimeError from isVisible(); the app must still be able
        # to finish closing.
        method = self._method()

        class FakeDialog:
            def isVisible(self) -> bool:
                raise RuntimeError("wrapped C/C++ object of type QDialog has been deleted")

        class Stub:
            _podcast_status_dialog = FakeDialog()

        method(Stub())  # must not raise


class TestGeometrySettingsPlumbing:
    """The geometry key round-trips through the runtime settings store."""

    def test_init_runtime_settings_exposes_previously_persisted_geometry(self) -> None:
        # PODCAST_STATUS_GEOMETRY_KEY must be registered in _init_runtime_settings so a
        # value a previous run wrote to the .env is visible to get_setting() even
        # though it is machine-written state, not a Settings-dialog field.
        blob = "AAAA/////wAAAAAAAAAAAAAA=="
        with mock.patch.dict(os.environ, {_config_mod.PODCAST_STATUS_GEOMETRY_KEY: blob}):
            importlib.reload(_config_mod)
            importlib.reload(_sd_mod)
            _sd_mod._init_runtime_settings()
            assert _sd_mod.get_setting(_config_mod.PODCAST_STATUS_GEOMETRY_KEY) == blob

    def test_persist_setting_round_trips_base64_padding_through_dotenv(
        self,
        tmp_path: Path,
    ) -> None:
        # The stored value is base64, which routinely ends in "=" padding right
        # next to the "key=value" separator this store writes; prove the padded
        # value survives a _persist_setting write and a dotenv-style re-read
        # unchanged, through a tmp_path-redirected store (never the real AppData
        # .env).
        blob = "QWxhZGRpbjpvcGVuIHNlc2FtZQ=="
        importlib.reload(_sd_mod)
        fake_env = tmp_path / ".env"
        with (
            mock.patch.object(_sd_mod, "_APPDATA_DIR", tmp_path),
            mock.patch.object(_sd_mod, "_USER_ENV", fake_env),
        ):
            _sd_mod._persist_setting(_config_mod.PODCAST_STATUS_GEOMETRY_KEY, blob)
            assert _sd_mod.get_setting(_config_mod.PODCAST_STATUS_GEOMETRY_KEY) == blob

        parsed = dotenv_values(fake_env)
        assert parsed[_config_mod.PODCAST_STATUS_GEOMETRY_KEY] == blob

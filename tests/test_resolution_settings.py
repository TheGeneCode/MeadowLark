"""
Boundary tests for the Resolutions settings tab and the registry-driven Playlists / Interface tabs.

Covers:
  - _init_runtime_settings() seeding every preset's playlist/drop/button keys
  - enabled_heights() parsing, defaulting, and non-string resilience
  - SettingsDialog Resolutions tab construction and pre-checked state
  - _apply() persistence of the enabled set, including the empty-selection guard
    and descending-order normalization
  - Playlists/Interface tabs listing every preset and marking disabled rows
  - HELP_TEXT coverage for every generated per-preset key
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QFormLayout, QTabWidget

import src.settings_dialog as sd
from src.resolutions import (
    RESOLUTION_PRESETS,
    button_label_key,
    drop_label_key,
    playlist_file_key,
)
from src.settings_dialog import HELP_TEXT, SettingsDialog, enabled_heights

# One QApplication for the entire module — Qt requires exactly one instance.
_app: QApplication = QApplication.instance() or QApplication([])


def _find_tab(dialog: SettingsDialog, title: str) -> QFormLayout:
    """Return the QFormLayout of the tab named *title*."""
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    for i in range(tabs.count()):
        if tabs.tabText(i) == title:
            form = tabs.widget(i).layout()
            assert isinstance(form, QFormLayout)
            return form
    msg = f"tab {title!r} not found"
    raise AssertionError(msg)


def _row_labels(form: QFormLayout) -> list[str]:
    """Return the text of every label widget in a QFormLayout."""
    labels: list[str] = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        if item is not None and item.widget() is not None:
            labels.append(item.widget().text())
    return labels


# ===========================================================================
# 1. Runtime store — every preset key seeded
# ===========================================================================


class TestRuntimeStoreSeeding:
    def test_runtime_store_has_every_preset_key(self) -> None:
        sd._init_runtime_settings()
        for p in RESOLUTION_PRESETS:
            assert isinstance(sd.get_setting(playlist_file_key(p.height)), str)
            assert sd.get_setting(playlist_file_key(p.height))
            assert isinstance(sd.get_setting(drop_label_key(p.height)), str)
            assert sd.get_setting(drop_label_key(p.height))
            assert isinstance(sd.get_setting(button_label_key(p.height)), str)
            assert sd.get_setting(button_label_key(p.height))

    def test_runtime_store_preserves_legacy_1080_path(self) -> None:
        sd._init_runtime_settings()
        value = str(sd.get_setting("VID_DL_PLAYLISTS_FILE"))
        assert value.endswith("playlists.txt")
        assert not value.endswith("1080playlists.txt")


# ===========================================================================
# 2. enabled_heights() — parsing, defaults, non-string resilience
# ===========================================================================


class TestEnabledHeights:
    def test_enabled_heights_defaults(self) -> None:
        with patch.dict(sd._runtime, {}, clear=True):
            assert enabled_heights() == (1080, 720)

    def test_enabled_heights_reads_persisted_string(self) -> None:
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": "2160,480"}):
            assert enabled_heights() == (2160, 480)

    def test_enabled_heights_survives_non_string(self) -> None:
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": None}):
            assert enabled_heights() == (1080, 720)
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": (1080,)}):
            assert enabled_heights() == (1080, 720)


# ===========================================================================
# 3. Resolutions tab — construction and pre-checked state
# ===========================================================================


class TestResolutionsTab:
    def test_resolutions_tab_has_one_check_per_preset(self) -> None:
        sd._init_runtime_settings()
        dialog = SettingsDialog()
        assert len(dialog._resolution_checks) == len(RESOLUTION_PRESETS)
        assert set(dialog._resolution_checks.keys()) == {p.height for p in RESOLUTION_PRESETS}

    def test_resolutions_tab_prechecks_enabled(self) -> None:
        sd._init_runtime_settings()
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": "1080,720"}):
            dialog = SettingsDialog()
        assert dialog._resolution_checks[1080].isChecked()
        assert dialog._resolution_checks[720].isChecked()
        for height in (2160, 1440, 480, 360):
            assert not dialog._resolution_checks[height].isChecked()


# ===========================================================================
# 4. _apply() — persisting the enabled set
# ===========================================================================


class TestApplyPersistsEnabledSet:
    def test_apply_persists_enabled_set(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            dialog._resolution_checks[2160].setChecked(True)
            dialog._resolution_checks[480].setChecked(True)
            dialog._resolution_checks[720].setChecked(False)
            received: dict[str, object] = {}
            dialog.settings_changed.connect(received.update)
            dialog._apply()
            content = fake_env.read_text(encoding="utf-8")

        assert "VID_DL_ENABLED_RESOLUTIONS=2160,1080,480\n" in content
        assert received.get("VID_DL_ENABLED_RESOLUTIONS") == "2160,1080,480"

    def test_apply_rejects_empty_selection(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            for box in dialog._resolution_checks.values():
                box.setChecked(False)
            original = sd.get_setting("VID_DL_ENABLED_RESOLUTIONS")
            received: dict[str, object] = {}
            dialog.settings_changed.connect(received.update)
            with patch("src.settings_dialog.QMessageBox.warning") as mock_warning:
                dialog._apply()
            mock_warning.assert_called_once()
            assert sd.get_setting("VID_DL_ENABLED_RESOLUTIONS") == original

        assert "VID_DL_ENABLED_RESOLUTIONS" not in received

    def test_apply_no_change_emits_nothing_for_resolutions(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            received: dict[str, object] = {}
            dialog.settings_changed.connect(received.update)
            dialog._apply()

        assert "VID_DL_ENABLED_RESOLUTIONS" not in received

    def test_enabled_set_persists_descending(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            dialog._resolution_checks[360].setChecked(True)
            dialog._resolution_checks[2160].setChecked(True)
            dialog._apply()
            content = fake_env.read_text(encoding="utf-8")

        assert "VID_DL_ENABLED_RESOLUTIONS=2160,1080,720,360\n" in content


# ===========================================================================
# 5. Playlists / Interface tabs — registry-driven
# ===========================================================================


class TestPlaylistsTab:
    def test_playlists_tab_lists_every_preset(self) -> None:
        sd._init_runtime_settings()
        dialog = SettingsDialog()
        for p in RESOLUTION_PRESETS:
            assert playlist_file_key(p.height) in dialog._edits
        assert "VID_DL_PLAYLISTS_AUDIO_FILE" in dialog._edits

    def test_playlists_tab_marks_disabled_rows(self) -> None:
        sd._init_runtime_settings()
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": "1080,720"}):
            dialog = SettingsDialog()
            labels = _row_labels(_find_tab(dialog, "Playlists"))

        for p in RESOLUTION_PRESETS:
            matching = [lbl for lbl in labels if f"({p.label}p)" in lbl]
            assert matching, f"no Playlists row found for {p.label}p"
            is_hidden = "(hidden)" in matching[0]
            assert is_hidden == (p.height not in (1080, 720)), (
                f"{p.label}p row hidden={is_hidden}, expected {p.height not in (1080, 720)}"
            )


class TestInterfaceTab:
    def test_interface_tab_lists_every_preset_label(self) -> None:
        sd._init_runtime_settings()
        dialog = SettingsDialog()
        for p in RESOLUTION_PRESETS:
            assert drop_label_key(p.height) in dialog._edits
            assert button_label_key(p.height) in dialog._edits
        assert "VID_DL_LABEL_DROP_AUDIO" in dialog._edits
        assert "VID_DL_LABEL_READY_TEXT" in dialog._edits
        assert "VID_DL_LABEL_BTN_PODCASTS" in dialog._edits

    def test_interface_tab_marks_disabled_rows(self) -> None:
        """
        Mirrors test_playlists_tab_marks_disabled_rows for the Interface tab.

        The handoff flagged the Playlists tab's (hidden) suffix as covered but did
        not call out Interface, which computes its own `current = enabled_heights()`
        independently and could silently regress (e.g. captured before
        _init_runtime_settings() ran) without this failing.
        """
        sd._init_runtime_settings()
        with patch.dict(sd._runtime, {"VID_DL_ENABLED_RESOLUTIONS": "1080,720"}):
            dialog = SettingsDialog()
            labels = _row_labels(_find_tab(dialog, "Interface"))

        for p in RESOLUTION_PRESETS:
            expected_hidden = p.height not in (1080, 720)

            drop_matches = [lbl for lbl in labels if lbl.startswith(f"Drop label — {p.label}:")]
            assert drop_matches, f"no Interface drop-label row found for {p.label}"
            assert ("(hidden)" in drop_matches[0]) == expected_hidden, (
                f"{p.label} drop row hidden={('(hidden)' in drop_matches[0])}, "
                f"expected {expected_hidden}"
            )

            button_matches = [
                lbl for lbl in labels if lbl.startswith(f"Button — {p.label} Playlists:")
            ]
            assert button_matches, f"no Interface button row found for {p.label}"
            assert ("(hidden)" in button_matches[0]) == expected_hidden, (
                f"{p.label} button row hidden={('(hidden)' in button_matches[0])}, "
                f"expected {expected_hidden}"
            )


# ===========================================================================
# 6. HELP_TEXT coverage
# ===========================================================================


class TestHelpTextCoverage:
    def test_help_text_covers_every_preset_key(self) -> None:
        for p in RESOLUTION_PRESETS:
            for key in (
                drop_label_key(p.height),
                button_label_key(p.height),
                playlist_file_key(p.height),
            ):
                assert key in HELP_TEXT
                assert HELP_TEXT[key]

    def test_help_text_covers_enabled_resolutions_key(self) -> None:
        assert HELP_TEXT.get("VID_DL_ENABLED_RESOLUTIONS")


# ===========================================================================
# 7. _apply() guard — dialog built without the Resolutions tab wired up
# ===========================================================================


class TestApplyGuardWithoutResolutionsTab:
    def test_apply_skips_resolution_block_when_no_checks_present(self, tmp_path: Path) -> None:
        """
        Empty _resolution_checks must make the whole resolution block a no-op.

        Highest-risk area per the handoff: `if self._resolution_checks and not
        checked` must be a no-op — no warning, no persist — when a caller builds
        the dialog without the Resolutions tab (empty dict is falsy, same code path
        as zero registered presets). Calls the unbound method against a minimal
        stand-in so the guard is tested independent of SettingsDialog.__init__
        always wiring the tab today.
        """
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
            patch("src.settings_dialog.QMessageBox.warning") as mock_warning,
        ):
            original = sd.get_setting("VID_DL_ENABLED_RESOLUTIONS")
            emitted: list[dict[str, object]] = []
            stub = SimpleNamespace(
                _resolution_checks={},
                _edits={},
                settings_changed=SimpleNamespace(emit=emitted.append),
            )
            SettingsDialog._apply(stub)

            mock_warning.assert_not_called()
            assert sd.get_setting("VID_DL_ENABLED_RESOLUTIONS") == original

        assert not fake_env.exists() or (
            "VID_DL_ENABLED_RESOLUTIONS" not in fake_env.read_text(encoding="utf-8")
        )
        assert emitted == []


# ===========================================================================
# 8. _apply() — resolution change combined with an unrelated edit in one call
# ===========================================================================


class TestApplyCombinedChanges:
    def test_apply_combines_resolution_and_edit_changes(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            dialog._resolution_checks[2160].setChecked(True)
            dialog._edits["VID_DL_LABEL_READY_TEXT"].setText("New Ready Text")
            received: dict[str, object] = {}
            dialog.settings_changed.connect(received.update)
            dialog._apply()
            content = fake_env.read_text(encoding="utf-8")

        assert received.get("VID_DL_ENABLED_RESOLUTIONS") == "2160,1080,720"
        assert received.get("VID_DL_LABEL_READY_TEXT") == "New Ready Text"
        assert "VID_DL_ENABLED_RESOLUTIONS=2160,1080,720\n" in content
        assert "VID_DL_LABEL_READY_TEXT=New Ready Text\n" in content


# ===========================================================================
# 9. _persist_setting() — replaces an existing .env line rather than duplicating
# ===========================================================================


class TestPersistReplacesExistingLine:
    def test_apply_replaces_existing_enabled_resolutions_line(self, tmp_path: Path) -> None:
        sd._init_runtime_settings()
        fake_env = tmp_path / ".env"
        fake_env.write_text(
            "VID_DL_ENABLED_RESOLUTIONS=1080,720\nVID_DL_VIDEO_FORMAT=mp4\n",
            encoding="utf-8",
        )
        with (
            patch.dict(sd._runtime),
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            dialog = SettingsDialog()
            dialog._resolution_checks[480].setChecked(True)
            dialog._apply()
            content = fake_env.read_text(encoding="utf-8")

        assert content.count("VID_DL_ENABLED_RESOLUTIONS=") == 1
        assert "VID_DL_ENABLED_RESOLUTIONS=1080,720,480\n" in content
        assert "VID_DL_VIDEO_FORMAT=mp4\n" in content

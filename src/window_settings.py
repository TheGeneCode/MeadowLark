"""Main-window settings dialog wiring and yt-dlp / app update checks."""

# Bodies were moved verbatim from meadowlark.pyw, which ruff never linted (.pyw is outside its
# default include), so their long lines are exempted here rather than rewrapped in the move.

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import UTC, date, datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QMessageBox

from src.resolutions import (
    RESOLUTION_PRESETS,
    button_label_key,
    drop_label_key,
    playlist_file_key,
)
from src.settings_dialog import SettingsDialog, _persist_setting, get_setting
from src.window_base import WindowState

logger = logging.getLogger(__name__)


class SettingsUpdatesMixin(WindowState):
    """Settings dialog wiring and update checks for the main window."""

    def _open_settings(self) -> None:
        """Open (or raise) the Settings dialog."""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self)
            self._settings_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self._settings_dialog.settings_changed.connect(self.reload_settings)
            # destroyed, not finished: finished only clears the reference, which would
            # leave the closed dialog alive as a hidden child of this window.
            self._settings_dialog.destroyed.connect(lambda: setattr(self, "_settings_dialog", None))
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def reload_settings(self, changes: dict) -> None:
        """Apply live setting changes emitted by the Settings dialog."""
        resolution_keys = {"VID_DL_ENABLED_RESOLUTIONS"}
        for preset in RESOLUTION_PRESETS:
            resolution_keys.add(drop_label_key(preset.height))
            resolution_keys.add(button_label_key(preset.height))
            resolution_keys.add(playlist_file_key(preset.height))
        if resolution_keys & changes.keys():
            # A label, a playlist path, or the enabled set moved. Rebuilding the
            # whole grid is cheaper to reason about than patching individual
            # widgets, and it is the only path that handles a rung appearing or
            # disappearing. This also recreates self.labelAudio and
            # self.buttonAudioPlaylists, so the audio handlers below must run
            # after this, not before, or they would style widgets about to be
            # deleted.
            self._populate_resolution_grid()
        self._apply_label_changes(changes)
        self._apply_path_changes(changes)
        podcast_keys = {
            "VID_DL_PODCAST_AUTO_CHECK",
            "VID_DL_PODCAST_CHECK_INTERVAL_MINUTES",
        }
        if podcast_keys & changes.keys():
            self._restart_podcast_timer()
        if "VID_DL_ALWAYS_ON_TOP" in changes:
            self._apply_always_on_top(changes["VID_DL_ALWAYS_ON_TOP"])

    def _apply_always_on_top(self, enabled: bool) -> None:
        # Not setWindowFlags(): on a shown window it goes through setParent(), which hides the
        # window and recreates its native handle. This runs from Settings -> Apply, so the
        # Settings dialog (an owned window of this one) is open, and recreating its owner left
        # the main window unexposed -- never repainted, all white once Settings closed.
        # overrideWindowFlags() updates only the widget's record (used if the native window is
        # ever created again); QWindow.setFlag() changes the live window in place.
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.overrideWindowFlags(flags)
        handle = self.windowHandle()
        if handle is not None:
            handle.setFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)

    def _apply_label_changes(self, changes: dict) -> None:
        """Update widget text from settings changes."""
        if "VID_DL_LABEL_DROP_AUDIO" in changes:
            self.labelAudio.setText(changes["VID_DL_LABEL_DROP_AUDIO"])
            self.labelAudio.originalText = changes["VID_DL_LABEL_DROP_AUDIO"]
        if "VID_DL_LABEL_READY_TEXT" in changes:
            self._ready_text = changes["VID_DL_LABEL_READY_TEXT"]
            if self.labelOutput.text() != "Checking podcasts for SponsorBlock info...":
                self.labelOutput.setText(self._ready_text)
        if "VID_DL_LABEL_BTN_PODCASTS" in changes:
            self.buttonAudioPlaylists.setText(changes["VID_DL_LABEL_BTN_PODCASTS"])

    def _apply_path_changes(self, changes: dict) -> None:
        """Update playlist paths from settings changes."""
        if "VID_DL_PLAYLISTS_AUDIO_FILE" in changes:
            self.buttonAudioPlaylists.playlist_path = Path(changes["VID_DL_PLAYLISTS_AUDIO_FILE"])

    def _start_app_update_check(self, auto: bool = False) -> None:
        """Start a background check for a newer app version."""
        self._app_update_worker = self._AppUpdateWorker()
        self._app_update_thread = QThread(self)
        self._app_update_worker.moveToThread(self._app_update_thread)
        self._app_update_thread.started.connect(self._app_update_worker.run)
        self._app_update_worker.finished.connect(
            lambda avail, tag, url: self._on_app_update_result(avail, tag, url, auto=auto)
        )
        self._app_update_worker.finished.connect(self._app_update_thread.quit)
        self._app_update_thread.start()

    def _maybe_start_auto_app_update_check(self) -> None:
        """Fire a background app-update check at most once per week if the setting is on."""
        if not get_setting("VID_DL_APP_UPDATE_AUTO_CHECK"):
            logger.info("Auto app update check disabled by setting")
            return
        last_checked = get_setting("VID_DL_APP_UPDATE_LAST_CHECKED")
        if last_checked:
            try:
                last_dt = date.fromisoformat(str(last_checked))
                if (datetime.now(tz=UTC).date() - last_dt).days < 7:
                    logger.info("Auto app update check skipped: last checked %s", last_checked)
                    return
            except ValueError:
                pass
        logger.info("Starting automatic app update check")
        self._start_app_update_check(auto=True)

    def _on_app_update_result(
        self,
        update_available: bool,
        latest_tag: str,
        download_url: str,
        *,
        auto: bool = False,
    ) -> None:
        """Handle the result of the background app update check."""
        if auto:
            _persist_setting(
                "VID_DL_APP_UPDATE_LAST_CHECKED",
                datetime.now(tz=UTC).date().isoformat(),
            )
        if not update_available:
            if not auto:
                QMessageBox.information(
                    self,
                    "No Update Available",
                    "You are running the latest version.",
                )
            else:
                logger.info("Auto app update check: already on latest version")
            return
        answer = QMessageBox.question(
            self,
            "Update Available",
            f"Version {latest_tag} is available. Download now?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            webbrowser.open(download_url)

    def do_updates(self) -> None:
        """
        Update YT_DLP at start.

        Upgrade yt-dlp to the latest version using uv, open the changelog in the default browser,
        and restart the application if the update is successful. Updates the UI to indicate failure
        if the installed version does not match the latest version after the update attempt.
        """
        uv_path = shutil.which("uv")
        if uv_path is None:
            self.buttonUpdate.setStyleSheet("color: red;")
            return

        # 1. Update the lockfile to the latest yt-dlp
        lock_process = subprocess.run(  # noqa: S603
            [uv_path, "lock", "--upgrade-package", "yt-dlp"],
            check=False,
        )
        # 2. Sync the environment to actually install the new yt-dlp, using the updated lockfile
        sync_process = subprocess.run(  # noqa: S603
            [uv_path, "sync"],
            check=False,
        )

        if lock_process.returncode == 0 and sync_process.returncode == 0:
            webbrowser.open("https://github.com/yt-dlp/yt-dlp/blob/master/Changelog.md")
            # Update successful, restart app
            python = sys.executable
            script = os.path.realpath(sys.argv[0])
            subprocess.Popen([python, script, *sys.argv[1:]])  # noqa: S603
            sys.exit()
        else:
            # Update failed or no version change
            self.buttonUpdate.setStyleSheet("color: red;")

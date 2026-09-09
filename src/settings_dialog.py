"""Settings dialog for MeadowLark — runtime-mutable configuration backed by AppData .env."""

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import PyQt6.QtCore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    ALWAYS_ON_TOP,
    APP_UPDATE_AUTO_CHECK,
    APP_UPDATE_LAST_CHECKED,
    COOKIES_FILE,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_VIDEO_FORMAT,
    ENABLED_RESOLUTIONS,
    LABEL_BTN_PODCASTS,
    LABEL_DROP_AUDIO,
    LABEL_READY_TEXT,
    MARK_WATCHED,
    PLAYLISTS_AUDIO_FILE,
    PODCAST_AUTO_CHECK,
    PODCAST_CHECK_INTERVAL_MINUTES,
    PODCAST_MISC_OUTPUT_DIR,
    PODCAST_STATUS_GEOMETRY,
    PODCAST_STATUS_GEOMETRY_KEY,
    VIDEO_STORAGE_DIR,
    button_label_for_height,
    drop_label_for_height,
    playlist_path_for_height,
)
from .resolutions import (
    RESOLUTION_PRESETS,
    button_label_key,
    drop_label_key,
    format_enabled_heights,
    parse_enabled_heights,
    playlist_file_key,
)
from .version_utils import (
    APP_VERSION,
    GITHUB_REPO_URL,
    get_current_yt_dlp_version,
    get_publish_date,
    is_app_update_available,
)

# ============================================================================
# Help text for every setting key
# ============================================================================

HELP_TEXT: dict[str, str] = {
    "VID_DL_VIDEO_STORAGE_DIR": (
        "Directory where downloaded videos are saved.\n"
        "Changes take effect immediately for new downloads."
    ),
    "VID_DL_PODCAST_MISC_OUTPUT_DIR": (
        "Directory where podcast audio files (m4a) are saved.\n"
        "Changes take effect immediately for new downloads."
    ),
    "VID_DL_ENABLED_RESOLUTIONS": (
        "Which resolution presets appear as drop targets and playlist buttons.\n\n"
        "Each preset downloads the best available quality at or below its height, so a\n"
        "video that only exists at a lower resolution still downloads — it is not\n"
        "skipped. If a resolution is blocked by YouTube, the app automatically retries\n"
        "at the next lower preset you have enabled.\n\n"
        "At least one must stay checked."
    ),
    "VID_DL_PLAYLISTS_AUDIO_FILE": (
        "Playlist file for podcast/audio downloads.\n"
        "The file is copied into AppData so the original can be moved or deleted.\n"
        "Each line should be a YouTube playlist URL, optionally preceded by a #Comment line."
    ),
    "VID_DL_COOKIES_FILE": (
        "Path to a cookies.txt file exported from your browser.\n"
        "Used by yt-dlp for authenticated downloads (e.g. age-restricted videos).\n"
        "The file is NOT copied — it is referenced in place so browser extensions can keep it updated."
    ),
    "VID_DL_LABEL_DROP_AUDIO": (
        "Display text for the audio/podcast drop target.\n"
        "Routing behaviour is unchanged regardless of display text."
    ),
    "VID_DL_LABEL_READY_TEXT": (
        "Text shown in the status bar when the app is idle and ready for new downloads."
    ),
    "VID_DL_LABEL_BTN_PODCASTS": "Label for the YT Podcasts button.",
    "VID_DL_PODCAST_AUTO_CHECK": (
        "When enabled, the app automatically checks your podcast playlists on the schedule below.\n"
        "Disable to run checks manually only."
    ),
    "VID_DL_PODCAST_CHECK_INTERVAL_MINUTES": (
        "How often (in minutes) the app automatically checks podcast playlists.\n"
        "Range: 5-1440 minutes (5 min to 24 hours)."
    ),
    "VID_DL_VIDEO_FORMAT": (
        "Container format for downloaded videos.\n"
        ".mp4 works on the widest range of devices and players."
    ),
    "VID_DL_AUDIO_FORMAT": (
        "Format for downloaded audio and podcast files.\n"
        ".m4a gives the best quality-to-size ratio for most listeners."
    ),
    "VID_DL_ALWAYS_ON_TOP": (
        "Keep the app window above all other windows.\n"
        "Changes take effect immediately."
    ),
    "VID_DL_APP_UPDATE_AUTO_CHECK": (
        "When enabled, the app silently checks for a newer release once per week at startup.\n"
        "If a new version is found you are prompted to download it. Disable to opt out."
    ),
    "VID_DL_MARK_WATCHED": (
        "When enabled, downloaded YouTube videos are automatically marked as watched\n"
        "in your YouTube account.\n\n"
        "Requires a cookies.txt file with an active YouTube login session.\n"
        "Configure your cookies.txt path in the Playlists tab."
    ),
}

for _preset in RESOLUTION_PRESETS:
    HELP_TEXT[drop_label_key(_preset.height)] = (
        f"Display text for the {_preset.label} drop target ({_preset.description}).\n"
        "Routing behaviour is unchanged regardless of display text."
    )
    HELP_TEXT[button_label_key(_preset.height)] = (
        f"Label for the {_preset.label} Playlists button."
    )
    HELP_TEXT[playlist_file_key(_preset.height)] = (
        f"Playlist file for {_preset.label} video downloads ({_preset.description}).\n"
        "The file is copied into AppData so the original can be moved or deleted.\n"
        "Each line should be a YouTube playlist URL, optionally preceded by a #Comment line."
    )

# ============================================================================
# AppData path (mirrors QYT.py and first_run_wizard.py)
# ============================================================================

_APPDATA_DIR: Path = Path.home() / "AppData" / "Roaming" / "MeadowLark"
_USER_ENV: Path = _APPDATA_DIR / ".env"
_PLAYLISTS_APPDATA_DIR: Path = _APPDATA_DIR / "playlists"

# ============================================================================
# Runtime settings store
# ============================================================================

_runtime: dict[str, Any] = {}


def _init_runtime_settings() -> None:
    """Populate the runtime store from frozen config constants.  Call once at startup."""
    _runtime.update(
        {
            "VID_DL_VIDEO_STORAGE_DIR": str(VIDEO_STORAGE_DIR),
            "VID_DL_PODCAST_MISC_OUTPUT_DIR": str(PODCAST_MISC_OUTPUT_DIR),
            "VID_DL_PLAYLISTS_AUDIO_FILE": str(PLAYLISTS_AUDIO_FILE),
            "VID_DL_COOKIES_FILE": str(COOKIES_FILE),
            "VID_DL_LABEL_DROP_AUDIO": LABEL_DROP_AUDIO,
            "VID_DL_LABEL_READY_TEXT": LABEL_READY_TEXT,
            "VID_DL_LABEL_BTN_PODCASTS": LABEL_BTN_PODCASTS,
            "VID_DL_PODCAST_AUTO_CHECK": PODCAST_AUTO_CHECK,
            "VID_DL_PODCAST_CHECK_INTERVAL_MINUTES": PODCAST_CHECK_INTERVAL_MINUTES,
            "VID_DL_VIDEO_FORMAT": DEFAULT_VIDEO_FORMAT,
            "VID_DL_AUDIO_FORMAT": DEFAULT_AUDIO_FORMAT,
            "VID_DL_ALWAYS_ON_TOP": ALWAYS_ON_TOP,
            "VID_DL_APP_UPDATE_AUTO_CHECK": APP_UPDATE_AUTO_CHECK,
            "VID_DL_APP_UPDATE_LAST_CHECKED": APP_UPDATE_LAST_CHECKED,
            "VID_DL_MARK_WATCHED": MARK_WATCHED,
            # Machine-written UI state, not exposed in the Settings dialog; it is
            # registered here so a geometry saved on a previous run is visible to
            # get_setting() and not just sitting unread in the AppData .env.
            PODCAST_STATUS_GEOMETRY_KEY: PODCAST_STATUS_GEOMETRY,
        }
    )
    # Enabled set is stored as its string form (not a tuple) so it matches what
    # _persist_setting writes and what _apply's `new_val != get_setting(key)`
    # comparison expects — a tuple here would make every Apply see a spurious change.
    _runtime["VID_DL_ENABLED_RESOLUTIONS"] = format_enabled_heights(ENABLED_RESOLUTIONS)
    for preset in RESOLUTION_PRESETS:
        h = preset.height
        _runtime[playlist_file_key(h)] = str(playlist_path_for_height(h))
        _runtime[drop_label_key(h)] = drop_label_for_height(h)
        _runtime[button_label_key(h)] = button_label_for_height(h)


def get_setting(key: str) -> object:
    """Return the current runtime value for *key*, or None if not registered."""
    return _runtime.get(key)


def enabled_heights() -> tuple[int, ...]:
    """Return the currently enabled resolution rungs, highest first."""
    raw = get_setting("VID_DL_ENABLED_RESOLUTIONS")
    return parse_enabled_heights(raw if isinstance(raw, str) else None)


def _persist_setting(key: str, value: object) -> None:
    """Write *key=value* to the AppData .env and update the in-memory store."""
    _APPDATA_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing lines, replace the matching key, or append if absent.
    lines: list[str] = []
    if _USER_ENV.exists():
        lines = _USER_ENV.read_text(encoding="utf-8").splitlines(keepends=True)

    str_value = str(value) if not isinstance(value, bool) else str(value).lower()
    key_prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(key_prefix):
            lines[i] = f"{key}={str_value}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={str_value}\n")

    _USER_ENV.write_text("".join(lines), encoding="utf-8")
    _runtime[key] = value


def _import_playlist_file(source_path: str, dest_name: str) -> str:
    """Copy *source_path* to the AppData playlists dir as *dest_name* and return the new path."""
    _PLAYLISTS_APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = _PLAYLISTS_APPDATA_DIR / dest_name
    shutil.copy2(source_path, dest)
    return str(dest)


# ============================================================================
# Widget helpers
# ============================================================================


def _make_help_button(key: str, parent: QWidget) -> QPushButton:
    btn = QPushButton("?", parent)
    btn.setFixedWidth(24)
    btn.setFlat(True)
    text = HELP_TEXT.get(key, "No help available.")
    btn.clicked.connect(lambda: QMessageBox.information(parent, "Help", text))
    return btn


def _make_dir_row(
    label: str, key: str, parent: QWidget
) -> tuple[QHBoxLayout, QLineEdit]:
    edit = QLineEdit(str(get_setting(key) or ""), parent)
    browse = QPushButton("Browse…", parent)
    help_btn = _make_help_button(key, parent)

    def _browse() -> None:
        chosen = QFileDialog.getExistingDirectory(parent, label, edit.text())
        if chosen:
            edit.setText(chosen)

    browse.clicked.connect(_browse)
    row = QHBoxLayout()
    row.addWidget(edit)
    row.addWidget(browse)
    row.addWidget(help_btn)
    return row, edit


def _make_file_row(
    label: str,
    key: str,
    parent: QWidget,
    filter_str: str = "All Files (*)",
    copy_to_appdata: bool = False,
    dest_name: str = "",
) -> tuple[QHBoxLayout, QLineEdit]:
    edit = QLineEdit(str(get_setting(key) or ""), parent)
    browse = QPushButton("Browse…", parent)
    help_btn = _make_help_button(key, parent)

    def _browse() -> None:
        chosen, _ = QFileDialog.getOpenFileName(parent, label, edit.text(), filter_str)
        if not chosen:
            return
        if copy_to_appdata and dest_name:
            chosen = _import_playlist_file(chosen, dest_name)
        edit.setText(chosen)

    browse.clicked.connect(_browse)
    row = QHBoxLayout()
    row.addWidget(edit)
    row.addWidget(browse)
    row.addWidget(help_btn)
    return row, edit


# ============================================================================
# Format options
# ============================================================================

VIDEO_FORMAT_OPTIONS: list[tuple[str, str]] = [
    ("mp4", "plays everywhere, best compatibility"),
    ("mkv", "flexible container, keeps all tracks"),
    ("webm", "smaller files, optimized for the web"),
]

AUDIO_FORMAT_OPTIONS: list[tuple[str, str]] = [
    ("m4a", "great quality, works on Apple devices"),
    ("mp3", "universal, plays on everything"),
    ("opus", "best quality at smallest file size"),
    ("flac", "lossless, perfect quality, large files"),
    ("wav", "uncompressed, maximum quality, very large"),
]


def _make_combo_row(
    key: str, options: list[tuple[str, str]], parent: QWidget
) -> tuple[QHBoxLayout, QComboBox]:
    combo = QComboBox(parent)
    current = str(get_setting(key) or "")
    for value, description in options:
        combo.addItem(f".{value} — {description}", userData=value)
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    help_btn = _make_help_button(key, parent)
    row = QHBoxLayout()
    row.addWidget(combo)
    row.addWidget(help_btn)
    return row, combo


def _get_windows_release() -> str:
    if platform.system() == "Windows" and sys.getwindowsversion().build >= 22000:
        return "11"
    return platform.release()


# ============================================================================
# Dialog
# ============================================================================


class SettingsDialog(QDialog):
    """Non-modal settings dialog.  Emits settings_changed with {env_var: new_value} on Apply."""

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        self._edits: dict[str, QLineEdit | QCheckBox | QSpinBox] = {}
        self._resolution_checks: dict[int, QCheckBox] = {}

        tabs = QTabWidget(self)
        tabs.addTab(self._build_resolutions_tab(), "Resolutions")
        tabs.addTab(self._build_downloads_tab(), "Downloads")
        tabs.addTab(self._build_playlists_tab(), "Playlists")
        tabs.addTab(self._build_interface_tab(), "Interface")
        tabs.addTab(self._build_automation_tab(), "Automation")
        tabs.addTab(self._build_about_tab(), "About")

        buttons = QDialogButtonBox(self)
        apply_btn = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.ApplyRole)
        close_btn = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        apply_btn.clicked.connect(self._apply)
        close_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_resolutions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "Choose which resolutions appear in the main window. Each one gets its "
            "own drop target, its own playlist file, and its own colour.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        current = enabled_heights()
        form = QFormLayout()
        for preset in RESOLUTION_PRESETS:
            check = QCheckBox(preset.description, self)
            check.setChecked(preset.height in current)
            self._resolution_checks[preset.height] = check
            form.addRow(QLabel(f"{preset.label}p:"), check)
        layout.addLayout(form)

        help_row = QHBoxLayout()
        help_row.addWidget(QLabel("About resolution presets:", self))
        help_row.addWidget(_make_help_button("VID_DL_ENABLED_RESOLUTIONS", self))
        help_row.addStretch()
        layout.addLayout(help_row)
        layout.addStretch()

        tab.setLayout(layout)
        return tab

    def _build_downloads_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        row, edit = _make_dir_row("Video Directory", "VID_DL_VIDEO_STORAGE_DIR", self)
        self._edits["VID_DL_VIDEO_STORAGE_DIR"] = edit
        form.addRow(QLabel("Video directory:"), _wrap(row))

        row, edit = _make_dir_row(
            "Audio/Podcast Directory", "VID_DL_PODCAST_MISC_OUTPUT_DIR", self
        )
        self._edits["VID_DL_PODCAST_MISC_OUTPUT_DIR"] = edit
        form.addRow(QLabel("Audio directory:"), _wrap(row))

        row, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, self)
        self._edits["VID_DL_VIDEO_FORMAT"] = combo
        form.addRow(QLabel("Video format:"), _wrap(row))

        row, combo = _make_combo_row("VID_DL_AUDIO_FORMAT", AUDIO_FORMAT_OPTIONS, self)
        self._edits["VID_DL_AUDIO_FORMAT"] = combo
        form.addRow(QLabel("Audio format:"), _wrap(row))

        mw_check = QCheckBox(self)
        mw_check.setChecked(bool(get_setting("VID_DL_MARK_WATCHED")))
        help_mw = _make_help_button("VID_DL_MARK_WATCHED", self)
        mw_row = QHBoxLayout()
        mw_row.addWidget(mw_check)
        mw_row.addWidget(help_mw)
        mw_row.addStretch()
        self._edits["VID_DL_MARK_WATCHED"] = mw_check
        form.addRow(QLabel("Mark watched on YouTube:"), _wrap(mw_row))

        tab.setLayout(form)
        return tab

    def _build_playlists_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        current = enabled_heights()
        specs: list[tuple[str, str, str]] = [
            (
                f"Playlists file ({p.label}p):"
                + ("" if p.height in current else "  (hidden)"),
                playlist_file_key(p.height),
                p.playlist_filename,
            )
            for p in RESOLUTION_PRESETS
        ]
        specs.append(
            ("Playlists file (audio):", "VID_DL_PLAYLISTS_AUDIO_FILE", "audio playlists.txt")
        )
        for lbl, key, dest in specs:
            row, edit = _make_file_row(
                lbl,
                key,
                self,
                filter_str="Text Files (*.txt);;All Files (*)",
                copy_to_appdata=True,
                dest_name=dest,
            )
            self._edits[key] = edit
            form.addRow(QLabel(lbl), _wrap(row))

        row, edit = _make_file_row(
            "Cookies file",
            "VID_DL_COOKIES_FILE",
            self,
            filter_str="Text Files (*.txt);;All Files (*)",
            copy_to_appdata=False,
        )
        self._edits["VID_DL_COOKIES_FILE"] = edit
        form.addRow(QLabel("Cookies.txt:"), _wrap(row))

        tab.setLayout(form)
        return tab

    def _build_interface_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        current = enabled_heights()
        text_fields: list[tuple[str, str]] = []
        for p in RESOLUTION_PRESETS:
            suffix = "" if p.height in current else "  (hidden)"
            text_fields.append((f"Drop label — {p.label}:{suffix}", drop_label_key(p.height)))
        text_fields.append(("Drop label — audio:", "VID_DL_LABEL_DROP_AUDIO"))
        text_fields.append(("Ready text:", "VID_DL_LABEL_READY_TEXT"))
        for p in RESOLUTION_PRESETS:
            suffix = "" if p.height in current else "  (hidden)"
            text_fields.append(
                (f"Button — {p.label} Playlists:{suffix}", button_label_key(p.height))
            )
        text_fields.append(("Button — YT Podcasts:", "VID_DL_LABEL_BTN_PODCASTS"))
        for lbl, key in text_fields:
            edit = QLineEdit(str(get_setting(key) or ""), self)
            help_btn = _make_help_button(key, self)
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(help_btn)
            self._edits[key] = edit
            form.addRow(QLabel(lbl), _wrap(row))

        aot_check = QCheckBox(self)
        aot_check.setChecked(bool(get_setting("VID_DL_ALWAYS_ON_TOP")))
        help_aot = _make_help_button("VID_DL_ALWAYS_ON_TOP", self)
        aot_row = QHBoxLayout()
        aot_row.addWidget(aot_check)
        aot_row.addWidget(help_aot)
        aot_row.addStretch()
        self._edits["VID_DL_ALWAYS_ON_TOP"] = aot_check
        form.addRow(QLabel("Always on top:"), _wrap(aot_row))

        auto_update_check = QCheckBox(self)
        auto_update_check.setChecked(bool(get_setting("VID_DL_APP_UPDATE_AUTO_CHECK")))
        help_update = _make_help_button("VID_DL_APP_UPDATE_AUTO_CHECK", self)
        update_row = QHBoxLayout()
        update_row.addWidget(auto_update_check)
        update_row.addWidget(help_update)
        update_row.addStretch()
        self._edits["VID_DL_APP_UPDATE_AUTO_CHECK"] = auto_update_check
        form.addRow(QLabel("Auto-check for app updates:"), _wrap(update_row))

        tab.setLayout(form)
        return tab

    def _build_automation_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        auto_check = QCheckBox(self)
        auto_check.setChecked(bool(get_setting("VID_DL_PODCAST_AUTO_CHECK")))
        help_auto = _make_help_button("VID_DL_PODCAST_AUTO_CHECK", self)
        auto_row = QHBoxLayout()
        auto_row.addWidget(auto_check)
        auto_row.addWidget(help_auto)
        auto_row.addStretch()
        self._edits["VID_DL_PODCAST_AUTO_CHECK"] = auto_check
        form.addRow(QLabel("Auto-check podcasts:"), _wrap(auto_row))

        interval = QSpinBox(self)
        interval.setRange(5, 1440)
        interval.setSuffix(" min")
        interval.setValue(
            int(get_setting("VID_DL_PODCAST_CHECK_INTERVAL_MINUTES") or 60)
        )
        interval.setEnabled(auto_check.isChecked())
        help_interval = _make_help_button("VID_DL_PODCAST_CHECK_INTERVAL_MINUTES", self)
        interval_row = QHBoxLayout()
        interval_row.addWidget(interval)
        interval_row.addWidget(help_interval)
        interval_row.addStretch()
        self._edits["VID_DL_PODCAST_CHECK_INTERVAL_MINUTES"] = interval
        form.addRow(QLabel("Check interval:"), _wrap(interval_row))

        auto_check.toggled.connect(interval.setEnabled)

        tab.setLayout(form)
        return tab

    def _build_about_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        # Version info
        version_value = QLabel(APP_VERSION)
        version_value.setStyleSheet("font-weight: bold;")
        form.addRow(QLabel("Version:"), version_value)

        # Publish date
        publish_date = get_publish_date() or "Unknown"
        date_value = QLabel(publish_date)
        form.addRow(QLabel("Published:"), date_value)

        # GitHub link
        link_label = QLabel(f'<a href="{GITHUB_REPO_URL}">{GITHUB_REPO_URL}</a>')
        link_label.setOpenExternalLinks(True)
        form.addRow(QLabel("Repository:"), link_label)

        # Separator
        form.addRow("", QLabel())

        # Python version
        py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        form.addRow(QLabel("Python:"), QLabel(py_version))

        # yt-dlp version
        ytdlp_version = get_current_yt_dlp_version() or "Not installed"
        form.addRow(QLabel("yt-dlp:"), QLabel(ytdlp_version))

        # Qt version
        qt_version = PyQt6.QtCore.qVersion()
        form.addRow(QLabel("Qt:"), QLabel(qt_version))

        # Platform
        platform_info = (
            f"{platform.system()} {_get_windows_release()} ({platform.machine()})"
        )
        form.addRow(QLabel("Platform:"), QLabel(platform_info))

        # Separator
        form.addRow("", QLabel())

        # License
        form.addRow(QLabel("License:"), QLabel("MIT License"))

        # Check for updates button
        update_btn = QPushButton("Check for Updates", self)
        update_btn.clicked.connect(self._check_for_updates)
        form.addRow(QLabel("Updates:"), update_btn)

        tab.setLayout(form)
        return tab

    def _check_for_updates(self) -> None:
        """Check for app updates and show result to user."""
        update_available, latest_tag, download_url = is_app_update_available()
        if update_available:
            QMessageBox.information(
                self,
                "Update Available",
                f"A new version is available: {latest_tag}\n\nDownload: {download_url}",
            )
        else:
            QMessageBox.information(
                self,
                "No Updates",
                "You are running the latest version.",
            )

    # ------------------------------------------------------------------
    # Apply logic
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        changes: dict[str, Any] = {}

        # Nested (rather than `if self._resolution_checks and not checked:` /
        # `else:`) on purpose: `{} and X` short-circuits to `{}` without
        # evaluating X, so a flat and/else would fall into the persist branch
        # and write VID_DL_ENABLED_RESOLUTIONS="" whenever _resolution_checks is
        # empty — the exact "worse" outcome the empty-selection guard exists to
        # prevent. Nesting makes an empty _resolution_checks a true no-op.
        if self._resolution_checks:
            checked = tuple(
                h for h, box in self._resolution_checks.items() if box.isChecked()
            )
            if not checked:
                QMessageBox.warning(
                    self,
                    "At least one resolution required",
                    "Keep at least one resolution checked — the main window needs a "
                    "drop target. Leaving your previous selection unchanged.",
                )
            else:
                new_enabled = format_enabled_heights(checked)
                if new_enabled != get_setting("VID_DL_ENABLED_RESOLUTIONS"):
                    _persist_setting("VID_DL_ENABLED_RESOLUTIONS", new_enabled)
                    changes["VID_DL_ENABLED_RESOLUTIONS"] = new_enabled

        for key, widget in self._edits.items():
            if isinstance(widget, QCheckBox):
                new_val: Any = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                new_val = widget.value()
            elif isinstance(widget, QComboBox):
                new_val = widget.currentData()
            else:
                new_val = widget.text().strip()

            if new_val != get_setting(key):
                _persist_setting(key, new_val)
                changes[key] = new_val

        if changes:
            self.settings_changed.emit(changes)


# ============================================================================
# Internal helper
# ============================================================================


def _wrap(layout: QHBoxLayout) -> QWidget:
    """Wrap a QHBoxLayout in a plain QWidget so it can be used as a form value widget."""
    w = QWidget()
    w.setLayout(layout)
    return w

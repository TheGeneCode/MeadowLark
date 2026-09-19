"""First-run wizard that collects required folder paths and writes them to AppData."""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_APPDATA_DIR = Path.home() / "AppData" / "Roaming" / "MeadowLark"
_USER_ENV = _APPDATA_DIR / ".env"


def needs_first_run() -> bool:
    return not _USER_ENV.exists()


class FirstRunWizard(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome — MeadowLark Setup")
        self.setMinimumWidth(500)
        self._video_dir = str(Path.home() / "Videos")
        self._podcast_dir = str(Path.home() / "Music" / "Podcasts")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "<b>Welcome to MeadowLark!</b><br><br>Where should downloaded videos be saved?",
            ),
        )
        vid_row = QHBoxLayout()
        self._video_edit = QLineEdit(self._video_dir)
        vid_browse = QPushButton("Browse…")
        vid_browse.clicked.connect(self._browse_video)
        vid_row.addWidget(self._video_edit)
        vid_row.addWidget(vid_browse)
        layout.addLayout(vid_row)

        layout.addWidget(QLabel("Where should podcast episodes be saved? (optional)"))
        pod_row = QHBoxLayout()
        self._podcast_edit = QLineEdit(self._podcast_dir)
        pod_browse = QPushButton("Browse…")
        pod_browse.clicked.connect(self._browse_podcast)
        pod_row.addWidget(self._podcast_edit)
        pod_row.addWidget(pod_browse)
        layout.addLayout(pod_row)

        layout.addWidget(
            QLabel(
                "<i>Settings are saved to AppData and can be changed there later.</i>",
            ),
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_video(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Video Folder",
            self._video_edit.text(),
        )
        if chosen:
            self._video_edit.setText(chosen)

    def _browse_podcast(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Podcast Folder",
            self._podcast_edit.text(),
        )
        if chosen:
            self._podcast_edit.setText(chosen)

    def accept(self) -> None:
        _APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f"VID_DL_VIDEO_STORAGE_DIR={self._video_edit.text()}\n",
            f"VID_DL_PODCAST_MISC_OUTPUT_DIR={self._podcast_edit.text()}\n",
        ]
        _USER_ENV.write_text("".join(lines), encoding="utf-8")
        super().accept()

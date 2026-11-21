"""
Typing base shared by the main-window mixins.

At runtime ``WindowState`` is a plain ``object`` subclass, so it adds nothing to Qt's MRO. Under
type checking it is a ``QWidget``, so mixin methods may call widget APIs. Instance attributes
assigned in ``MyWindow.__init__``/``_setup_*`` are declared here once, as annotations, so every
mixin type-checks against the same state. Methods one mixin calls on another are declared as
stubs inside ``if TYPE_CHECKING:`` and never exist at runtime.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any

    from PyQt6.QtWidgets import QCheckBox, QLabel, QPlainTextEdit, QProgressBar, QPushButton
    from PyQt6.QtWidgets import QWidget as _QWidgetBase

    from src import qyt
else:
    _QWidgetBase = object


class WindowState(_QWidgetBase):
    """State and cross-mixin contract of the main window (annotations only)."""

    _ready_text: str

    if TYPE_CHECKING:
        # Widgets built by LayoutMixin, and state MyWindow sets up, that other mixins read.
        # N815: names mirror the existing mixedCase attributes, so they cannot be renamed here.
        downloadQueue: queue.Queue  # noqa: N815
        podcastIndicator: QPushButton  # noqa: N815
        buttonFailed: QPushButton  # noqa: N815
        buttonPending: QPushButton  # noqa: N815
        checkIgnoreArchive: QCheckBox  # noqa: N815
        labelOutput: QLabel  # noqa: N815
        barProgress: QProgressBar  # noqa: N815
        logEdit: QPlainTextEdit  # noqa: N815
        # Worker class nested in MyWindow; typed loosely so the stub needs no import of it.
        _PodcastCheckWorker: type[Any]
        # Podcast state set up by PodcastCheckMixin._setup_podcast_state.
        _podcast_last_statuses: list[dict]
        _podcast_latest_url_cache: dict[str, dict]
        _podcasts_downloading: set[str]
        # Custom widgets with extra attributes (originalText, playlist_path); typed loosely.
        labelAudio: Any  # noqa: N815
        buttonAudioPlaylists: Any  # noqa: N815
        buttonUpdate: QPushButton  # noqa: N815
        # Worker class nested in MyWindow, like _PodcastCheckWorker.
        _AppUpdateWorker: type[Any]
        # Parked-download store path, set in MyWindow.__init__.
        pending_queue_path: Path

        def make_match_filter(self, source: str, label: str | None = None) -> Callable: ...
        def _fork_download_context(
            self,
            base_opts: dict,
        ) -> tuple[qyt.QHook, qyt.QLogger, dict]: ...
        def _wire_download_signals(self, qhook: qyt.QHook, qlogger: qyt.QLogger) -> None: ...
        def _create_download_context(self) -> tuple[qyt.QHook, qyt.QLogger, dict]: ...
        def get_options(
            self,
            urls: list,
            source: str,
            skip_playlist_dialog: bool = False,
        ) -> dict | None: ...
        def append_properties(self, dictionary: dict, properties: dict) -> dict: ...
        def handle_log_entry(self, entry: str) -> None: ...
        def _filter_audio_playlist_urls(
            self,
            urls: list,
            ydl_opts: dict,
            *,
            bypass_sponsorblock_wait: bool = False,
        ) -> tuple[list, list, bool, list, list]: ...
        def _on_podcast_check_finished(
            self,
            to_download: list,
            pending: list,
            had_error: bool,
            ydl_opts: dict,
            messages: list | None,
            statuses: list | None = None,
        ) -> None: ...
        def _cache_put(
            self,
            playlist_url: str,
            latest_url: str | None,
            latest_ts: int | None,
        ) -> None: ...
        def _refresh_podcast_status_dialog(self) -> None: ...
        def _restart_podcast_timer(self) -> None: ...
        def _populate_resolution_grid(self) -> None: ...

        def playlist_button_clicked(self, source: str) -> None: ...
        def request_detected(self, urls: list, source: str) -> None: ...
        def _show_podcast_status(self) -> None: ...
        def _show_pending_downloads(self) -> None: ...
        def _show_failed_downloads(self) -> None: ...
        def _open_settings(self) -> None: ...

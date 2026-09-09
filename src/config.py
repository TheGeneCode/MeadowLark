"""Centralized configuration management for paths and constants with environment variable fallbacks."""

import os
import sys
from pathlib import Path
from subprocess import SubprocessError
from typing import Final

# ============================================================================
# Exception Constants
# ============================================================================
# Import yt-dlp exceptions for consistent error handling
from yt_dlp.utils import DownloadError, ExtractorError, MaxDownloadsReached

from .resolutions import (
    button_label_key,
    drop_label_key,
    get_preset,
    parse_enabled_heights,
    playlist_file_key,
)

# Exception tuples for consistent error handling across the application.
# SubprocessError covers the helper processes yt-dlp shells out to mid-extraction
# -- notably the bgutil PO-token provider's Deno probe, which yt-dlp gives a hard
# 15s budget and which overruns it whenever Deno has to re-resolve the provider's
# npm deps over the network. That surfaces as subprocess.TimeoutExpired escaping
# ydl.download(); it is a failed download, not a bug in the app, so it must be
# caught wherever the yt-dlp errors are.
YDL_COMMON_ERRORS: Final = (DownloadError, ExtractorError, OSError, SubprocessError)
YDL_EXTRACTION_ERRORS: Final = (
    DownloadError,
    ExtractorError,
    OSError,
    SubprocessError,
    ValueError,
)
YDL_DOWNLOAD_ERRORS: Final = (
    DownloadError,
    ExtractorError,
    MaxDownloadsReached,
    OSError,
    SubprocessError,
    ValueError,
)

# ============================================================================
# Path Configuration
# ============================================================================


def _resolve_path(env_var: str, default: str | Path) -> Path:
    """
    Resolve a path from environment variable with fallback to default.

    Args:
        env_var: Environment variable name to check.
        default: Default path if env var not set.

    Returns:
        Resolved Path object.
    """
    env_value = os.getenv(env_var)
    if env_value:
        return Path(env_value)
    return Path(default)


# Log files
ERROR_LOG_PATH: Final[Path] = _resolve_path("VID_DL_ERROR_LOG", "error_log.txt")
HISTORY_LOG_PATH: Final[Path] = _resolve_path("VID_DL_HISTORY_LOG", "history_log.txt")

# Resource directories and files
RESOURCES_DIR: Final[Path] = _resolve_path("VID_DL_RESOURCES_DIR", "resources")
COOKIES_FILE: Final[Path] = _resolve_path(
    "VID_DL_COOKIES_FILE", RESOURCES_DIR / "cookies.txt"
)
LIVE_QUEUE_FILE: Final[Path] = RESOURCES_DIR / "live_queue.txt"
FAILED_DOWNLOADS_FILE: Final[Path] = RESOURCES_DIR / "failed_downloads.json"
# Deferred downloads: live streams parked by match_filter plus premieres that were
# announced but had not aired at download time (see src/pending_queue.py). Supersedes
# LIVE_QUEUE_FILE, which is now read once and migrated.
PENDING_QUEUE_FILE: Final[Path] = RESOURCES_DIR / "pending_queue.json"

# Playlist files
PLAYLISTS_DIR: Final[Path] = RESOURCES_DIR / "playlists"
PLAYLISTS_FILE: Final[Path] = _resolve_path(
    "VID_DL_PLAYLISTS_FILE",
    PLAYLISTS_DIR / "playlists.txt",
)
PLAYLISTS_720_FILE: Final[Path] = _resolve_path(
    "VID_DL_PLAYLISTS_720_FILE",
    PLAYLISTS_DIR / "720playlists.txt",
)
PLAYLISTS_AUDIO_FILE: Final[Path] = _resolve_path(
    "VID_DL_PLAYLISTS_AUDIO_FILE",
    PLAYLISTS_DIR / "audio playlists.txt",
)


def playlist_path_for_height(height: int) -> Path:
    """
    Return the playlist file for a resolution rung, honoring its env override.

    1080 and 720 resolve through their pre-registry key names and pre-registry
    filenames (see src/resolutions.py); every other rung uses
    ``VID_DL_PLAYLISTS_<height>_FILE`` over ``PLAYLISTS_DIR/<height>playlists.txt``.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        Resolved Path (it need not exist; load_playlist_urls creates a template).
    """
    preset = get_preset(height)
    if preset is None:
        return PLAYLISTS_DIR / f"{height}playlists.txt"
    return _resolve_path(
        playlist_file_key(height),
        PLAYLISTS_DIR / preset.playlist_filename,
    )


def drop_label_for_height(height: int) -> str:
    """Return the configured drop-target text for a rung, defaulting to its label."""
    preset = get_preset(height)
    default = preset.label if preset is not None else str(height)
    return os.getenv(drop_label_key(height), default)


def button_label_for_height(height: int) -> str:
    """Return the configured playlist-button text for a rung."""
    preset = get_preset(height)
    default = f"{preset.label} Playlists" if preset is not None else f"{height} Playlists"
    return os.getenv(button_label_key(height), default)


# External storage paths
ARCHIVE_PATH: Final[Path] = _resolve_path(
    "VID_DL_ARCHIVE_PATH",
    Path(__file__).parent.parent / "resources" / "archive.txt",
)
PODCAST_MISC_OUTPUT_DIR: Final[Path] = _resolve_path(
    "VID_DL_PODCAST_MISC_OUTPUT_DIR",
    Path.home() / "Music" / "Podcasts",
)
VIDEO_STORAGE_DIR: Final[Path] = _resolve_path(
    "VID_DL_VIDEO_STORAGE_DIR",
    Path.home() / "Videos",
)

# JavaScript runtime configuration
if getattr(sys, "frozen", False):
    VENV_SCRIPTS_DIR: Final[Path] = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    VENV_SCRIPTS_DIR: Final[Path] = _resolve_path(
        "VID_DL_VENV_SCRIPTS", ".venv/Scripts"
    )

# Make the bundled Deno runtime discoverable on PATH. The yt-dlp PO-token
# provider (bgutil, script mode) locates node/deno via PATH, not via yt-dlp's
# js_runtimes. When the app is launched with pythonw.exe WITHOUT activating the
# venv, .venv/Scripts is not on PATH, so the provider silently becomes
# unavailable and 1080p downloads regress to HTTP 403 (see YOUTUBE_PLAYER_CLIENTS
# below). Prepend the (absolute) scripts dir once, only if it really exists.
_scripts_abs = str(VENV_SCRIPTS_DIR.resolve())
if VENV_SCRIPTS_DIR.exists() and _scripts_abs not in os.environ.get("PATH", "").split(
    os.pathsep
):
    os.environ["PATH"] = _scripts_abs + os.pathsep + os.environ.get("PATH", "")

# Absolute path to the bundled Deno executable, or None when it is not there.
# VENV_SCRIPTS_DIR defaults to the *relative* ".venv/Scripts", so any consumer
# that hands that path to a subprocess only works while the process CWD happens
# to be the repo root -- which it is under `uv run`, but not under a pythonw
# shortcut. Resolve it once here so every consumer gets an absolute path.
_deno_exe = VENV_SCRIPTS_DIR.resolve() / "deno.exe"
DENO_EXECUTABLE: Final[Path | None] = _deno_exe if _deno_exe.is_file() else None

# ============================================================================
# yt-dlp extractor behavior
# ============================================================================

# YouTube gates 1080p+ media URLs behind a per-video "GVS PO token" (SABR
# experiment, yt-dlp #12482). The bgutil provider (script mode via the bundled
# Deno runtime) mints that token, and it is still REQUIRED -- without it every
# 1080p download 403s regardless of which client is selected here.
#
# But a valid token is no longer sufficient. As of 2026-08, YouTube enforces
# SABR on "mweb" for this account: its media URLs carry a correct "pot=", the
# first request succeeds, and the server then cuts the transfer off with HTTP
# 403 after roughly ONE MEGABYTE. Measured directly, same video and second,
# varying only the request: --test (10 KB range) succeeded, while a full
# download 403'd; with --http-chunk-size the file reached ~1.0-1.07 MB at every
# chunk size (10 KB, 256 KB, 1 MB) before 403. That byte allowance is why the
# failure looks like "all downloads are broken" while any short-range probe
# passes -- and it is why a test-mode repro cannot detect this bug.
# "tv" and every other client tried (ios, android, android_vr, web, web_safari)
# now expose only SABR formats, so they fail earlier still, with "Requested
# format is not available".
#
# "tv_embedded" was the client measured to serve complete files (full 1080p
# video+audio downloads of 37 MB / 173 MB / 228 MB completed with no 403), but
# yt-dlp has since dropped it from INNERTUBE_CLIENTS entirely -- it no longer
# appears in extractor/youtube/_base.py's client registry. Requesting a name
# yt-dlp doesn't recognize doesn't error; _get_requested_clients logs
# "Skipping unsupported client" and silently substitutes its authenticated-
# default set instead (since we always pass a cookiefile), which currently
# resolves to "tv_downgraded" -- a client YouTube broke for cookie-
# authenticated requests server-side in August 2026 (yt-dlp#17389), producing
# "The page needs to be reloaded" on every video. "web_embedded" is yt-dlp's
# confirmed-working replacement (verified 2026-08-18: full untruncated
# downloads, no SABR cutoff) and actually exists in the client registry.
# Order matters: the first client that supplies a given format id wins, so do
# NOT append "mweb" as a fallback -- it would win rungs web_embedded could
# have served and reintroduce the 1 MB cutoff. Override with
# VID_DL_YT_PLAYER_CLIENT (comma-separated, in priority order).
YOUTUBE_PLAYER_CLIENTS: Final[str] = os.getenv("VID_DL_YT_PLAYER_CLIENT", "web_embedded")

# PO Token provider (bgutil "script-deno" mode) server home. In script mode the
# provider looks for {server_home}/src/generate_once.ts and {server_home}/
# node_modules (see yt_dlp_plugins.extractor.getpot_bgutil_script). The default is
# the vendored copy under vendor/bgutil-pot-provider/server (dev), or the bundled
# "bgutil-server" dir inside the PyInstaller bundle (frozen). node_modules is
# generated by scripts/setup_pot_provider.py. Override with VID_DL_POT_SERVER_HOME.
if getattr(sys, "frozen", False):
    _pot_default_home: Path = Path(sys._MEIPASS) / "bgutil-server"  # type: ignore[attr-defined]
else:
    _pot_default_home = (
        Path(__file__).parent.parent / "vendor" / "bgutil-pot-provider" / "server"
    )
POT_PROVIDER_SERVER_HOME: Final[Path] = _resolve_path(
    "VID_DL_POT_SERVER_HOME",
    _pot_default_home,
)


# ============================================================================
# Timeout Configuration (seconds)
# ============================================================================

HTTP_TIMEOUT_SECONDS: Final[int] = int(
    os.getenv("VID_DL_HTTP_TIMEOUT", "120"),
)
SOCKET_TIMEOUT_SECONDS: Final[int] = int(
    os.getenv("VID_DL_SOCKET_TIMEOUT", "120"),
)

# HTTP request timeout for external API calls (e.g., SponsorBlock)
HTTP_REQUEST_TIMEOUT_SECONDS: Final[int] = int(
    os.getenv("VID_DL_HTTP_REQUEST_TIMEOUT", "5"),
)

# ============================================================================
# Download Configuration
# ============================================================================

MAX_FRAGMENT_RETRIES: Final[int] = int(
    os.getenv("VID_DL_MAX_FRAGMENT_RETRIES", "10"),
)

# HTTP status code
HTTP_OK: Final[int] = 200

# ============================================================================
# Progress Smoothing Configuration
# ============================================================================

# Shortest rolling-average window, used in the first seconds of a download (seconds)
PROGRESS_MIN_WINDOW_SECONDS: Final[float] = float(
    os.getenv("VID_DL_PROGRESS_MIN_WINDOW_SECONDS", "0.5"),
)

# Longest rolling-average window, reached on long downloads (seconds)
PROGRESS_MAX_WINDOW_SECONDS: Final[float] = float(
    os.getenv("VID_DL_PROGRESS_MAX_WINDOW_SECONDS", "30.0"),
)

# Rolling-average window grows as this fraction of elapsed download time
PROGRESS_WINDOW_ELAPSED_FRACTION: Final[float] = float(
    os.getenv("VID_DL_PROGRESS_WINDOW_ELAPSED_FRACTION", "0.25"),
)

# EMA weight applied to yt-dlp's per-fragment total_bytes_estimate
PROGRESS_TOTAL_EMA_ALPHA: Final[float] = float(
    os.getenv("VID_DL_PROGRESS_TOTAL_EMA_ALPHA", "0.2"),
)

# Minimum interval between progress label/bar repaints (seconds)
PROGRESS_UI_MIN_INTERVAL_SECONDS: Final[float] = float(
    os.getenv("VID_DL_PROGRESS_UI_MIN_INTERVAL_SECONDS", "0.2"),
)

# Hard cap on retained progress samples (memory safety net; time pruning normally wins)
PROGRESS_MAX_SAMPLES: Final[int] = int(
    os.getenv("VID_DL_PROGRESS_MAX_SAMPLES", "4000"),
)

# ============================================================================
# Podcast Configuration
# ============================================================================

# Minimum duration for podcast episodes (seconds)
PODCAST_MIN_DURATION_SECONDS: Final[int] = int(
    os.getenv("VID_DL_PODCAST_MIN_DURATION_SECONDS", "180"),
)

# SponsorBlock cache TTL (hours)
SPONSORBLOCK_CACHE_TTL_HOURS: Final[int] = int(
    os.getenv("VID_DL_SPONSORBLOCK_CACHE_TTL_HOURS", "6"),
)

# Live queue check interval (minutes)
LIVE_QUEUE_CHECK_INTERVAL_MINUTES: Final[int] = int(
    os.getenv("VID_DL_LIVE_QUEUE_CHECK_INTERVAL_MINUTES", "30"),
)

# Maximum attempts to fetch the latest accessible playlist entry
PODCAST_LOOKAHEAD_MAX_ATTEMPTS: Final[int] = int(
    os.getenv("VID_DL_PODCAST_LOOKAHEAD_MAX_ATTEMPTS", "5"),
)

PODCAST_AUTO_CHECK: Final[bool] = (
    os.getenv("VID_DL_PODCAST_AUTO_CHECK", "true").lower() == "true"
)
ALWAYS_ON_TOP: Final[bool] = os.getenv("VID_DL_ALWAYS_ON_TOP", "true").lower() == "true"
APP_UPDATE_AUTO_CHECK: Final[bool] = (
    os.getenv("VID_DL_APP_UPDATE_AUTO_CHECK", "true").lower() == "true"
)
MARK_WATCHED: Final[bool] = os.getenv("VID_DL_MARK_WATCHED", "false").lower() == "true"
APP_UPDATE_LAST_CHECKED: Final[str] = os.getenv("VID_DL_APP_UPDATE_LAST_CHECKED", "")

# Last geometry of the Podcast Status window, as the base64 form of Qt's
# QWidget.saveGeometry() blob.  Machine-written (see src/window_geometry.py),
# never edited by hand -- an unreadable value just falls back to the default size.
PODCAST_STATUS_GEOMETRY_KEY: Final[str] = "VID_DL_PODCAST_STATUS_GEOMETRY"
PODCAST_STATUS_GEOMETRY: Final[str] = os.getenv(PODCAST_STATUS_GEOMETRY_KEY, "")
PODCAST_CHECK_INTERVAL_MINUTES: Final[int] = int(
    os.getenv("VID_DL_PODCAST_CHECK_INTERVAL_MINUTES", "60"),
)

# ============================================================================
# Display Configuration
# ============================================================================

LABEL_OUTPUT_FONT_NAME: Final[str] = os.getenv("VID_DL_LABEL_OUTPUT_FONT", "Arial")
LABEL_OUTPUT_FONT_SIZE: Final[int] = int(
    os.getenv("VID_DL_LABEL_OUTPUT_FONT_SIZE", "16"),
)
LABEL_READY_TEXT: Final[str] = os.getenv("VID_DL_LABEL_READY_TEXT", "[ Ready ]")
LABEL_DROP_1080: Final[str] = os.getenv("VID_DL_LABEL_DROP_1080", "1080")
LABEL_DROP_720: Final[str] = os.getenv("VID_DL_LABEL_DROP_720", "720")
LABEL_DROP_AUDIO: Final[str] = os.getenv("VID_DL_LABEL_DROP_AUDIO", "audio")
LABEL_BTN_PLAYLISTS: Final[str] = os.getenv("VID_DL_LABEL_BTN_PLAYLISTS", "Playlists")
LABEL_BTN_720: Final[str] = os.getenv("VID_DL_LABEL_BTN_720", "720 Playlists")
LABEL_BTN_PODCASTS: Final[str] = os.getenv("VID_DL_LABEL_BTN_PODCASTS", "YT Podcasts")

# Which resolution rungs the UI shows. Comma-separated heights; unknown or
# malformed entries are dropped and an empty result falls back to the default
# pair, because an app with zero drop targets cannot be fixed from its own UI.
ENABLED_RESOLUTIONS: Final[tuple[int, ...]] = parse_enabled_heights(
    os.getenv("VID_DL_ENABLED_RESOLUTIONS"),
)

# ============================================================================
# Post-Processing Configuration
# ============================================================================

DEFAULT_MERGE_OUTPUT_FORMAT: Final[str] = os.getenv(
    "VID_DL_MERGE_OUTPUT_FORMAT",
    "mp4",
)
DEFAULT_VIDEO_FORMAT: Final[str] = os.getenv("VID_DL_VIDEO_FORMAT", "mp4")
DEFAULT_AUDIO_FORMAT: Final[str] = os.getenv("VID_DL_AUDIO_FORMAT", "m4a")

# ============================================================================
# Debug Configuration
# ============================================================================

LOGFILE_MIGRATION_ENABLED: Final[bool] = (
    os.getenv("VID_DL_LOGFILE_MIGRATION", "true").lower() == "true"
)

# yt-dlp's own diagnostic stream. Off by default: it is verbose and the media
# URLs it prints carry PO tokens and session identifiers. Turn it on to diagnose
# extraction/403 failures -- error_log.txt only ever receives the final exception
# string, which does not say which player client served the chosen format or
# whether its media URL carried a "pot=" token.
YTDLP_VERBOSE: Final[bool] = (
    os.getenv("VID_DL_YTDLP_VERBOSE", "false").lower() == "true"
)
# Absolute: the point of this file is that someone can find it and read it, and
# the app is not always launched from the repo root.
YTDLP_DEBUG_LOG_PATH: Final[Path] = (
    _resolve_path("VID_DL_YTDLP_DEBUG_LOG", RESOURCES_DIR / "ytdlp_debug.log")
    .expanduser()
    .resolve()
)

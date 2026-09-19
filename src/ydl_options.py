"""Centralized yt-dlp option builders and constants."""

from pathlib import Path
from typing import Any

from .config import (
    COOKIES_FILE,
    DENO_EXECUTABLE,
    MAX_FRAGMENT_RETRIES,
    PODCAST_MISC_OUTPUT_DIR,
    POT_PROVIDER_SERVER_HOME,
    SOCKET_TIMEOUT_SECONDS,
    VIDEO_STORAGE_DIR,
    YOUTUBE_PLAYER_CLIENTS,
    YTDLP_VERBOSE,
)
from .dict_utils import DEFAULT_POSTPROCESSORS
from .path_utils import slugify_if_too_long
from .qt_protocols import YdlLogger, YdlProgressHook
from .resolutions import RESOLUTION_PRESETS, playlist_source_key
from .settings_dialog import get_setting

MISC_PODCAST_LABEL = "misc"
"""Folder used for audio episodes whose show cannot be resolved."""

# JavaScript runtimes configuration. yt-dlp documents ``path`` as the path to the
# *executable*, and it must be absolute: the value used to be VENV_SCRIPTS_DIR,
# which is both a directory and (by default) the relative ".venv/Scripts", so the
# resulting "deno" invocation only resolved while the process CWD was the repo
# root. When the bundled runtime is absent, omit ``path`` entirely so yt-dlp falls
# back to searching PATH -- config.py prepends the scripts dir there.
JS_RUNTIMES_CONFIG: dict[str, dict[str, str]] = {
    "deno": {"path": str(DENO_EXECUTABLE)} if DENO_EXECUTABLE is not None else {},
}


def resolve_cookiefile() -> str:
    """
    Return the cookie jar path as an absolute string.

    The configured default is the relative ``resources/cookies.txt``; handing
    that to yt-dlp silently yields an empty cookie jar whenever the process CWD
    is not the repo root, which costs age-restricted and members-only videos.

    Returns:
        Absolute path to the cookies file (it need not exist yet).
    """
    # get_setting is untyped and backed by a generic store, so a non-string here
    # is not a path at all -- fall back to the configured default rather than
    # letting Path() raise and take the whole option build down with it.
    configured = get_setting("VID_DL_COOKIES_FILE")
    raw = configured if isinstance(configured, str) and configured else str(COOKIES_FILE)
    return str(Path(raw).expanduser().resolve())


def build_shared_extraction_opts() -> dict[str, Any]:
    """
    Options every YoutubeDL instance must carry, even metadata-only ones.

    Without these, the bgutil PO-token provider falls back to its
    ~/bgutil-ytdlp-pot-provider default server_home instead of the
    vendored/bundled copy, and its Deno availability probe (hard 15s budget
    inside yt-dlp) runs against a cold cache -- re-resolving the provider's
    npm deps over the network and surfacing subprocess.TimeoutExpired to the
    caller. See POT_PROVIDER_SERVER_HOME in config.

    Returns:
        Dictionary with the JS-runtime and extractor-arg wiring shared by
        all YoutubeDL constructions.
    """
    return {
        "js_runtimes": JS_RUNTIMES_CONFIG,
        # Force an explicit client priority so YouTube's per-client 403 gating
        # (SABR/PO-token experiment, #12482) can't steer us onto broken media
        # URLs (see YOUTUBE_PLAYER_CLIENTS in config). Only affects YouTube.
        "extractor_args": {
            "youtube": {
                "player_client": [
                    c.strip() for c in YOUTUBE_PLAYER_CLIENTS.split(",") if c.strip()
                ],
            },
            # Point the bgutil script provider at our vendored/bundled server copy
            # instead of its ~/bgutil-ytdlp-pot-provider default. Extractor-arg
            # values are lists; the provider reads _configuration_arg(...)[0].
            "youtubepot-bgutilscript": {
                "server_home": [str(POT_PROVIDER_SERVER_HOME)],
            },
        },
    }


def build_base_ydl_opts(logger: YdlLogger, qhook: YdlProgressHook) -> dict[str, Any]:
    """
    Centralize common yt-dlp options used across the app.

    Args:
        logger: Logger instance with debug/warning/error/exception methods.
        qhook: Progress hook callable that emits info_changed signal.

    Returns:
        Dictionary of base yt-dlp options suitable for most downloads.
    """
    opts: dict[str, Any] = {
        "logger": logger,
        "progress_hooks": [qhook],
        "windowsfilenames": True,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        "max_fragment_retries": MAX_FRAGMENT_RETRIES,
        "mtime": True,
        # Custom match_filter will be set per-source by callers
        "cookiefile": resolve_cookiefile(),
        "postprocessors": list(DEFAULT_POSTPROCESSORS),
        "remote_components": ["ejs:github"],
        **build_shared_extraction_opts(),
    }
    if get_setting("VID_DL_MARK_WATCHED"):
        opts["mark_watched"] = True
    if YTDLP_VERBOSE:
        # Makes yt-dlp emit its [debug] stream through ``logger``; QLogger tees it
        # to resources/ytdlp_debug.log. See get_ytdlp_debug_logger.
        opts["verbose"] = True
    return opts


def _build_video_format_selector(height: int | None, vfmt: str) -> str:
    # ``height<=`` rather than ``height==``: a quality rung names a rendition
    # tier, not a literal pixel height. Letterboxed / ultrawide masters are
    # encoded at 1920x816, 1280x544, 2560x1088 and so on, so an equality match
    # excludes *every* rendition of such a video and yt-dlp fails the whole
    # download with "Requested format is not available".
    h = f"[height<={height}]" if height else ""
    if vfmt == "webm":
        # Only allow webm-native streams: VP9/VP8 video + Opus/Vorbis audio.
        # Mixed-codec fallbacks (e.g. VP9+AAC) cannot be stream-copied into webm
        # and would trigger an ffmpeg postprocessing error.
        # If nothing under the requested height has a VP9 stream, fall back to
        # the best webm at any height rather than failing entirely.
        return (
            f"bestvideo*{h}[ext=webm]+bestaudio[ext=webm]/bestvideo*[ext=webm]+bestaudio[ext=webm]"
        )
    return (
        f"bestvideo*{h}[ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo*{h}+bestaudio/"
        f"best{h}/"
        # Unconstrained catch-all. Bare ``best`` only matches *muxed* formats,
        # so on sites that publish HLS as separate video-only and audio-only
        # renditions (Nebula) it can never fire; pair it with a merge rung that
        # can.
        "bestvideo*+bestaudio/best"
    )


def podcast_base_dir() -> str:
    """
    Return the directory that holds one folder per podcast show.

    The configured audio/podcast directory is the "misc" bucket for episodes
    whose show is unknown; the per-show folders are its siblings.

    Returns:
        Posix-style path of the directory containing the per-show folders.
    """
    misc_dir = Path(get_setting("VID_DL_PODCAST_MISC_OUTPUT_DIR") or str(PODCAST_MISC_OUTPUT_DIR))
    return misc_dir.parent.as_posix()


def build_podcast_outtmpl(label: str | None) -> str:
    """
    Return the output template that files an audio episode under its show folder.

    Every ``audio_playlists`` download must go through this: the flat template
    in ``get_source_options`` writes straight into the misc directory, which is
    only correct for episodes with no known show.

    Args:
        label: Resolved show label, or None/empty when the show is unknown.

    Returns:
        yt-dlp output template rooted at ``<podcast base>/<show label>``.
    """
    base_dir = podcast_base_dir()
    safe_label = slugify_if_too_long(base_dir, label or MISC_PODCAST_LABEL)
    return f"{base_dir}/{safe_label}/%(title)s.%(ext)s"


def get_source_options(source: str) -> dict[str, Any]:
    """
    Return yt-dlp properties for a given source type.

    Args:
        source: The download source identifier.

    Returns:
        A dictionary of yt-dlp options specific to the source.
    """
    vfmt = str(get_setting("VID_DL_VIDEO_FORMAT") or "mp4")
    afmt = str(get_setting("VID_DL_AUDIO_FORMAT") or "m4a")
    video_dir = Path(get_setting("VID_DL_VIDEO_STORAGE_DIR") or str(VIDEO_STORAGE_DIR))
    podcast_dir = Path(
        get_setting("VID_DL_PODCAST_MISC_OUTPUT_DIR") or str(PODCAST_MISC_OUTPUT_DIR)
    )

    source_options: dict[str, dict[str, Any]] = {
        "audio": {
            "format": f"{afmt}/bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": afmt},
            ],
            "outtmpl": (podcast_dir / "%(title)s.%(ext)s").as_posix(),
        },
        "audio_playlists": {
            "format": f"{afmt}/bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": afmt},
            ],
            "outtmpl": (podcast_dir / "%(title)s.%(ext)s").as_posix(),
            "ignoreerrors": "only_download",
        },
    }

    playlist_outtmpl = (
        video_dir / "%(playlist)s" / "%(playlist_index)s - %(title)s.%(ext)s"
    ).as_posix()
    # One entry per registered rung rather than two literals. The values are
    # identical to the old 720/1080 entries apart from the requested height, so
    # a new rung needs no edit here at all. Generating every registered rung
    # (not just enabled ones) is deliberate: a failed-download record or a
    # parked pending item can carry a rung the user has since disabled, and
    # failed_downloads_dialog.py calls get_source_options on it.
    for preset in RESOLUTION_PRESETS:
        source_options[playlist_source_key(preset.height)] = {
            "format": _build_video_format_selector(preset.height, vfmt),
            "merge_output_format": vfmt,
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": vfmt}],
            "outtmpl": playlist_outtmpl,
            "ignoreerrors": "only_download",
        }

    if source in source_options:
        return source_options[source].copy()

    try:
        height = int(source)
    except ValueError:
        height = None

    if height and height > 0:
        format_string = _build_video_format_selector(height, vfmt)
    else:
        format_string = "bestvideo*+bestaudio/best"

    return {
        "format": format_string,
        "merge_output_format": vfmt,
        "outtmpl": (video_dir / "%(title)s.%(ext)s").as_posix(),
        "postprocessors": [
            *DEFAULT_POSTPROCESSORS,
            {"key": "FFmpegVideoRemuxer", "preferedformat": vfmt},
        ],
    }


def get_output_template(source: str) -> str:
    """
    Return the output filename template for the source.

    Args:
        source: The source identifier.

    Returns:
        The yt-dlp output template string.
    """
    return get_source_options(source)["outtmpl"]


def get_postprocessors(source: str) -> list[dict[str, Any]]:
    """
    Return the postprocessors list for the source.

    Args:
        source: The source identifier.

    Returns:
        A list of yt-dlp postprocessor dictionaries.
    """
    return get_source_options(source).get("postprocessors", list(DEFAULT_POSTPROCESSORS))

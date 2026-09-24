"""Download execution logic extracted from QYTQueue for testability and reusability."""

from collections.abc import Callable

import yt_dlp

from src.dict_utils import remove_sponsorblock_postprocessor
from src.logging_utils import log_exception

from .config import ENABLED_RESOLUTIONS, YDL_DOWNLOAD_ERRORS, YDL_EXTRACTION_ERRORS
from .path_utils import rename_playlist_folders_from_comments
from .resolutions import MAX_LADDER_DESCENT, height_from_source, lower_heights
from .settings_dialog import get_setting
from .ydl_options import _build_video_format_selector
from .ydl_utils import extract_playlist_info

YoutubeDL = yt_dlp.YoutubeDL


class DownloadExecutor:
    """
    Manages download execution with fallback strategies.

    Encapsulates download logic including title extraction, format fallbacks,
    and error recovery. Designed for testability with dependency injection.
    """

    def __init__(
        self,
        message_callback: Callable[[str], None] | None = None,
    ) -> None:
        """
        Initialize the download executor.

        Args:
            message_callback: Optional callback for emitting status messages.
                             Called with string messages during execution.
        """
        self.message_callback = message_callback or (lambda _: None)

    def _emit_message(self, message: str) -> None:
        """Emit a status message via callback."""
        self.message_callback(message)

    def _run_download(self, opts: dict, urls: list) -> None:
        """
        Run a yt-dlp download.

        Note: we deliberately do NOT wipe ``ydl.cache`` here. Recent yt-dlp
        caches the YouTube JS-challenge solver library (and player data) under
        ~/.cache/yt-dlp; blanket-clearing it every run forced a fresh GitHub
        fetch of the solver on each download, turning a transient upstream blip
        (e.g. HTTP 504) into a hard "Requested format is not available" failure.
        yt-dlp keys its cache by player URL/version and self-invalidates.
        """
        with YoutubeDL(opts) as ydl:
            ydl.download(urls)

    def _extract_title(self, urls: list, options: dict | None = None) -> str:
        """
        Extract video title from first URL for error logging.

        Args:
            urls: List of URLs to extract from.
            options: Optional yt-dlp options from the download context.
                     ``cookiefile`` is forwarded so age-restricted lookups succeed.

        Returns:
            Video title or '(unknown)' if extraction fails.
        """
        if not urls:
            return "(unknown)"

        title = urls[0]
        extra_opts: dict | None = None
        if options and (cookiefile := options.get("cookiefile")):
            extra_opts = {"cookiefile": cookiefile}
        try:
            info = extract_playlist_info(urls[0], ydl_class=YoutubeDL, extra_opts=extra_opts)
            title = info.get("title", title)
        except YDL_EXTRACTION_ERRORS as exc:
            log_exception(exc, "Failed to extract title for error logging")
        return title

    def _try_fallback(
        self,
        urls: list,
        options: dict,
        tried_flag: str,
        trigger_phrase: str | tuple[str, ...],
        error_str: str,
        message: str,
        options_modifier: Callable[[dict], dict],
        log_context: str,
    ) -> tuple[bool, str]:
        """Attempt a generic fallback download if a trigger phrase is present and not yet tried."""
        phrases = (trigger_phrase,) if isinstance(trigger_phrase, str) else trigger_phrase
        if not any(phrase in error_str for phrase in phrases) or options.get(tried_flag):
            return False, error_str
        self._emit_message(message)
        fallback = options_modifier(options)
        fallback[tried_flag] = True
        try:
            self._run_download(fallback, urls)
            return True, error_str
        except YDL_EXTRACTION_ERRORS as e2:
            log_exception(e2, log_context)
            return False, str(e2)

    @staticmethod
    def _rung_options_modifier(height: int) -> Callable[[dict], dict]:
        """
        Build the options transform that retargets a download at *height*.

        Mirrors the original 720 fallback exactly: swap the format selector, make
        sure the container and remuxer survive, and restamp ``qmeta["type"]`` so
        the history log records the rung that actually ran rather than the one
        that was requested and gated.

        The returned callable copies the caller's dict; ``_try_fallback`` relies
        on the original options being left untouched so a later fallback (e.g.
        the SponsorBlock retry) still sees the unmodified request.
        """

        def _modify(opts: dict) -> dict:
            vfmt = str(get_setting("VID_DL_VIDEO_FORMAT") or "mp4")
            fallback = opts.copy()
            fallback["format"] = _build_video_format_selector(height, vfmt)
            fallback.setdefault("merge_output_format", vfmt)
            pps = list(fallback.get("postprocessors") or [])
            if not any(pp.get("key") == "FFmpegVideoRemuxer" for pp in pps):
                pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": vfmt})
            fallback["postprocessors"] = pps
            fq = dict(fallback.get("qmeta", {}))
            fq["type"] = str(height)
            fallback["qmeta"] = fq
            return fallback

        return _modify

    # Trigger substrings shared by every rung of the descent. These indicate a
    # *gated* media URL, not an absent rendition: the selector matches
    # ``height<=N``, so a rung that simply does not exist is already resolved
    # downward inside one yt-dlp call (yt-dlp #12482 / SABR, see
    # YOUTUBE_PLAYER_CLIENTS in config).
    _GATED_TRIGGERS: tuple[str, ...] = (
        "Requested format is not available",
        "unable to download video data",
        "HTTP Error 403",
    )

    def _try_lower_rung_fallback(
        self,
        urls: list,
        options: dict,
        title: str,
        error_str: str,
        height: int,
    ) -> tuple[bool, str]:
        """
        Retry a gated download at progressively lower resolution rungs.

        Walks the enabled rungs strictly below *height*, highest first, stopping
        at the first success or after ``MAX_LADDER_DESCENT`` attempts. The cap is
        deliberate: a systematically gated session (stale cookies, dead PO-token
        provider) would otherwise pay a full extract-and-download cycle for every
        rung on every video before finally reporting failure.

        Args:
            urls: URLs passed to the original download.
            options: The original yt-dlp options. Never mutated.
            title: Resolved title, for the status message only.
            error_str: The failure text from the original attempt.
            height: The rung that was requested and gated.

        Returns:
            (success, error_str) where error_str is the most recent failure text.
        """
        descent = lower_heights(height, enabled=ENABLED_RESOLUTIONS)[:MAX_LADDER_DESCENT]
        for rung in descent:
            success, error_str = self._try_fallback(
                urls=urls,
                options=options,
                # Per-rung flag, so a caller that already tried this rung is not
                # retried at it, while other rungs stay available. A single shared
                # flag would collapse the whole ladder into one attempt.
                tried_flag=f"_tried_rung_{rung}_fallback",
                trigger_phrase=self._GATED_TRIGGERS,
                error_str=error_str,
                message=(
                    f"{height} format unavailable or blocked for '{title}'; retrying at {rung}..."
                ),
                options_modifier=self._rung_options_modifier(rung),
                log_context=f"{rung}p fallback attempt failed",
            )
            if success:
                return True, ""
            # _try_fallback returns the *new* failure text on a failed attempt, so
            # the next rung's trigger check runs against the latest error. If that
            # text no longer carries a gating phrase the loop stops on its own,
            # which is correct: a different failure mode is not a rung problem.
        return False, error_str

    def _try_720_fallback(
        self,
        urls: list,
        options: dict,
        title: str,
        site: str,
        error_str: str,
    ) -> tuple[bool, str]:
        """
        Try downloading at 720p if the requested rung was gated.

        Retained as the pre-registry entry point: ``QYT.QYTQueue`` re-exports it
        and ``tests/test_download_executor_formats.py`` drives it directly. New
        callers should use ``_try_lower_rung_fallback``.
        """
        return self._try_fallback(
            urls=urls,
            options=options,
            tried_flag="_tried_720_fallback",
            trigger_phrase=self._GATED_TRIGGERS,
            error_str=error_str,
            message=f"1080 format unavailable or blocked for '{title}'; retrying at 720...",
            options_modifier=self._rung_options_modifier(720),
            log_context="720p fallback attempt failed",
        )

    def _try_without_sponsorblock(
        self,
        urls: list,
        options: dict,
        title: str,
        site: str,
        dtype: str,
        error_str: str,
    ) -> tuple[bool, str]:
        """Try downloading without SponsorBlock if API unavailable."""
        return self._try_fallback(
            urls=urls,
            options=options,
            tried_flag="_tried_without_sponsorblock",
            trigger_phrase="Unable to communicate with SponsorBlock API",
            error_str=error_str,
            message="SponsorBlock API unavailable; retrying download without SponsorBlock...",
            options_modifier=remove_sponsorblock_postprocessor,
            log_context="SponsorBlock removal retry failed",
        )

    def _extract_base_output_dir(self, options: dict) -> str | None:
        """Extract the base output directory from outtmpl, or None if not determinable."""
        outtmpl = options.get("outtmpl", "")
        outtmpl_str: str | None = None

        if isinstance(outtmpl, str):
            outtmpl_str = outtmpl
        elif isinstance(outtmpl, dict):
            outtmpl_str = outtmpl.get("default")
            if not isinstance(outtmpl_str, str) or not outtmpl_str:
                for value in outtmpl.values():
                    if isinstance(value, str) and value:
                        outtmpl_str = value
                        break

        if not outtmpl_str:
            return None

        # outtmpl is like "E:/vid storage/%(playlist)s/..." — take all but last segment
        parts = outtmpl_str.split("/")
        return "/".join(parts[:-1]) or None if len(parts) >= 2 else None

    def _rename_na_folder_if_needed(self, options: dict, urls: list) -> None:
        """Rename 'NA' playlist folders using comment metadata after a successful download."""
        meta = options.get("qmeta") or {}
        playlist_comments = meta.get("playlist_comments")
        if not playlist_comments:
            return
        base_output_dir = self._extract_base_output_dir(options)
        if not base_output_dir:
            return
        rename_playlist_folders_from_comments(
            base_output_dir,
            urls,
            playlist_comments,
            direct_playlist_id=meta.get("playlist_id"),
        )

    def execute(self, urls: list, options: dict) -> tuple[bool, str]:
        """
        Execute download with fallback strategies.

        Attempts a descent to lower resolution rungs (if the requested one is
        gated) and a retry without SponsorBlock (if API down) before reporting
        final failure.

        Returns (success: bool, error_message: str).
        """
        try:
            self._run_download(options, urls)
            self._rename_na_folder_if_needed(options, urls)
        except YDL_DOWNLOAD_ERRORS as e:
            title = self._extract_title(urls, options)
            error_str = str(e)
            meta = options.get("qmeta") or {}
            site = meta.get("site", "unknown")
            dtype = meta.get("type", meta.get("source", "unknown"))

            rung = height_from_source(str(dtype))
            if rung is not None:
                success, error_str = self._try_lower_rung_fallback(
                    urls, options, title, error_str, rung
                )
                if success:
                    return True, ""

            success, error_str = self._try_without_sponsorblock(
                urls,
                options,
                title,
                site,
                dtype,
                error_str,
            )
            if success:
                return True, ""

            return (
                False,
                f"Error downloading '{title}' (site: {site}, type: {dtype}): {e!s}",
            )
        else:
            return True, ""

"""
Boundary tests for _try_720_fallback format-string propagation.

Covers every cell in the boundary matrix for the VID_DL_VIDEO_FORMAT /
VID_DL_AUDIO_FORMAT settings path through _modify() inside _try_720_fallback.

Patch target: src.download_executor.get_setting
  — get_setting is bound by name into the module, so patching the attribute
    on the module object (not settings_dialog._runtime) is the right approach.
    Either patch.dict(sd._runtime, ...) or patch("src.download_executor.get_setting")
    work; we prefer patch() here so these tests don't depend on the internal
    _runtime storage structure changing.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.download_executor import DownloadExecutor

# ---------------------------------------------------------------------------
# Trigger phrase used by _try_720_fallback to decide whether to attempt
# the 720p fallback.
# ---------------------------------------------------------------------------
_TRIGGER = "Requested format is not available"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor() -> DownloadExecutor:
    """Return a fresh executor with a no-op message callback."""
    return DownloadExecutor(message_callback=lambda _: None)


def _patch_get_setting(vfmt: object, afmt: object):  # type: ignore[return]
    """Patch src.download_executor.get_setting to return vfmt/afmt for format keys."""

    def _fake_get_setting(key: str) -> object:
        if key == "VID_DL_VIDEO_FORMAT":
            return vfmt
        if key == "VID_DL_AUDIO_FORMAT":
            return afmt
        return None

    return patch("src.download_executor.get_setting", side_effect=_fake_get_setting)


def _run_fallback_and_capture_options(vfmt: object, afmt: object, base_opts: dict) -> dict:
    """Drive _try_720_fallback with a successful inner download, returning the captured opts."""
    captured: list[dict] = []

    class _CapturingYDL:
        def __init__(self, opts: dict) -> None:
            captured.append(opts)
            self._mock_instance = MagicMock()

        def __enter__(self) -> MagicMock:
            return self._mock_instance

        def __exit__(self, *_: object) -> None:
            pass

    with (
        _patch_get_setting(vfmt, afmt),
        patch("src.download_executor.YoutubeDL", side_effect=_CapturingYDL),
    ):
        executor = _make_executor()
        success, _ = executor._try_720_fallback(
            ["https://example.com/v"],
            base_opts,
            "Test Title",
            "youtube",
            _TRIGGER,
        )

    assert success is True, "Expected fallback to succeed — CapturingYDL never raises"
    assert captured, "YoutubeDL was never instantiated"
    return captured[0]


# ===========================================================================
# 1. Guard clauses — fallback should NOT fire
# ===========================================================================


class TestTry720FallbackGuards:
    """_try_720_fallback must not attempt a download unless the trigger phrase is present."""

    def test_no_trigger_phrase_returns_false(self) -> None:
        """Error string without trigger phrase → no fallback, original error returned."""
        executor = _make_executor()
        success, error = executor._try_720_fallback(
            ["url"],
            {},
            "title",
            "youtube",
            "Some unrelated error",
        )
        assert success is False
        assert error == "Some unrelated error"

    def test_already_tried_flag_blocks_retry(self) -> None:
        """_tried_720_fallback=True in opts prevents a second attempt."""
        executor = _make_executor()
        success, _ = executor._try_720_fallback(
            ["url"],
            {"_tried_720_fallback": True},
            "title",
            "youtube",
            _TRIGGER,
        )
        assert success is False

    def test_empty_error_string_no_trigger_returns_false(self) -> None:
        """Empty error string never contains the trigger phrase."""
        executor = _make_executor()
        success, error = executor._try_720_fallback(
            ["url"],
            {},
            "title",
            "youtube",
            "",
        )
        assert success is False
        assert error == ""


class TestTry720FallbackTriggersOn403:
    """Regression (#12482): a gated-1080 403 must also trigger the 720p retry, not only the 'format not available' phrase."""

    @pytest.mark.parametrize(
        "error_str",
        [
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "HTTP Error 403: Forbidden",
        ],
    )
    def test_403_error_triggers_fallback(self, error_str: str) -> None:
        """A 1080 media 403 must attempt the 720p fallback (inner download succeeds)."""
        captured: list[dict] = []

        class _CapturingYDL:
            def __init__(self, opts: dict) -> None:
                captured.append(opts)
                self._mock_instance = MagicMock()

            def __enter__(self) -> MagicMock:
                return self._mock_instance

            def __exit__(self, *_: object) -> None:
                pass

        with (
            _patch_get_setting("mp4", "m4a"),
            patch("src.download_executor.YoutubeDL", side_effect=_CapturingYDL),
        ):
            executor = _make_executor()
            success, _ = executor._try_720_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "youtube",
                error_str,
            )

        assert success is True, "403 should have triggered the 720p fallback"
        assert captured, "fallback never attempted a download"
        assert "height<=720" in captured[0]["format"]


# ===========================================================================
# 2. Nominal format — non-default vfmt/afmt appear in the fallback format string
# ===========================================================================


class TestTry720FallbackFormatString:
    """_modify() format selector: mp4/m4a for non-webm vfmt, webm streams for webm vfmt."""

    def test_non_default_vfmt_sets_merge_output_format(self) -> None:
        """vfmt='mkv' must appear in merge_output_format, not in the format selector."""
        opts = _run_fallback_and_capture_options("mkv", "m4a", {})
        assert opts["merge_output_format"] == "mkv"
        assert "ext=mkv" not in opts["format"]

    def test_format_selector_uses_mp4_source_for_non_webm_vfmt(self) -> None:
        """mp4/mkv targets use ext=mp4/m4a native streams in the format selector."""
        for vfmt in ("mp4", "mkv"):
            opts = _run_fallback_and_capture_options(vfmt, "m4a", {})
            assert "ext=mp4" in opts["format"], f"ext=mp4 missing for vfmt={vfmt!r}"
            assert "ext=m4a" in opts["format"], f"ext=m4a missing for vfmt={vfmt!r}"

    def test_format_selector_uses_webm_streams_for_webm_vfmt(self) -> None:
        """Webm target requests VP9/Opus streams to avoid codec mismatch on remux."""
        opts = _run_fallback_and_capture_options("webm", "m4a", {})
        assert "ext=webm" in opts["format"]
        assert "ext=mp4" not in opts["format"]

    def test_default_vfmt_mp4_sets_merge_output_format(self) -> None:
        """Default vfmt='mp4' sets merge_output_format to 'mp4' (regression guard)."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert opts["merge_output_format"] == "mp4"
        assert "ext=mp4" in opts["format"]

    def test_format_string_contains_height_720(self) -> None:
        """The fallback must request 720p height, not some other resolution."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert "height<=720" in opts["format"]

    def test_format_selector_uses_mp4_even_when_vfmt_is_mkv(self) -> None:
        """Format selector stays ext=mp4 even when vfmt='mkv'; container is mkv via merge_output_format."""
        opts = _run_fallback_and_capture_options("mkv", "m4a", {})
        assert "ext=mp4" in opts["format"]
        assert opts["merge_output_format"] == "mkv"

    def test_format_selector_uses_m4a_regardless_of_afmt(self) -> None:
        """Audio selector in video format string always uses ext=m4a (native YT stream)."""
        opts = _run_fallback_and_capture_options("mp4", "opus", {})
        assert "ext=m4a" in opts["format"]

    def test_format_string_has_generic_fallback_tiers(self) -> None:
        """The format string must include bare 'bestvideo*[height<=720]+bestaudio' fallback tier."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        # The second and third tiers have no ext= constraint — verify they're present
        assert "bestvideo*[height<=720]+bestaudio/" in opts["format"]
        assert "best[height<=720]" in opts["format"]
        # ...and an unconstrained merge tier, so sites that publish only
        # video-only + audio-only renditions still have a reachable catch-all.
        assert opts["format"].endswith("bestvideo*+bestaudio/best")


# ===========================================================================
# 3. merge_output_format — setdefault behaviour
# ===========================================================================


class TestMergeOutputFormat:
    """setdefault('merge_output_format', vfmt) must set when absent, not override when present."""

    def test_merge_output_format_set_to_vfmt_when_absent(self) -> None:
        """If opts has no 'merge_output_format', the fallback dict gets vfmt."""
        opts = _run_fallback_and_capture_options("mkv", "m4a", {})
        assert opts["merge_output_format"] == "mkv"

    def test_merge_output_format_not_overridden_when_already_present(self) -> None:
        """Existing 'merge_output_format' in opts must NOT be changed by setdefault."""
        base_opts = {"merge_output_format": "webm"}
        opts = _run_fallback_and_capture_options("mkv", "m4a", base_opts)
        # The existing "webm" must be preserved; setdefault must not overwrite it with "mkv"
        assert opts["merge_output_format"] == "webm"

    def test_merge_output_format_matches_vfmt_default_mp4(self) -> None:
        """With default vfmt='mp4', merge_output_format should be 'mp4'."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert opts["merge_output_format"] == "mp4"

    def test_merge_output_format_not_hardcoded_mp4_when_vfmt_is_webm(self) -> None:
        """merge_output_format must be 'webm', not the old hardcoded 'mp4'."""
        opts = _run_fallback_and_capture_options("webm", "m4a", {})
        assert opts["merge_output_format"] == "webm"
        assert opts["merge_output_format"] != "mp4"


# ===========================================================================
# 4. None / empty string fallback guard — `or "mp4"` / `or "m4a"`
# ===========================================================================


class TestFallbackToDefaults:
    """None and empty string settings must produce 'mp4'/'m4a', never 'None' or ''."""

    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_none_or_empty_vfmt_falls_back_to_mp4_in_format(self, vfmt: Any) -> None:
        opts = _run_fallback_and_capture_options(vfmt, "m4a", {})
        assert "ext=mp4" in opts["format"]
        assert "None" not in opts["format"]
        assert "ext=" not in opts["format"].replace("ext=mp4", "").replace(
            "ext=m4a", ""
        )  # no other ext= fragments with garbage values

    @pytest.mark.parametrize("afmt", [None, ""])
    def test_none_or_empty_afmt_falls_back_to_m4a_in_format(self, afmt: Any) -> None:
        opts = _run_fallback_and_capture_options("mp4", afmt, {})
        assert "ext=m4a" in opts["format"]
        assert "None" not in opts["format"]

    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_none_or_empty_vfmt_falls_back_to_mp4_for_merge_output_format(self, vfmt: Any) -> None:
        opts = _run_fallback_and_capture_options(vfmt, "m4a", {})
        assert opts["merge_output_format"] == "mp4"
        assert opts["merge_output_format"] is not None
        assert opts["merge_output_format"] != ""

    def test_both_none_produces_valid_format_string(self) -> None:
        """Both settings None → both fall back to defaults; format string must be non-empty."""
        opts = _run_fallback_and_capture_options(None, None, {})
        assert opts["format"]
        assert "None" not in opts["format"]
        assert opts["merge_output_format"] == "mp4"

    def test_both_empty_produces_valid_format_string(self) -> None:
        """Both settings empty string → both fall back to defaults."""
        opts = _run_fallback_and_capture_options("", "", {})
        assert opts["format"]
        assert "None" not in opts["format"]
        assert opts["merge_output_format"] == "mp4"


# ===========================================================================
# 5. opts isolation — fallback dict is a copy, not the same object
# ===========================================================================


class TestOptsCopyIsolation:
    """_modify() must return a copy; the original opts must not be mutated."""

    def test_original_opts_not_mutated_by_modify(self) -> None:
        """Base opts must not gain 'format' or 'merge_output_format' after fallback runs."""
        original_opts: dict = {}

        with (
            _patch_get_setting("mp4", "m4a"),
            patch("src.download_executor.YoutubeDL") as mock_ydl_class,
        ):
            mock_ydl_class.return_value.__enter__.return_value = MagicMock()
            executor = _make_executor()
            executor._try_720_fallback(
                ["url"],
                original_opts,
                "title",
                "youtube",
                _TRIGGER,
            )

        # The original opts must not have been mutated
        assert "format" not in original_opts
        assert "merge_output_format" not in original_opts

    def test_tried_flag_set_only_on_fallback_copy_not_original(self) -> None:
        """_tried_720_fallback flag is set on the fallback copy, not the caller's dict."""
        original_opts: dict = {}

        with (
            _patch_get_setting("mp4", "m4a"),
            patch("src.download_executor.YoutubeDL") as mock_ydl_class,
        ):
            mock_ydl_class.return_value.__enter__.return_value = MagicMock()
            executor = _make_executor()
            executor._try_720_fallback(
                ["url"],
                original_opts,
                "title",
                "youtube",
                _TRIGGER,
            )

        assert "_tried_720_fallback" not in original_opts


# ===========================================================================
# 6. qmeta propagation — type field set to "720"
# ===========================================================================


class TestQmetaPropagation:
    """_modify must set qmeta['type'] = '720' in the fallback dict."""

    def test_qmeta_type_set_to_720(self) -> None:
        """Fallback options must carry qmeta.type == '720'."""
        opts = _run_fallback_and_capture_options(
            "mp4", "m4a", {"qmeta": {"site": "youtube", "type": "1080"}}
        )
        assert opts.get("qmeta", {}).get("type") == "720"

    def test_qmeta_type_set_to_720_when_no_existing_qmeta(self) -> None:
        """When base opts has no qmeta, the fallback still gets qmeta.type == '720'."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert opts.get("qmeta", {}).get("type") == "720"

    def test_qmeta_other_fields_preserved(self) -> None:
        """Existing qmeta fields (e.g. 'site') must not be dropped."""
        opts = _run_fallback_and_capture_options(
            "mp4", "m4a", {"qmeta": {"site": "nebula", "type": "1080"}}
        )
        assert opts.get("qmeta", {}).get("site") == "nebula"


# ===========================================================================
# 7. Live setting read — get_setting is called inside _modify, not at call-time
# ===========================================================================


class TestLiveSettingRead:
    """get_setting must be called fresh inside _modify so late changes take effect."""

    def test_get_setting_called_during_modify_not_at_outer_call_time(self) -> None:
        """Verify get_setting is invoked when the download attempt fires, not earlier."""
        call_log: list[str] = []

        def _tracking_get_setting(key: str) -> str:
            call_log.append(key)
            if key == "VID_DL_VIDEO_FORMAT":
                return "flac"  # deliberately unusual value
            if key == "VID_DL_AUDIO_FORMAT":
                return "aac"
            return ""

        with (
            patch(
                "src.download_executor.get_setting",
                side_effect=_tracking_get_setting,
            ),
            patch("src.download_executor.YoutubeDL") as mock_ydl_class,
        ):
            mock_ydl_class.return_value.__enter__.return_value = MagicMock()
            executor = _make_executor()
            executor._try_720_fallback(
                ["url"],
                {},
                "title",
                "youtube",
                _TRIGGER,
            )

        # get_setting must be called for the video format key inside _modify (lazy read)
        assert "VID_DL_VIDEO_FORMAT" in call_log


# ===========================================================================
# 8. Message callback fires with title in the fallback message
# ===========================================================================


class TestFallbackMessage:
    """The message emitted on fallback must mention '720' and the video title."""

    def test_callback_message_contains_720(self) -> None:
        messages: list[str] = []

        with (
            _patch_get_setting("mp4", "m4a"),
            patch("src.download_executor.YoutubeDL") as mock_ydl_class,
        ):
            mock_ydl_class.return_value.__enter__.return_value = MagicMock()
            executor = DownloadExecutor(message_callback=messages.append)
            executor._try_720_fallback(
                ["url"],
                {},
                "My Video Title",
                "youtube",
                _TRIGGER,
            )

        assert len(messages) == 1
        assert "720" in messages[0]

    def test_callback_message_contains_title(self) -> None:
        messages: list[str] = []

        with (
            _patch_get_setting("mp4", "m4a"),
            patch("src.download_executor.YoutubeDL") as mock_ydl_class,
        ):
            mock_ydl_class.return_value.__enter__.return_value = MagicMock()
            executor = DownloadExecutor(message_callback=messages.append)
            executor._try_720_fallback(
                ["url"],
                {},
                "My Video Title",
                "youtube",
                _TRIGGER,
            )

        assert "My Video Title" in messages[0]


# ===========================================================================
# 9. FFmpegVideoRemuxer postprocessor in _try_720_fallback._modify()
# ===========================================================================


def _find_remuxer_in_fallback(opts: dict) -> dict | None:
    """Return the FFmpegVideoRemuxer postprocessor entry from opts, or None."""
    return next(
        (pp for pp in opts.get("postprocessors", []) if pp.get("key") == "FFmpegVideoRemuxer"),
        None,
    )


class TestRemuxvideoKeyInFallback:
    """
    Guard the FFmpegVideoRemuxer postprocessor inside _try_720_fallback._modify().

    yt-dlp's Python API ignores bare params like 'remuxvideo'; _modify must
    append {"key": "FFmpegVideoRemuxer", "preferedformat": vfmt} to the
    postprocessors list so pre-muxed 720p downloads are remuxed correctly.
    """

    def test_remuxvideo_present_in_fallback_opts(self) -> None:
        """FFmpegVideoRemuxer must appear in the postprocessors of the 720p fallback dict."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert _find_remuxer_in_fallback(opts) is not None, (
            "FFmpegVideoRemuxer missing from postprocessors in 720p fallback options"
        )

    def test_remux_video_old_key_absent_in_fallback_opts(self) -> None:
        """Neither 'remux_video' nor 'remuxvideo' must appear as top-level keys."""
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        assert "remux_video" not in opts, (
            "Old misspelled key 'remux_video' found in 720p fallback options"
        )
        assert "remuxvideo" not in opts, (
            "CLI-only key 'remuxvideo' found as top-level param in 720p fallback options"
        )

    @pytest.mark.parametrize("vfmt", ["mp4", "mkv", "webm"])
    def test_remuxvideo_value_matches_vfmt(self, vfmt: str) -> None:
        """FFmpegVideoRemuxer preferedformat must equal vfmt read from settings at fallback time."""
        opts = _run_fallback_and_capture_options(vfmt, "m4a", {})
        remuxer = _find_remuxer_in_fallback(opts)
        assert remuxer is not None
        assert remuxer["preferedformat"] == vfmt, (
            f"preferedformat={remuxer['preferedformat']!r}, expected {vfmt!r}"
        )

    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_remuxvideo_falls_back_to_mp4_when_vfmt_falsy(self, vfmt: Any) -> None:
        """Falsy vfmt must produce FFmpegVideoRemuxer preferedformat='mp4'."""
        opts = _run_fallback_and_capture_options(vfmt, "m4a", {})
        remuxer = _find_remuxer_in_fallback(opts)
        assert remuxer is not None
        assert remuxer["preferedformat"] == "mp4", (
            f"preferedformat={remuxer['preferedformat']!r} for vfmt={vfmt!r}"
        )

    def test_remuxvideo_independent_of_merge_output_format_preset(self) -> None:
        """FFmpegVideoRemuxer is always appended even when merge_output_format was pre-set."""
        base_opts = {"merge_output_format": "webm"}
        opts = _run_fallback_and_capture_options("mkv", "m4a", base_opts)
        assert opts["merge_output_format"] == "webm"
        remuxer = _find_remuxer_in_fallback(opts)
        assert remuxer is not None
        assert remuxer["preferedformat"] == "mkv"

    def test_remuxer_preferedformat_typo_is_correct_ydl_key(self) -> None:
        """
        Guard the intentional 'preferedformat' spelling used by yt-dlp.

        yt-dlp uses 'preferedformat' (one 'r') — not 'preferredformat'.
        If the key is correctly spelled, yt-dlp silently ignores it.
        """
        opts = _run_fallback_and_capture_options("mp4", "m4a", {})
        remuxer = _find_remuxer_in_fallback(opts)
        assert remuxer is not None
        assert "preferedformat" in remuxer, (
            f"Key 'preferedformat' missing from remuxer: {remuxer!r}"
        )
        assert "preferredformat" not in remuxer, (
            "Correctly-spelled key 'preferredformat' found — yt-dlp ignores it; "
            "use the intentionally misspelled 'preferedformat'"
        )

    def test_no_duplicate_remuxer_when_base_opts_already_has_remuxer(self) -> None:
        """If base_opts already has FFmpegVideoRemuxer, _modify() must not add a second one."""
        base_opts = {"postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}
        opts = _run_fallback_and_capture_options("mp4", "m4a", base_opts)
        remuxers = [
            pp for pp in opts.get("postprocessors", []) if pp.get("key") == "FFmpegVideoRemuxer"
        ]
        assert len(remuxers) == 1, (
            f"Expected exactly 1 remuxer, got {len(remuxers)}. "
            "Dedup guard in _modify() may be broken."
        )

    def test_remuxer_is_last_postprocessor_in_fallback(self) -> None:
        """FFmpegVideoRemuxer must be the final entry so it runs after SponsorBlock."""
        base_opts = {
            "postprocessors": list(
                __import__(
                    "src.dict_utils", fromlist=["DEFAULT_POSTPROCESSORS"]
                ).DEFAULT_POSTPROCESSORS
            )
        }
        opts = _run_fallback_and_capture_options("mp4", "m4a", base_opts)
        pps = opts.get("postprocessors", [])
        assert pps, "postprocessors list is empty"
        assert pps[-1].get("key") == "FFmpegVideoRemuxer", (
            f"Last postprocessor is {pps[-1].get('key')!r}, expected 'FFmpegVideoRemuxer'"
        )

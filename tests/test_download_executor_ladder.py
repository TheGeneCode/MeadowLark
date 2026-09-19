"""
Tests for _try_lower_rung_fallback and execute's ladder dispatch.

Covers resolution-ladder descent when a requested rung is gated, including
enabled-rung filtering, descent capping, and qmeta propagation.

Patch targets:
  - src.download_executor.YoutubeDL: context manager mock, records opts
  - src.download_executor.get_setting: returns vfmt for VID_DL_VIDEO_FORMAT
  - src.download_executor.ENABLED_RESOLUTIONS: list of rungs the user has turned on
  - src.download_executor.MAX_LADDER_DESCENT: cap on descent depth (usually 3)
"""

from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from src.download_executor import DownloadExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor() -> DownloadExecutor:
    """Return a fresh executor with a no-op message callback."""
    return DownloadExecutor(message_callback=lambda _: None)


def _patch_get_setting(vfmt: object) -> object:
    """Patch src.download_executor.get_setting to return vfmt for VID_DL_VIDEO_FORMAT."""

    def _fake_get_setting(key: str) -> object:
        if key == "VID_DL_VIDEO_FORMAT":
            return vfmt
        return None

    return patch("src.download_executor.get_setting", side_effect=_fake_get_setting)


class _CallTrackingYDL:
    """Mock YoutubeDL that tracks download calls and can be made to fail."""

    def __init__(self, opts: dict) -> None:
        """Record the options this instance was created with."""
        self.opts_list.append(opts)
        self._mock_instance = MagicMock()
        self._should_raise = False

    def __enter__(self) -> MagicMock:
        return self._mock_instance

    def __exit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Test fixtures for controlling YDL behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def call_tracking_ydl():
    """Fixture that provides a call-tracking YDL mock."""
    _CallTrackingYDL.opts_list = []
    _CallTrackingYDL.raise_on_call = 0  # how many calls to fail before succeeding

    def _make_ydl(opts: dict) -> _CallTrackingYDL:
        instance = _CallTrackingYDL(opts)
        if _CallTrackingYDL.raise_on_call > 0:
            _CallTrackingYDL.raise_on_call -= 1
            # Patch the download method to raise
            instance._mock_instance.download.side_effect = DownloadError(
                "HTTP Error 403: Forbidden"
            )
        return instance

    return _make_ydl


# ===========================================================================
# 1. test_descends_to_next_enabled_rung
# ===========================================================================


class TestDescentFlow:
    """Test basic descent to the next enabled rung."""

    def test_descends_to_next_enabled_rung(self, call_tracking_ydl) -> None:
        """First attempt fails; second attempt at lower enabled rung succeeds."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 1  # Fail first call, succeed second

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is True
        assert error == ""
        assert len(_CallTrackingYDL.opts_list) == 2, (
            f"Expected 2 downloads, got {len(_CallTrackingYDL.opts_list)}"
        )
        # First retry to 1080, second retry to 720 (next enabled below 2160, then below 1080)
        assert "height<=1080" in _CallTrackingYDL.opts_list[0]["format"]
        assert "height<=720" in _CallTrackingYDL.opts_list[1]["format"]

    # ===========================================================================
    # 2. test_skips_disabled_rungs
    # ===========================================================================

    def test_skips_disabled_rungs(self, call_tracking_ydl) -> None:
        """Only 1080 and 360 enabled; 1080 gated; retries only 360."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all calls

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 360)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        assert success is False
        # Only one retry (to 360); 1440 and 720 are skipped
        assert len(_CallTrackingYDL.opts_list) == 1
        assert "height<=360" in _CallTrackingYDL.opts_list[0]["format"]

    # ===========================================================================
    # 3. test_descent_capped_at_max
    # ===========================================================================

    def test_descent_capped_at_max(self, call_tracking_ydl) -> None:
        """All six rungs enabled; descent capped at MAX_LADDER_DESCENT (3)."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all calls

        with (
            patch(
                "src.download_executor.ENABLED_RESOLUTIONS",
                (2160, 1440, 1080, 720, 480, 360),
            ),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is False
        # MAX_LADDER_DESCENT is 3, so exactly 3 retries after initial
        assert len(_CallTrackingYDL.opts_list) == 3
        # Rungs should be 1440, 1080, 720
        formats = [opts["format"] for opts in _CallTrackingYDL.opts_list]
        assert "height<=1440" in formats[0]
        assert "height<=1080" in formats[1]
        assert "height<=720" in formats[2]

    # ===========================================================================
    # 4. test_lowest_rung_makes_no_attempt
    # ===========================================================================

    def test_lowest_rung_makes_no_attempt(self, call_tracking_ydl) -> None:
        """Lowest rung (360) gated; no rungs below it; no retry attempted."""
        _CallTrackingYDL.opts_list = []

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1440, 1080, 720, 480, 360)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                360,
            )

        assert success is False
        # No retries attempted since 360 is the lowest rung
        assert len(_CallTrackingYDL.opts_list) == 0


# ===========================================================================
# 5. test_non_gating_error_does_not_retry
# ===========================================================================


class TestTriggerGuards:
    """Test that non-gating errors do not trigger the ladder."""

    def test_non_gating_error_does_not_retry(self, call_tracking_ydl) -> None:
        """SponsorBlock error (non-gating); no retry attempted."""
        _CallTrackingYDL.opts_list = []

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            error_str = "Unable to communicate with SponsorBlock API"
            success, returned_error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                error_str,
                1080,
            )

        assert success is False
        assert returned_error == error_str
        # No retry because error does not match gating trigger phrases
        assert len(_CallTrackingYDL.opts_list) == 0

    # ===========================================================================
    # 6. test_stops_when_error_changes_to_non_gating
    # ===========================================================================

    def test_stops_when_error_changes_to_non_gating(self, call_tracking_ydl) -> None:
        """First retry raises non-gating error; loop stops without further retries."""
        _CallTrackingYDL.opts_list = []

        def _make_ydl_with_custom_failure(opts: dict) -> _CallTrackingYDL:
            instance = _CallTrackingYDL(opts)
            # First call (to 1440) raises non-gating error
            if len(_CallTrackingYDL.opts_list) == 1:
                instance._mock_instance.download.side_effect = DownloadError(
                    "Unable to communicate with SponsorBlock API"
                )
            return instance

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1440, 1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=_make_ydl_with_custom_failure),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is False
        # Only 1 retry attempted because error changed to non-gating
        assert len(_CallTrackingYDL.opts_list) == 1
        assert "Unable to communicate with SponsorBlock API" in error


# ===========================================================================
# 7. test_caller_options_not_mutated
# ===========================================================================


class TestOptionsMutation:
    """Test that the caller's options dict is never mutated."""

    def test_caller_options_not_mutated(self, call_tracking_ydl) -> None:
        """Original options dict unchanged after failed retries."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        original_opts = {"some_key": "some_value"}
        original_format = original_opts.get("format")

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                original_opts,
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        # Original dict must not be mutated
        assert original_opts == {"some_key": "some_value"}
        assert "format" not in original_opts
        assert "_tried_rung_720_fallback" not in original_opts
        assert original_opts.get("format") == original_format


# ===========================================================================
# 8. test_qmeta_type_records_actual_rung
# ===========================================================================


class TestQmetaPropagation:
    """Test that qmeta['type'] records the rung that succeeded."""

    def test_qmeta_type_records_actual_rung(self, call_tracking_ydl) -> None:
        """
        Requested 2160; first retry (1080) fails, second retry (720) succeeds.

        qmeta['type'] must equal '720'.
        """
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 1  # Fail first retry (1080), succeed second (720)

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is True
        # The second (successful) call should have qmeta['type'] == '720'
        successful_opts = _CallTrackingYDL.opts_list[1]
        assert successful_opts.get("qmeta", {}).get("type") == "720"

    # ===========================================================================
    # 9. test_qmeta_absent_does_not_raise
    # ===========================================================================

    def test_qmeta_absent_does_not_raise(self, call_tracking_ydl) -> None:
        """Options with no qmeta key; retry succeeds; no KeyError."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = (
            0  # Succeed immediately (only one rung available below 1080)
        )

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            # Pass options with no qmeta
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        assert success is True
        # Retry must have created qmeta with type
        successful_opts = _CallTrackingYDL.opts_list[0]
        assert successful_opts.get("qmeta", {}).get("type") == "720"


# ===========================================================================
# 10. test_execute_runs_ladder_for_playlist_source
# ===========================================================================


class TestExecuteDispatch:
    """Test execute() integration with the ladder."""

    def test_execute_runs_ladder_for_playlist_source(self, call_tracking_ydl) -> None:
        """execute() with playlist source; initial 403; retry succeeds."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 1  # Fail first, succeed second

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {"type": "1080playlists"}}
            success, error = executor.execute(
                ["https://example.com/v"],
                options,
            )

        # Should succeed on the fallback
        assert success is True
        assert error == ""

    # ===========================================================================
    # 11. test_execute_skips_ladder_for_audio
    # ===========================================================================

    def test_execute_skips_ladder_for_audio(self, call_tracking_ydl) -> None:
        """execute() with audio source; no ladder attempted; falls through."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {"type": "audio_playlists"}}
            success, error = executor.execute(
                ["https://example.com/v"],
                options,
            )

        # Should fail (no ladder, no SponsorBlock available in test)
        assert success is False
        # Error message should mention audio type
        assert "audio_playlists" in error or "audio" in error.lower()

    # ===========================================================================
    # 12. test_execute_skips_ladder_for_unknown_type
    # ===========================================================================

    def test_execute_skips_ladder_for_unknown_type(self, call_tracking_ydl) -> None:
        """execute() with unknown type; no ladder attempted."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {}}  # No "type" key → defaults to "unknown"
            success, error = executor.execute(
                ["https://example.com/v"],
                options,
            )

        # Should fail (no ladder for unknown type)
        assert success is False


# ===========================================================================
# 13. test_webm_container_propagates_through_ladder
# ===========================================================================


class TestContainerPropagation:
    """Test that container format settings propagate through ladder."""

    def test_webm_container_propagates_through_ladder(self, call_tracking_ydl) -> None:
        """VID_DL_VIDEO_FORMAT='webm'; retry format has [ext=webm]; merge and remuxer match."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 0  # Succeed on first retry (only one rung below 1080)

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("webm"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        assert success is True
        # First (successful) retry should have webm format settings
        fallback_opts = _CallTrackingYDL.opts_list[0]
        assert "[ext=webm]" in fallback_opts["format"]
        assert fallback_opts["merge_output_format"] == "webm"
        remuxer = next(
            (
                pp
                for pp in fallback_opts.get("postprocessors", [])
                if pp.get("key") == "FFmpegVideoRemuxer"
            ),
            None,
        )
        assert remuxer is not None
        assert remuxer["preferedformat"] == "webm"


# ===========================================================================
# 14. test_remuxer_not_duplicated
# ===========================================================================


class TestRemuxerDedup:
    """Test that remuxer is not duplicated when already present."""

    def test_remuxer_not_duplicated(self, call_tracking_ydl) -> None:
        """Base opts already has FFmpegVideoRemuxer; fallback has exactly one."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 0  # Succeed on first retry

        base_opts = {"postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                base_opts,
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        assert success is True
        # First (successful) retry should have exactly one remuxer
        fallback_opts = _CallTrackingYDL.opts_list[0]
        remuxers = [
            pp
            for pp in fallback_opts.get("postprocessors", [])
            if pp.get("key") == "FFmpegVideoRemuxer"
        ]
        assert len(remuxers) == 1, f"Expected 1 remuxer, got {len(remuxers)}"


# ===========================================================================
# 15. test_only_requested_height_enabled_still_descends (integration-level
#     coverage of resolutions.lower_heights' empty-restriction fallback,
#     wired through the download executor rather than unit-tested in
#     isolation)
# ===========================================================================


class TestEnabledRestrictionFallbackIntegration:
    """The 'every rung below is disabled' fallback, exercised end-to-end."""

    def test_only_requested_height_enabled_still_descends(self, call_tracking_ydl) -> None:
        """
        ENABLED_RESOLUTIONS=(2160,) — nothing below 2160 is enabled.

        The ladder must fall back to the full unrestricted descent rather
        than making zero attempts (resolutions.lower_heights' documented
        invariant: a user who enabled only the top rung still gets a working
        retry ladder).
        """
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160,)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is False
        # Falls back to the unrestricted ladder, still capped at MAX_LADDER_DESCENT
        assert len(_CallTrackingYDL.opts_list) == 3
        formats = [opts["format"] for opts in _CallTrackingYDL.opts_list]
        assert "height<=1440" in formats[0]
        assert "height<=1080" in formats[1]
        assert "height<=720" in formats[2]

    def test_empty_enabled_resolutions_still_descends(self, call_tracking_ydl) -> None:
        """
        ENABLED_RESOLUTIONS=() (e.g. config failed to load anything).

        Must behave like an unrestricted ladder, not strand the retry with
        zero attempts.
        """
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 0  # Succeed immediately

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", ()),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                1080,
            )

        assert success is True
        assert "height<=720" in _CallTrackingYDL.opts_list[0]["format"]


# ===========================================================================
# 16. test_descent_cap_of_one / test_descent_cap_of_zero — MAX_LADDER_DESCENT
#     boundary values tighter than what test 3 already covers (cap smaller
#     than available rungs, and cap of zero).
# ===========================================================================


class TestMaxLadderDescentBoundary:
    """MAX_LADDER_DESCENT boundary values below the default of 3."""

    def test_descent_cap_of_one_allows_single_retry(self, call_tracking_ydl) -> None:
        """MAX_LADDER_DESCENT=1 with 3 enabled rungs below: only 1 attempt made."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1440, 1080, 720)),
            patch("src.download_executor.MAX_LADDER_DESCENT", 1),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is False
        assert len(_CallTrackingYDL.opts_list) == 1
        assert "height<=1440" in _CallTrackingYDL.opts_list[0]["format"]

    def test_descent_cap_of_zero_makes_no_attempt(self, call_tracking_ydl) -> None:
        """
        MAX_LADDER_DESCENT=0 must make zero retry attempts.

        Even though lower rungs exist and are enabled — guards against the
        slice `descent[:MAX_LADDER_DESCENT]` silently misbehaving on the
        boundary value (a negative cap would drop from the end instead of
        returning empty; 0 is the smallest value that must produce an empty
        descent).
        """
        _CallTrackingYDL.opts_list = []

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1440, 1080, 720)),
            patch("src.download_executor.MAX_LADDER_DESCENT", 0),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                {},
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is False
        assert error == "HTTP Error 403: Forbidden"
        assert len(_CallTrackingYDL.opts_list) == 0


# ===========================================================================
# 17. test_execute_skips_ladder_for_update_type — the fourth explicitly-named
#     non-resolution source ("Update") that must skip the ladder, distinct
#     from "audio"/"audio_playlists" (already covered) and "unknown"
#     (already covered).
# ===========================================================================


class TestExecuteDispatchAdditionalTypes:
    """Additional execute() dispatch boundary values not covered by tests 10-12."""

    def test_execute_skips_ladder_for_update_type(self, call_tracking_ydl) -> None:
        """Dtype == 'Update' must skip the ladder (height_from_source returns None)."""
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {"type": "Update"}}
            success, error = executor.execute(
                ["https://example.com/v"],
                options,
            )

        assert success is False
        # No ladder retry was attempted: no call carries a rung tried-flag or
        # a rewritten format selector (the title-extraction lookup that runs
        # after the failed download is a separate, unrelated YoutubeDL call).
        assert not any(
            key.startswith("_tried_rung_") for opts in _CallTrackingYDL.opts_list for key in opts
        )
        assert not any("format" in opts for opts in _CallTrackingYDL.opts_list)

    def test_execute_skips_ladder_for_unregistered_height(self, call_tracking_ydl) -> None:
        """
        Dtype names a height that parses as an int but isn't registered.

        height_from_source must reject it (e.g. '144') and execute() must
        skip the ladder rather than descending from an unregistered height.
        """
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 999  # Fail all

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {"type": "144"}}
            success, error = executor.execute(
                ["https://example.com/v"],
                options,
            )

        assert success is False
        # No ladder retry: '144' parses as an int but isn't a registered rung,
        # so height_from_source must reject it before any descent is attempted.
        assert not any(
            key.startswith("_tried_rung_") for opts in _CallTrackingYDL.opts_list for key in opts
        )
        assert not any("format" in opts for opts in _CallTrackingYDL.opts_list)


# ===========================================================================
# 18. test_execute_falls_through_ladder_to_sponsorblock_retry — the full
#     three-stage chain (initial attempt -> ladder exhausts with the last
#     error now non-gating -> SponsorBlock-removal retry succeeds), which no
#     existing test drives through execute() itself (tests 5/6 exercise the
#     early-exit only at the _try_lower_rung_fallback layer).
# ===========================================================================


class TestExecuteFullChain:
    """execute()'s three-stage fallback chain, ladder then SponsorBlock."""

    def test_execute_falls_through_ladder_to_sponsorblock_retry(self) -> None:
        """
        Initial attempt is gated (403).

        The one enabled rung below it (720) fails with a SponsorBlock error
        instead of a gating one, so the ladder stops (per test 6's
        invariant) and execute() must still attempt the SponsorBlock-removal
        fallback with that updated error text, and succeed.
        """
        calls: list[dict] = []

        class _SequencedYDL:
            def __init__(self, opts: dict) -> None:
                calls.append(opts)
                self._mock_instance = MagicMock()
                if len(calls) == 1:
                    self._mock_instance.download.side_effect = DownloadError(
                        "HTTP Error 403: Forbidden"
                    )
                elif len(calls) == 2:
                    self._mock_instance.download.side_effect = DownloadError(
                        "Unable to communicate with SponsorBlock API"
                    )
                # Third call (SponsorBlock-removal retry): succeeds.

            def __enter__(self) -> MagicMock:
                return self._mock_instance

            def __exit__(self, *_: object) -> None:
                pass

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=_SequencedYDL),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            options = {"qmeta": {"type": "1080"}}
            success, error = executor.execute(["https://example.com/v"], options)

        assert success is True
        assert error == ""
        assert len(calls) == 3


# ===========================================================================
# 19. test_stale_tried_flag_on_one_rung_skips_only_that_rung — the per-rung
#     tried-flag design point (docstring: "a shared flag would collapse the
#     whole ladder into one attempt"), proven from the opposite direction: a
#     caller-supplied options dict that already carries ONE rung's tried flag
#     (e.g. reused from an earlier execute() call) must skip only that rung,
#     not the whole ladder.
# ===========================================================================


class TestPerRungTriedFlag:
    """A pre-existing tried-flag for one rung must not block other rungs."""

    def test_stale_tried_flag_on_one_rung_skips_only_that_rung(self, call_tracking_ydl) -> None:
        """
        Options already has _tried_rung_1440_fallback=True.

        Descent from 2160 must skip straight to 1080 (still attempting it),
        not treat the whole ladder as already tried.
        """
        _CallTrackingYDL.opts_list = []
        _CallTrackingYDL.raise_on_call = 0  # First attempt made (1080) succeeds

        preseeded_opts = {"_tried_rung_1440_fallback": True}

        with (
            patch("src.download_executor.ENABLED_RESOLUTIONS", (2160, 1440, 1080, 720)),
            patch("src.download_executor.YoutubeDL", side_effect=call_tracking_ydl),
            _patch_get_setting("mp4"),
        ):
            executor = _make_executor()
            success, error = executor._try_lower_rung_fallback(
                ["https://example.com/v"],
                preseeded_opts,
                "Test Title",
                "HTTP Error 403: Forbidden",
                2160,
            )

        assert success is True
        # 1440 was skipped (already "tried"); the first real attempt is 1080
        assert len(_CallTrackingYDL.opts_list) == 1
        assert "height<=1080" in _CallTrackingYDL.opts_list[0]["format"]

"""Unit tests for src.match_filter.build_match_filter."""

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from src.match_filter import build_match_filter


def _make_mf(source: str = "1080playlists") -> Callable[[dict, bool], str | None]:
    return build_match_filter(source, MagicMock(), MagicMock())


def test_mf_does_not_skip_needs_auth() -> None:
    mf = _make_mf()
    result = mf({"availability": "needs_auth"}, False)
    assert result is None


def test_mf_skips_scheduled() -> None:
    mf = _make_mf()
    result = mf({"availability": "scheduled"}, False)
    assert result == "Skipping: scheduled"


def test_mf_none_info_calls_log_exception_and_returns_none() -> None:
    mf = _make_mf()
    with patch("utils.log_exception") as mock_log:
        result = mf(None, False)  # type: ignore[arg-type]
    assert result is None
    mock_log.assert_called_once()


def test_mf_normal_video_returns_none() -> None:
    mf = _make_mf()
    result = mf({"is_live": False, "live_status": "not_live", "availability": "public"}, False)
    assert result is None


def test_mf_is_live_true_queues_url_and_returns_skip_message() -> None:
    add_fn = MagicMock()
    log_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, log_fn)
    result = mf(
        {
            "is_live": True,
            "webpage_url": "https://youtube.com/watch?v=abc",
            "live_status": "is_live",
        },
        False,
    )
    assert result == "Skipping live; queued for later"
    add_fn.assert_called_once_with("https://youtube.com/watch?v=abc", "1080playlists", None)
    log_fn.assert_called_once()


def test_mf_is_upcoming_queues_and_returns_skip_message() -> None:
    add_fn = MagicMock()
    mf = build_match_filter("720playlists", add_fn, MagicMock())
    result = mf(
        {
            "is_live": False,
            "live_status": "is_upcoming",
            "webpage_url": "https://youtube.com/watch?v=upcoming",
        },
        False,
    )
    assert result == "Skipping live; queued for later"
    add_fn.assert_called_once()


def test_mf_live_without_any_url_does_not_call_add_fn() -> None:
    """When no url key is populated in the info dict, add_to_queue must not be called."""
    add_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, MagicMock())
    result = mf({"is_live": True, "live_status": "is_live"}, False)
    assert result == "Skipping live; queued for later"
    add_fn.assert_not_called()


def test_mf_live_falls_back_to_original_url() -> None:
    add_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, MagicMock())
    mf(
        {
            "is_live": True,
            "live_status": "is_live",
            "original_url": "https://youtube.com/watch?v=orig",
        },
        False,
    )
    add_fn.assert_called_once_with("https://youtube.com/watch?v=orig", "1080playlists", None)


def test_mf_live_falls_back_to_url_key() -> None:
    add_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, MagicMock())
    mf(
        {
            "is_live": True,
            "live_status": "is_live",
            "url": "https://youtube.com/watch?v=fallback",
        },
        False,
    )
    add_fn.assert_called_once_with("https://youtube.com/watch?v=fallback", "1080playlists", None)


def test_mf_add_fn_raises_os_error_returns_none_and_logs() -> None:
    """Exception from add_to_queue_fn must be swallowed and logged, not propagated."""
    add_fn = MagicMock(side_effect=OSError("disk full"))
    log_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, log_fn)
    with patch("utils.log_exception") as mock_log:
        result = mf(
            {
                "is_live": True,
                "live_status": "is_live",
                "webpage_url": "https://youtube.com/watch?v=abc",
            },
            False,
        )
    assert result is None
    mock_log.assert_called_once()


def test_mf_log_fn_raises_runtime_error_returns_none_and_logs() -> None:
    """Exception from log_fn must be swallowed and logged, not propagated."""
    add_fn = MagicMock()
    log_fn = MagicMock(side_effect=RuntimeError("signal destroyed"))
    mf = build_match_filter("1080playlists", add_fn, log_fn)
    with patch("utils.log_exception") as mock_log:
        result = mf(
            {
                "is_live": True,
                "live_status": "is_live",
                "webpage_url": "https://youtube.com/watch?v=abc",
            },
            False,
        )
    assert result is None
    mock_log.assert_called_once()


def test_mf_playlist_id_passed_to_add_fn() -> None:
    add_fn = MagicMock()
    mf = build_match_filter("720playlists", add_fn, MagicMock())
    mf(
        {
            "is_live": True,
            "live_status": "is_live",
            "webpage_url": "https://youtube.com/watch?v=abc",
            "playlist_id": "PLxyz123",
        },
        False,
    )
    add_fn.assert_called_once_with("https://youtube.com/watch?v=abc", "720playlists", "PLxyz123")


def test_mf_empty_dict_returns_none() -> None:
    mf = _make_mf()
    result = mf({}, False)
    assert result is None


def test_mf_availability_public_is_not_skipped() -> None:
    mf = _make_mf()
    result = mf({"availability": "public"}, False)
    assert result is None


def test_mf_incomplete_true_nominal_video_returns_none() -> None:
    """incomplete=True should not change behavior for a non-live video."""
    mf = _make_mf()
    result = mf({"is_live": False, "live_status": "not_live"}, True)
    assert result is None


# ---------------------------------------------------------------------------
# New boundary / equivalence-partition tests added for the availability guard
# refactor (needs_auth removed from skip list).
# ---------------------------------------------------------------------------


def test_mf_availability_none_is_not_skipped() -> None:
    """Availability key present but value is None — must not skip."""
    mf = _make_mf()
    result = mf({"availability": None}, False)
    assert result is None


def test_mf_availability_empty_string_is_not_skipped() -> None:
    """Availability is an empty string — must not skip (not equal to 'scheduled')."""
    mf = _make_mf()
    result = mf({"availability": ""}, False)
    assert result is None


def test_mf_availability_private_is_not_skipped() -> None:
    """'private' availability must pass through; only 'scheduled' is blocked."""
    mf = _make_mf()
    result = mf({"availability": "private"}, False)
    assert result is None


def test_mf_availability_subscriber_only_is_not_skipped() -> None:
    """'subscriber_only' availability must pass through."""
    mf = _make_mf()
    result = mf({"availability": "subscriber_only"}, False)
    assert result is None


def test_mf_availability_premium_only_is_not_skipped() -> None:
    """'premium_only' availability must pass through."""
    mf = _make_mf()
    result = mf({"availability": "premium_only"}, False)
    assert result is None


def test_mf_needs_auth_and_is_live_queues_and_returns_skip_message() -> None:
    """needs_auth + is_live: the live-queue logic must still fire (availability no longer short-circuits)."""
    add_fn = MagicMock()
    log_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, log_fn)
    result = mf(
        {
            "availability": "needs_auth",
            "is_live": True,
            "live_status": "is_live",
            "webpage_url": "https://youtube.com/watch?v=auth_live",
        },
        False,
    )
    assert result == "Skipping live; queued for later"
    add_fn.assert_called_once_with("https://youtube.com/watch?v=auth_live", "1080playlists", None)


def test_mf_scheduled_takes_priority_over_live_status() -> None:
    """Scheduled availability must skip even when is_live is True."""
    add_fn = MagicMock()
    mf = build_match_filter("1080playlists", add_fn, MagicMock())
    result = mf(
        {
            "availability": "scheduled",
            "is_live": True,
            "live_status": "is_live",
            "webpage_url": "https://youtube.com/watch?v=sched",
        },
        False,
    )
    assert result == "Skipping: scheduled"
    # scheduled short-circuits before the queue logic
    add_fn.assert_not_called()


def test_mf_availability_case_sensitive() -> None:
    """'Scheduled' (capital S) must NOT match the 'scheduled' guard — case-sensitive."""
    mf = _make_mf()
    result = mf({"availability": "Scheduled"}, False)
    assert result is None

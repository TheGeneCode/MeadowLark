"""Unit tests for src.release_status: datetime and release-time classification."""

from datetime import datetime, timedelta, timezone

from src.release_status import (
    format_release_at,
    is_not_yet_released,
    parse_relative_release,
    parse_release_at,
    release_at_from_timestamp,
    to_release_at,
)

# Fixed reference time for deterministic assertions
NOW = datetime(2026, 7, 26, 15, 0, tzinfo=timezone(timedelta(hours=-5)))


def test_premieres_in_is_not_yet_released() -> None:
    assert is_not_yet_released("ERROR: [youtube] E2yjABcDiWQ: Premieres in 6 hours") is True


def test_live_event_will_begin_is_not_yet_released() -> None:
    assert is_not_yet_released("This live event will begin in 3 hours.") is True


def test_premieres_on_absolute_date_is_not_yet_released() -> None:
    assert is_not_yet_released("Premieres on Jul 30, 2026") is True


def test_genuine_failure_is_not_not_yet_released() -> None:
    assert is_not_yet_released("ERROR: unable to download video data: HTTP Error 403") is False


def test_private_video_is_not_not_yet_released() -> None:
    assert is_not_yet_released("Private video. Sign in if you've been granted access") is False


def test_empty_and_none_error_are_not_not_yet_released() -> None:
    assert is_not_yet_released("") is False
    assert is_not_yet_released(None) is False


def test_marker_match_is_case_insensitive() -> None:
    assert is_not_yet_released("PREMIERES IN 2 HOURS") is True


def test_parse_relative_release_hours() -> None:
    result = parse_relative_release("Premieres in 6 hours", now=NOW)
    assert result == NOW + timedelta(hours=6)
    assert result.tzinfo is not None


def test_parse_relative_release_singular_unit() -> None:
    result = parse_relative_release("Premieres in 1 minute", now=NOW)
    assert result == NOW + timedelta(seconds=60)


def test_parse_relative_release_absolute_date_returns_none() -> None:
    result = parse_relative_release("Premieres on Jul 30, 2026")
    assert result is None


def test_parse_relative_release_localizes_naive_now() -> None:
    naive_now = datetime(2026, 7, 26, 15, 0)  # noqa: DTZ001
    result = parse_relative_release("Premieres in 1 hour", now=naive_now)
    assert result is not None
    assert result.tzinfo is not None


def test_release_at_from_timestamp_is_aware_and_local() -> None:
    result = release_at_from_timestamp(1785110400)
    assert result is not None
    # Verify offset marker is present (the string contains + or -)
    assert "+" in result or "-" in result.split("T")[1]
    # Parse it back and verify it's aware
    parsed = parse_release_at(result)
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_release_at_from_timestamp_none_and_garbage() -> None:
    assert release_at_from_timestamp(None) is None
    assert release_at_from_timestamp("abc") is None  # type: ignore[arg-type]
    assert release_at_from_timestamp(1e30) is None


def test_parse_release_at_localizes_naive_string() -> None:
    result = parse_release_at("2026-07-26T21:00:00")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_release_at_rejects_garbage() -> None:
    result = parse_release_at("not a date")
    assert result is None


def test_format_release_at_future_hours_and_minutes() -> None:
    release = to_release_at(NOW + timedelta(hours=5, minutes=42))
    result = format_release_at(release, now=NOW)
    assert result.endswith("(in 5h 42m)")


def test_format_release_at_future_days() -> None:
    release = to_release_at(NOW + timedelta(days=2, hours=3))
    result = format_release_at(release, now=NOW)
    assert result.endswith("(in 2d 3h)")


def test_format_release_at_past_is_due_now() -> None:
    release = to_release_at(NOW - timedelta(hours=1))
    result = format_release_at(release, now=NOW)
    assert result.endswith("(due now)")


def test_format_release_at_none_is_unknown() -> None:
    result = format_release_at(None)
    assert result == "(time unknown)"


# ---------------------------------------------------------------------------
# Boundary coverage added: unit variants, count boundaries, naive/aware
# datetime handling, and the reference/parse dead-branch cases flagged as
# highest-risk in the phase handoff.
# ---------------------------------------------------------------------------


def test_parse_relative_release_singular_day() -> None:
    result = parse_relative_release("Premieres in 1 day", now=NOW)
    assert result == NOW + timedelta(days=1)


def test_parse_relative_release_plural_weeks() -> None:
    result = parse_relative_release("Premieres in 2 weeks", now=NOW)
    assert result == NOW + timedelta(weeks=2)


def test_parse_relative_release_plural_months_uses_30_day_approximation() -> None:
    result = parse_relative_release("Premieres in 3 months", now=NOW)
    assert result == NOW + timedelta(days=90)


def test_parse_relative_release_plural_seconds() -> None:
    result = parse_relative_release("Premieres in 45 seconds", now=NOW)
    assert result == NOW + timedelta(seconds=45)


def test_parse_relative_release_zero_count_is_now() -> None:
    result = parse_relative_release("Premieres in 0 seconds", now=NOW)
    assert result == NOW


def test_parse_relative_release_negative_count_does_not_match() -> None:
    # \d+ has no sign group, so a leading "-" simply isn't consumed by the regex.
    result = parse_relative_release("Premieres in -6 hours", now=NOW)
    assert result is None


def test_parse_relative_release_no_space_between_count_and_unit_does_not_match() -> None:
    result = parse_relative_release("Premieres in 6hours", now=NOW)
    assert result is None


def test_parse_relative_release_unmatched_unit_word_does_not_match() -> None:
    result = parse_relative_release("Premieres in 6 fortnights", now=NOW)
    assert result is None


def test_parse_relative_release_huge_count_returns_none() -> None:
    """
    An absurdly large count would overflow ``timedelta``/``datetime`` addition.

    The text this function parses comes verbatim from YouTube's own error
    string (surfaced by yt-dlp), so the app does not control its contents;
    an overflowing count must degrade to None rather than crash, matching
    ``release_at_from_timestamp``'s handling of the equivalent case.
    """
    assert parse_relative_release("Premieres in 999999999999 weeks", now=NOW) is None


def test_release_at_from_timestamp_epoch_zero() -> None:
    result = release_at_from_timestamp(0.0)
    assert result is not None
    parsed = parse_release_at(result)
    assert parsed is not None


def test_release_at_from_timestamp_negative_pre_1970() -> None:
    # On Windows, datetime.fromtimestamp raises OSError for negative timestamps
    # even with tz=UTC explicitly given -- covered by the existing except tuple.
    assert release_at_from_timestamp(-86400.0) is None


def test_release_at_from_timestamp_nan_returns_none() -> None:
    assert release_at_from_timestamp(float("nan")) is None


def test_release_at_from_timestamp_inf_returns_none() -> None:
    assert release_at_from_timestamp(float("inf")) is None


def test_to_release_at_naive_datetime_is_localized() -> None:
    """
    Highest-risk case per the handoff: a naive datetime must be localized.

    On the way in, it must be localized to the system's local offset, never
    emitted without an offset.
    """
    naive = datetime(2026, 7, 26, 21, 0)  # noqa: DTZ001
    result = to_release_at(naive)
    assert result is not None
    assert result.startswith("2026-07-26T21:00:00")
    # Must carry a real UTC offset, not be silently naive.
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None


def test_to_release_at_aware_datetime_preserves_offset_unchanged() -> None:
    aware = datetime(2026, 7, 26, 21, 0, tzinfo=timezone(timedelta(hours=9)))
    result = to_release_at(aware)
    assert result == "2026-07-26T21:00:00+09:00"


def test_to_release_at_none_returns_none() -> None:
    assert to_release_at(None) is None


def test_parse_release_at_z_suffix_is_parsed_as_utc() -> None:
    result = parse_release_at("2026-07-26T21:00:00Z")
    assert result is not None
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_parse_release_at_preserves_explicit_offset() -> None:
    result = parse_release_at("2026-07-26T21:00:00+09:00")
    assert result is not None
    assert result.utcoffset() == timedelta(hours=9)


def test_parse_release_at_empty_string_returns_none() -> None:
    assert parse_release_at("") is None


def test_format_release_at_exact_zero_remaining_is_due_now() -> None:
    release = to_release_at(NOW)
    result = format_release_at(release, now=NOW)
    assert result.endswith("(due now)")


def test_format_release_at_minutes_only_no_hours_or_days() -> None:
    release = to_release_at(NOW + timedelta(minutes=42))
    result = format_release_at(release, now=NOW)
    assert result.endswith("(in 42m)")


def test_format_release_at_garbage_value_is_time_unknown() -> None:
    result = format_release_at("not a date", now=NOW)
    assert result == "(time unknown)"


def test_format_release_at_naive_reference_is_localized() -> None:
    """The `reference.tzinfo is None` branch: a naive `now` must not crash format."""
    # +3 days comfortably clears any plausible UTC-offset discrepancy between
    # NOW's fixed offset and whatever the test machine's local tz resolves to
    # when the naive `now` below gets localized via .astimezone().
    release = to_release_at(NOW + timedelta(days=3))
    naive_now = datetime(2026, 7, 26, 15, 0)  # noqa: DTZ001
    result = format_release_at(release, now=naive_now)
    assert "(time unknown)" not in result
    assert "(due now)" not in result

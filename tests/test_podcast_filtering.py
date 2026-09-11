"""Unit tests for podcast_filtering helpers."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.podcast_filtering import (
    PODCAST_MIN_DURATION_SECONDS,
    _try_parse_datetime,
    append_downloaded_video_ids,
    append_to_archive_and_mark_skipped,
    check_sponsorblock_for_video_id,
    format_timestamp_readable,
    load_downloaded_video_ids,
    parse_scheduled_time_from_error,
    parse_video_id_from_error,
    parse_video_timestamp,
)


def test_parse_video_timestamp_uses_timestamp_field() -> None:
    entry = {"timestamp": 1710920000.0, "upload_date": "20240301"}
    assert parse_video_timestamp(entry) == 1710920000.0


def test_parse_video_timestamp_uses_upload_date_as_fallback() -> None:
    entry = {"upload_date": "20240301"}
    result = parse_video_timestamp(entry)
    assert isinstance(result, float)
    assert (
        datetime.fromtimestamp(result, tz=UTC).strftime("%Y%m%d") == "20240301"
    )


def test_parse_video_timestamp_invalid_upload_date_returns_none() -> None:
    entry = {"upload_date": "invalid"}
    assert parse_video_timestamp(entry) is None


def test_format_timestamp_readable_unknown() -> None:
    assert format_timestamp_readable(None) == "(unknown)"


def test_format_timestamp_readable_valid() -> None:
    ts = datetime(2024, 3, 1, tzinfo=UTC).timestamp()
    assert format_timestamp_readable(ts) == "2024-03-01"


def test_load_downloaded_video_ids_none_path() -> None:
    assert load_downloaded_video_ids(None) == set()


def test_load_downloaded_video_ids_missing_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.txt"
    assert not missing_file.exists()
    assert load_downloaded_video_ids(str(missing_file)) == set()


def test_load_downloaded_video_ids_reads_ids(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc123\nothers 456def\n")
    ids = load_downloaded_video_ids(str(archive_file))
    assert ids == {"abc123", "456def"}


def test_parse_scheduled_time_from_error_primetime() -> None:
    err = "This live event will begin in 2 hours"
    ts = parse_scheduled_time_from_error(err)
    assert ts is not None
    assert ts > datetime.now(tz=UTC).timestamp()


def test_parse_scheduled_time_from_error_scheduled() -> None:
    err = "This stream is scheduled to begin 2025-12-25 10:00"
    ts = parse_scheduled_time_from_error(err)
    assert ts is not None


def test_parse_scheduled_time_from_error_invalid() -> None:
    err = "Unknown error message"
    assert parse_scheduled_time_from_error(err) is None


def test_parse_video_id_from_error_premiere() -> None:
    err = "ERROR: [youtube] dQw4w9WgXcQ: This live event will begin in 2 hours."
    assert parse_video_id_from_error(err) == "dQw4w9WgXcQ"


def test_parse_video_id_from_error_scheduled_date() -> None:
    err = (
        "ERROR: [youtube] abc_DEF-123: This live event will begin… "
        "scheduled to begin 2025-12-25 10:00 UTC"
    )
    assert parse_video_id_from_error(err) == "abc_DEF-123"


def test_parse_video_id_from_error_no_match() -> None:
    err = "Unknown error message"
    assert parse_video_id_from_error(err) is None


def test_parse_video_id_from_error_ignores_playlist_tab() -> None:
    err = "ERROR: [youtube:tab] PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx: blah"
    assert parse_video_id_from_error(err) is None


# ---------------------------------------------------------------------------
# parse_video_id_from_error — boundary matrix for ID length and prefix variants
# ---------------------------------------------------------------------------


def test_parse_video_id_from_error_10_char_id_no_match() -> None:
    """10-char ID is below the required 11 — must not be extracted."""
    err = "ERROR: [youtube] 1234567890: some error"
    # The regex requires exactly 11 chars; a 10-char ID followed by ':' must not match.
    result = parse_video_id_from_error(err)
    assert result is None


def test_parse_video_id_from_error_12_char_id_no_match() -> None:
    """
    12-char ID is above the required 11 — must not be extracted as a whole token.

    The regex `{11}` is greedy-fixed-length.  A 12-char run ``ABCDEFGHIJKLmore:``
    does NOT end in ``:`` at position 11, so the pattern does not match the
    12-char ID.  The ID following ``[youtube]`` must be exactly 11 chars.
    """
    err = "ERROR: [youtube] 123456789012: some error"
    result = parse_video_id_from_error(err)
    assert result is None


def test_parse_video_id_from_error_no_trailing_colon_no_match() -> None:
    """11-char ID with no trailing colon must not be extracted."""
    err = "ERROR: [youtube] dQw4w9WgXcQ some error"
    assert parse_video_id_from_error(err) is None


def test_parse_video_id_from_error_empty_string_returns_none() -> None:
    """Empty input must return None without raising."""
    assert parse_video_id_from_error("") is None


def test_parse_video_id_from_error_multiple_youtube_tags_returns_first() -> None:
    """When two [youtube] patterns appear, the first 11-char ID is returned."""
    err = (
        "ERROR: [youtube] AAAAAAAAAAA: first error; "
        "[youtube] BBBBBBBBBBB: second error"
    )
    assert parse_video_id_from_error(err) == "AAAAAAAAAAA"


def test_parse_video_id_from_error_ignores_youtube_playlist_prefix() -> None:
    """[youtube:playlist] prefix (not [youtube:tab]) must also be ignored."""
    err = "ERROR: [youtube:playlist] PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx: blah"
    assert parse_video_id_from_error(err) is None


def test_parse_video_id_from_error_uppercase_youtube_prefix_no_match() -> None:
    """[YOUTUBE] (uppercase) must not match — the pattern is case-sensitive."""
    err = "ERROR: [YOUTUBE] dQw4w9WgXcQ: some error"
    assert parse_video_id_from_error(err) is None


def test_parse_video_id_from_error_id_with_hyphen_and_underscore() -> None:
    """IDs that mix ``-`` and ``_`` must be captured correctly."""
    err = "ERROR: [youtube] a-b_cD3EfGH: some premiere"
    assert parse_video_id_from_error(err) == "a-b_cD3EfGH"


# ---------------------------------------------------------------------------
# parse_scheduled_time_from_error — additional boundary cases
# ---------------------------------------------------------------------------


def test_parse_scheduled_time_from_error_days_unit() -> None:
    """'will begin in X days' must convert days to seconds correctly."""
    from datetime import datetime

    err = "This live event will begin in 3 days"
    ts = parse_scheduled_time_from_error(err)
    assert ts is not None
    expected_min = datetime.now(tz=UTC).timestamp() + 3 * 86400 - 5
    expected_max = datetime.now(tz=UTC).timestamp() + 3 * 86400 + 5
    assert expected_min < ts < expected_max


def test_parse_scheduled_time_from_error_full_datetime_with_utc_suffix() -> None:
    """'scheduled to begin YYYY-MM-DD HH:MM:SS UTC' must parse correctly."""
    from datetime import datetime

    err = "This stream is scheduled to begin 2026-01-15 09:30:00 UTC"
    ts = parse_scheduled_time_from_error(err)
    assert ts is not None
    expected = datetime(2026, 1, 15, 9, 30, 0, tzinfo=UTC).timestamp()
    assert ts == expected


def test_parse_scheduled_time_from_error_empty_string_returns_none() -> None:
    """Empty input must return None without raising."""
    assert parse_scheduled_time_from_error("") is None


@patch("src.podcast_filtering.requests.get")
def test_check_sponsorblock_for_video_id_has_segments(mock_get: Mock) -> None:
    mock_response = mock_get.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = [{"start": 0, "end": 10}]
    assert check_sponsorblock_for_video_id("abc123") is True


@patch("src.podcast_filtering.requests.get")
def test_check_sponsorblock_for_video_id_no_segments(mock_get: Mock) -> None:
    mock_response = mock_get.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = []
    assert check_sponsorblock_for_video_id("abc123") is False


@patch("src.podcast_filtering.requests.get")
def test_check_sponsorblock_for_video_id_api_error(mock_get: Mock) -> None:
    mock_get.side_effect = Exception("Network error")
    assert check_sponsorblock_for_video_id("abc123") is False


def test_append_to_archive_and_mark_skipped_default_reason(tmp_path: Path) -> None:
    """Test that default reason is 'Update exception'."""
    archive_file = tmp_path / "archive.txt"
    existing_ids: set[str] = set()
    messages: list[str] = []

    append_to_archive_and_mark_skipped(
        str(archive_file),
        "vid123",
        existing_ids,
        "Test Video (Update)",
        messages,
    )

    # Check video was archived
    assert "vid123" in existing_ids
    content = archive_file.read_text()
    assert "youtube vid123" in content

    # Check message contains default reason
    assert len(messages) == 1
    assert "Update exception" in messages[0]
    assert "Test Video (Update)" in messages[0]


def test_append_to_archive_and_mark_skipped_custom_reason(tmp_path: Path) -> None:
    """Test that custom reason is used in log message."""
    archive_file = tmp_path / "archive.txt"
    existing_ids: set[str] = set()
    messages: list[str] = []

    append_to_archive_and_mark_skipped(
        str(archive_file),
        "vid456",
        existing_ids,
        "Short Episode",
        messages,
        reason="Short duration (<3 min)",
    )

    # Check video was archived
    assert "vid456" in existing_ids
    content = archive_file.read_text()
    assert "youtube vid456" in content

    # Check message contains custom reason
    assert len(messages) == 1
    assert "Short duration (<3 min)" in messages[0]
    assert "Short Episode" in messages[0]


def test_append_to_archive_and_mark_skipped_none_archive() -> None:
    """Test that it works without an archive path."""
    existing_ids: set[str] = set()
    messages: list[str] = []

    append_to_archive_and_mark_skipped(
        None,
        "vid789",
        existing_ids,
        "Test Video",
        messages,
        reason="Test reason",
    )

    # Check video was NOT added to existing_ids (since no archive)
    assert "vid789" not in existing_ids

    # Check message was still logged
    assert len(messages) == 1
    assert "Test reason" in messages[0]


def test_append_to_archive_and_mark_skipped_deduplication(tmp_path: Path) -> None:
    """Test that duplicates are not re-archived."""
    archive_file = tmp_path / "archive.txt"
    existing_ids = {"vid999"}
    messages: list[str] = []

    append_to_archive_and_mark_skipped(
        str(archive_file),
        "vid999",
        existing_ids,
        "Duplicate Video",
        messages,
        reason="Test",
    )

    # Check message was logged
    assert len(messages) == 1

    # Nothing new to write, so the archive is never opened (deduplication worked)
    assert not archive_file.exists()


def test_podcast_min_duration_seconds_constant() -> None:
    """Test that the duration threshold constant is set correctly."""
    assert PODCAST_MIN_DURATION_SECONDS == 180


# ---------------------------------------------------------------------------
# load_downloaded_video_ids — blank lines and OSError (lines 69, 73-74)
# ---------------------------------------------------------------------------


def test_load_downloaded_video_ids_skips_blank_lines(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc123\n\nyoutube def456\n\n")
    ids = load_downloaded_video_ids(str(archive_file))
    assert ids == {"abc123", "def456"}


def test_load_downloaded_video_ids_oserror_returns_empty(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc123\n")
    with patch("pathlib.Path.open", side_effect=OSError("permission denied")):
        result = load_downloaded_video_ids(str(archive_file))
    assert result == set()


# ---------------------------------------------------------------------------
# append_downloaded_video_ids
# ---------------------------------------------------------------------------


def test_append_downloaded_video_ids_drops_falsy_and_duplicates_preserves_order(
    tmp_path: Path,
) -> None:
    archive_file = tmp_path / "archive.txt"
    written = append_downloaded_video_ids(
        archive_file, [None, "abc", "", "def", "abc", "def", "ghi"], set()
    )
    assert written == ["abc", "def", "ghi"]
    assert archive_file.read_text() == "youtube abc\nyoutube def\nyoutube ghi\n"


def test_append_downloaded_video_ids_loads_existing_ids_when_none_given(
    tmp_path: Path,
) -> None:
    archive_file = tmp_path / "archive.txt"
    archive_file.write_text("youtube abc\n")
    written = append_downloaded_video_ids(archive_file, ["abc", "def"])
    assert written == ["def"]
    assert archive_file.read_text() == "youtube abc\nyoutube def\n"


def test_append_downloaded_video_ids_mutates_given_existing_ids_in_place(
    tmp_path: Path,
) -> None:
    archive_file = tmp_path / "archive.txt"
    existing_ids = {"abc"}
    written = append_downloaded_video_ids(archive_file, ["abc", "def"], existing_ids)
    assert written == ["def"]
    assert existing_ids == {"abc", "def"}


def test_append_downloaded_video_ids_does_not_mutate_existing_ids_on_write_failure(
    tmp_path: Path,
) -> None:
    archive_file = tmp_path / "archive.txt"
    existing_ids = {"abc"}
    with (
        patch("pathlib.Path.open", side_effect=OSError("permission denied")),
        pytest.raises(OSError, match="permission denied"),
    ):
        append_downloaded_video_ids(archive_file, ["def"], existing_ids)
    assert existing_ids == {"abc"}


# ---------------------------------------------------------------------------
# format_timestamp_readable — OSError/ValueError branch (lines 97-98)
# ---------------------------------------------------------------------------


def test_format_timestamp_readable_oserror_returns_unknown() -> None:
    with patch("src.podcast_filtering.datetime") as mock_dt:
        mock_dt.fromtimestamp.side_effect = OSError("invalid ts")
        result = format_timestamp_readable(12345.0)
    assert result == "(unknown)"


def test_format_timestamp_readable_value_error_returns_unknown() -> None:
    with patch("src.podcast_filtering.datetime") as mock_dt:
        mock_dt.fromtimestamp.side_effect = ValueError("out of range")
        result = format_timestamp_readable(12345.0)
    assert result == "(unknown)"


# ---------------------------------------------------------------------------
# append_to_archive_and_mark_skipped — OSError branch (lines 129-130)
# ---------------------------------------------------------------------------


def test_append_to_archive_oserror_still_appends_message(tmp_path: Path) -> None:
    archive_file = tmp_path / "archive.txt"
    existing_ids: set[str] = set()
    messages: list[str] = []
    with patch("pathlib.Path.open", side_effect=OSError("permission denied")):
        append_to_archive_and_mark_skipped(
            str(archive_file),
            "vid123",
            existing_ids,
            "Test Video",
            messages,
        )
    assert len(messages) == 1
    assert "Test Video" in messages[0]


# ---------------------------------------------------------------------------
# _try_parse_datetime — all formats fail (line 196)
# ---------------------------------------------------------------------------


def test_try_parse_datetime_no_format_matches_returns_none() -> None:
    result = _try_parse_datetime("not-a-date", ("%Y-%m-%d",))
    assert result is None

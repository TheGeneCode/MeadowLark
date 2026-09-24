"""Tests for src.logging_utils module."""

import logging
import re
from datetime import datetime
from pathlib import Path

import pytest

from src import logging_utils
from src.logging_utils import get_local_timestamp, log_exception


class TestGetLocalTimestamp:
    """Tests for get_local_timestamp() function."""

    def test_returns_string(self) -> None:
        """get_local_timestamp() should return a string."""
        result = get_local_timestamp()
        assert isinstance(result, str)

    def test_format_is_correct(self) -> None:
        """get_local_timestamp() should return string in format 'YYYY-MM-DD HH:MM:SS'."""
        result = get_local_timestamp()
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, result), f"Timestamp {result} does not match expected format"

    def test_timestamp_is_recent(self) -> None:
        """get_local_timestamp() should return current time (within 1 second)."""
        before = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        result = get_local_timestamp()
        after = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

        assert before <= result <= after, (
            f"Timestamp {result} not within expected range ({before} to {after})"
        )

    def test_called_twice_increases_or_stays_same(self) -> None:
        """Sequential calls to get_local_timestamp() should be in ascending order."""
        timestamp1 = get_local_timestamp()
        timestamp2 = get_local_timestamp()

        assert timestamp1 <= timestamp2, (
            f"First timestamp {timestamp1} should be <= second timestamp {timestamp2}"
        )


class TestLogException:
    """Tests for log_exception() function."""

    def test_log_exception_no_root_handlers_triggers_basicconfig(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "error_log.txt"
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", log_path)
        original_handlers = logging.root.handlers[:]
        logging.root.handlers.clear()
        try:
            log_exception(ValueError("test error"), "test context")
        finally:
            # Close the file handler log_exception attached, or its open handle
            # surfaces as an unraisable ResourceWarning at interpreter shutdown.
            for handler in logging.root.handlers:
                handler.close()
            logging.root.handlers = original_handlers
        assert "test context: test error" in log_path.read_text(encoding="utf-8")

"""Tests for src.logging_utils module."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
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


@contextmanager
def _bare_root_logger() -> Iterator[None]:
    """
    Run with no root handlers; afterwards close any the block attached and restore.

    Entered inside the test body, not as a fixture: pytest's logging plugin attaches
    and detaches its own root handlers per phase, so clearing during setup would
    misfire in the call phase and restore setup-phase handlers that never get removed.
    """
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    logging.root.handlers.clear()
    try:
        yield
    finally:
        # Close what the block attached, or its open handle surfaces as an
        # unraisable ResourceWarning at interpreter shutdown.
        for handler in logging.root.handlers:
            handler.close()
        logging.root.handlers = original_handlers
        logging.root.setLevel(original_level)


class TestInitAppLogging:
    """init_app_logging() is the one place the root error-log handler is attached."""

    def test_routes_logged_exceptions_to_error_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "error_log.txt"
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", log_path)
        with _bare_root_logger():
            logging_utils.init_app_logging()
            log_exception(ValueError("test error"), "test context")
        assert "test context: test error" in log_path.read_text(encoding="utf-8")

    def test_log_exception_without_init_never_creates_error_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No lazy self-configuration: an unconfigured process must not open the log."""
        log_path = tmp_path / "error_log.txt"
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", log_path)
        with _bare_root_logger():
            log_exception(ValueError("test error"), "test context")
            assert not logging.root.handlers
        assert not log_path.exists()


class TestInitAppLoggingMigration:
    """logfile.txt -> error_log.txt migration branches of init_app_logging()."""

    @staticmethod
    def _setup(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, enabled: bool = True
    ) -> tuple[Path, Path]:
        legacy = tmp_path / "logfile.txt"
        error_log = tmp_path / "error_log.txt"
        monkeypatch.setattr(logging_utils, "_LEGACY_LOGFILE", legacy)
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", error_log)
        monkeypatch.setattr(logging_utils, "LOGFILE_MIGRATION_ENABLED", enabled)
        return legacy, error_log

    def test_legacy_file_moved_when_no_error_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy, error_log = self._setup(monkeypatch, tmp_path)
        legacy.write_text("old line\n", encoding="utf-8")
        with _bare_root_logger():
            logging_utils.init_app_logging()
        assert not legacy.exists()
        assert "old line" in error_log.read_text(encoding="utf-8")

    def test_legacy_file_untouched_when_flag_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy, error_log = self._setup(monkeypatch, tmp_path, enabled=False)
        legacy.write_text("old line\n", encoding="utf-8")
        with _bare_root_logger():
            logging_utils.init_app_logging()
        assert legacy.read_text(encoding="utf-8") == "old line\n"
        logged = error_log.read_text(encoding="utf-8") if error_log.exists() else ""
        assert "old line" not in logged

    def test_legacy_file_untouched_when_error_log_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy, error_log = self._setup(monkeypatch, tmp_path)
        legacy.write_text("old line\n", encoding="utf-8")
        error_log.write_text("current\n", encoding="utf-8")
        with _bare_root_logger():
            logging_utils.init_app_logging()
        assert legacy.exists()
        assert "old line" not in error_log.read_text(encoding="utf-8")

    def test_replace_oserror_is_logged_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy, error_log = self._setup(monkeypatch, tmp_path)
        legacy.write_text("old line\n", encoding="utf-8")

        def _boom(self: Path, target: object) -> Path:
            msg = "locked"
            raise PermissionError(msg)

        monkeypatch.setattr(Path, "replace", _boom)
        with _bare_root_logger():
            logging_utils.init_app_logging()
        assert legacy.exists()
        assert "Failed to migrate logfile.txt" in error_log.read_text(encoding="utf-8")


class TestInitAppLoggingRotation:
    """init_app_logging() attaches a size-capped, single-backup root file handler."""

    def test_root_handler_is_rotating_with_module_constants(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", tmp_path / "error_log.txt")
        with _bare_root_logger():
            logging_utils.init_app_logging()
            handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
            assert len(handlers) == 1
            assert handlers[0].maxBytes == logging_utils._ERROR_LOG_MAX_BYTES
            assert handlers[0].backupCount == logging_utils._ERROR_LOG_BACKUPS

    def test_rollover_leaves_exactly_log_and_one_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log_path = tmp_path / "error_log.txt"
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", log_path)
        monkeypatch.setattr(logging_utils, "_ERROR_LOG_MAX_BYTES", 1000)
        with _bare_root_logger():
            logging_utils.init_app_logging()
            for marker in ("first", "second", "third"):
                log_exception(ValueError(marker + "x" * 600), "ctx")
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "error_log.txt",
            "error_log.txt.1",
        ]
        assert "third" in log_path.read_text(encoding="utf-8")

    def test_migrated_legacy_content_survives_into_rotating_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Migration runs before the handler opens (and creates) the target file."""
        legacy = tmp_path / "logfile.txt"
        error_log = tmp_path / "error_log.txt"
        legacy.write_text("old line\n", encoding="utf-8")
        monkeypatch.setattr(logging_utils, "_LEGACY_LOGFILE", legacy)
        monkeypatch.setattr(logging_utils, "ERROR_LOG_PATH", error_log)
        monkeypatch.setattr(logging_utils, "LOGFILE_MIGRATION_ENABLED", True)
        with _bare_root_logger():
            logging_utils.init_app_logging()
            log_exception(ValueError("new"), "ctx")
        text = error_log.read_text(encoding="utf-8")
        assert not legacy.exists()
        assert text.startswith("old line")
        assert "ctx: new" in text


class TestLogException:
    """Tests for log_exception() function."""

    def test_log_exception_outside_except_block_records_passed_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A previously caught exception keeps its traceback after the handler exits."""
        try:
            int("not a number")
        except ValueError as exc:
            caught = exc
        assert logging.getLogger().hasHandlers()  # caplog handler: skip basicConfig path

        with caplog.at_level(logging.ERROR):
            log_exception(caught, "late")

        record = next(r for r in caplog.records if "late: invalid literal" in r.getMessage())
        assert record.exc_info is not None
        assert record.exc_info[1] is caught
        assert "ValueError: invalid literal" in caplog.text
        assert "NoneType: None" not in caplog.text

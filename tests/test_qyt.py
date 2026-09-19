"""Unit tests for QYT module classes and functionality."""

import subprocess
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject

from QYT import HistoryHook, HistoryLogger, QHook, QLogger, QYTQueue, parse_history_log


class TestQLogger:
    """Tests for QLogger class."""

    def test_qlogger_initialization(self) -> None:
        """Test QLogger initializes with a download queue."""
        queue = Queue()
        logger = QLogger(queue)
        assert logger.downloadQueue is queue
        assert logger.daemon is True
        assert hasattr(logger, "message_changed")

    def test_qlogger_debug(self) -> None:
        """Test debug messages are emitted (except ETA and iB/s)."""
        queue = Queue()
        logger = QLogger(queue)

        mock_signal = MagicMock()
        logger.message_changed.connect(mock_signal)

        logger.debug("Normal message")
        mock_signal.assert_called_with("Normal message")

        mock_signal.reset_mock()
        logger.debug("Message with ETA time")
        mock_signal.assert_not_called()

        mock_signal.reset_mock()
        logger.debug("Download speed: 100 iB/s")
        mock_signal.assert_not_called()

    def test_qlogger_warning(self) -> None:
        """Test warning messages are emitted."""
        queue = Queue()
        logger = QLogger(queue)
        mock_signal = MagicMock()
        logger.message_changed.connect(mock_signal)

        logger.warning("Warning message")
        mock_signal.assert_called_with("Warning message")

    def test_qlogger_error(self) -> None:
        """Test error messages are emitted."""
        queue = Queue()
        logger = QLogger(queue)
        mock_signal = MagicMock()
        logger.message_changed.connect(mock_signal)

        logger.error("Error message")
        mock_signal.assert_called_with("Error message")


class TestQHook:
    """Tests for QHook class."""

    def test_qhook_initialization(self) -> None:
        """Test QHook initializes as a QObject."""
        hook = QHook()
        assert isinstance(hook, QObject)
        assert hasattr(hook, "info_changed")

    def test_qhook_call(self) -> None:
        """Test QHook emits info_changed signal with copied dict."""
        hook = QHook()
        mock_signal = MagicMock()
        hook.info_changed.connect(mock_signal)

        test_dict = {"id": "123", "title": "Test Video"}
        hook(test_dict)

        # Verify emit was called with a dict containing the same data
        mock_signal.assert_called_once()
        emitted_dict = mock_signal.call_args[0][0]
        assert emitted_dict == test_dict
        # Verify it's a copy, not the same object
        assert emitted_dict is not test_dict


class TestHistoryLogger:
    """Tests for HistoryLogger class."""

    def test_history_logger_path(self) -> None:
        """Test HistoryLogger has correct file path."""
        assert Path("history_log.txt") == HistoryLogger.HISTORY_PATH

    def test_format_entry(self) -> None:
        """Test HistoryLogger._format_entry formatting."""
        result = HistoryLogger._format_entry(
            "2026-03-24 10:30:45",
            "youtube",
            "1080",
            "Test Video",
            "SUCCESS",
        )
        assert "[2026-03-24 10:30:45]" in result
        assert "Site: youtube" in result
        assert "Type: 1080" in result
        assert "Title: Test Video" in result
        assert "Result: SUCCESS" in result
        assert result.endswith("\n")

    def test_format_entry_with_url(self) -> None:
        """Test _format_entry appends URL field when provided."""
        result = HistoryLogger._format_entry(
            "2026-03-24 10:30:45",
            "youtube",
            "1080",
            "Test Video",
            "SUCCESS",
            "https://www.youtube.com/watch?v=abc123",
        )
        assert "| URL: https://www.youtube.com/watch?v=abc123" in result
        assert result.endswith("\n")

    def test_format_entry_without_url(self) -> None:
        """Test _format_entry omits URL field when url is None."""
        result = HistoryLogger._format_entry(
            "2026-03-24 10:30:45",
            "youtube",
            "1080",
            "Test Video",
            "SUCCESS",
            None,
        )
        assert "URL" not in result

    def test_format_entry_failure(self) -> None:
        """Test HistoryLogger._format_entry with failure."""
        result = HistoryLogger._format_entry(
            "2026-03-24 10:30:45",
            "nebula",
            "podcast",
            "Episode 1",
            "FAIL",
        )
        assert "Result: FAIL" in result

    def test_format_entry_skipped(self) -> None:
        """Test HistoryLogger._format_entry with skipped result."""
        result = HistoryLogger._format_entry(
            "2026-03-24 10:30:45",
            "youtube",
            "audio_playlists",
            "Short Episode",
            "SKIPPED (Short duration (<3 min))",
        )
        assert "Result: SKIPPED (Short duration (<3 min))" in result

    @patch("QYT.HistoryLogger.HISTORY_PATH")
    def test_log_success(self, mock_path: MagicMock) -> None:
        """Test HistoryLogger.log writes success entry."""
        mock_file = MagicMock()
        mock_path.parent.mkdir = MagicMock()
        mock_path.open = MagicMock(return_value=mock_file.__enter__.return_value)
        mock_path.open.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_path.open.return_value.__exit__ = MagicMock(return_value=None)

        HistoryLogger().log("youtube", "1080", "Test Video", success=True)

        mock_file.write.assert_called_once()
        written_content = mock_file.write.call_args[0][0]
        assert "SUCCESS" in written_content
        assert "URL" not in written_content

    @patch("QYT.HistoryLogger.HISTORY_PATH")
    def test_log_success_with_url(self, mock_path: MagicMock) -> None:
        """Test HistoryLogger.log writes URL field when provided."""
        mock_file = MagicMock()
        mock_path.parent.mkdir = MagicMock()
        mock_path.open = MagicMock(return_value=mock_file.__enter__.return_value)
        mock_path.open.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_path.open.return_value.__exit__ = MagicMock(return_value=None)

        HistoryLogger().log(
            "youtube",
            "1080",
            "Test Video",
            success=True,
            url="https://www.youtube.com/watch?v=abc123",
        )

        mock_file.write.assert_called_once()
        written_content = mock_file.write.call_args[0][0]
        assert "SUCCESS" in written_content
        assert "| URL: https://www.youtube.com/watch?v=abc123" in written_content

    @patch("QYT.HistoryLogger.HISTORY_PATH")
    def test_log_skip(self, mock_path: MagicMock) -> None:
        """Test HistoryLogger.log_skip writes skip entry."""
        mock_file = MagicMock()
        mock_path.parent.mkdir = MagicMock()
        mock_path.open = MagicMock(return_value=mock_file.__enter__.return_value)
        mock_path.open.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_path.open.return_value.__exit__ = MagicMock(return_value=None)

        HistoryLogger().log_skip(
            "youtube",
            "audio_playlists",
            "Short Episode",
            "Short duration (<3 min)",
        )

        # Verify write was called
        mock_file.write.assert_called_once()
        written_content = mock_file.write.call_args[0][0]
        assert "SKIPPED (Short duration (<3 min))" in written_content


class TestParseHistoryLog:
    """Tests for parse_history_log()."""

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when history file does not exist."""
        with patch("QYT.HistoryLogger.HISTORY_PATH", tmp_path / "nonexistent.txt"):
            assert parse_history_log() == []

    def test_parses_old_format_without_url(self, tmp_path: Path) -> None:
        """Parses legacy entries that have no URL field."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "[2026-03-01 10:00:00] Site: youtube | Type: 1080 | Title: Old Video | Result: SUCCESS\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["url"] is None
        assert entries[0]["result"] == "SUCCESS"
        assert entries[0]["title"] == "Old Video"

    def test_parses_new_format_with_url(self, tmp_path: Path) -> None:
        """Parses new entries that include URL field."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: New Video | Result: SUCCESS | URL: https://www.youtube.com/watch?v=xyz\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["url"] == "https://www.youtube.com/watch?v=xyz"
        assert entries[0]["result"] == "SUCCESS"

    def test_returns_newest_first(self, tmp_path: Path) -> None:
        """Entries are returned newest-first (file order reversed)."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "[2026-03-01 10:00:00] Site: youtube | Type: 1080 | Title: First | Result: SUCCESS\n"
            "[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: Second | Result: FAIL\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert entries[0]["title"] == "Second"
        assert entries[1]["title"] == "First"

    def test_title_with_pipe_characters(self, tmp_path: Path) -> None:
        """Parses entries where the title contains ' | ' without breaking."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "[2026-04-01 12:00:00] Site: nebula | Type: podcast | Title: Part 1 | The Story | Result: SUCCESS | URL: https://nebula.tv/ep1\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["title"] == "Part 1 | The Story"
        assert entries[0]["url"] == "https://nebula.tv/ep1"

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        """Silently skips lines that don't match the expected format."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "this is garbage\n"
            "[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: Good | Result: SUCCESS\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["title"] == "Good"

    def test_crlf_line_endings_no_trailing_carriage_return(self, tmp_path: Path) -> None:
        r"""CRLF line endings do not leave a trailing \r in the result field."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_bytes(
            b"[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: Video | Result: SUCCESS\r\n",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["result"] == "SUCCESS"

    def test_utf8_bom_does_not_drop_first_entry(self, tmp_path: Path) -> None:
        """UTF-8 BOM at start of file does not cause the first entry to be skipped."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_bytes(
            b"\xef\xbb\xbf[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: First | Result: SUCCESS\n",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["title"] == "First"

    def test_result_containing_pipe_url_uses_last_occurrence(self, tmp_path: Path) -> None:
        """When result text contains ' | URL: ', the real URL (last field) is extracted correctly."""
        log_file = tmp_path / "history_log.txt"
        log_file.write_text(
            "[2026-04-01 12:00:00] Site: youtube | Type: 1080 | Title: Vid | Result: SKIPPED (see | URL: docs) | URL: https://youtube.com/watch?v=abc\n",
            encoding="utf-8",
        )
        with patch("QYT.HistoryLogger.HISTORY_PATH", log_file):
            entries = parse_history_log()
        assert len(entries) == 1
        assert entries[0]["url"] == "https://youtube.com/watch?v=abc"
        assert entries[0]["result"] == "SKIPPED (see | URL: docs)"


class TestHistoryHook:
    """Tests for HistoryHook class."""

    def test_history_hook_initialization(self) -> None:
        """Test HistoryHook initializes with metadata."""
        meta = {"site": "youtube", "type": "1080"}
        hook = HistoryHook(meta)
        assert hook.meta == meta
        assert hook._seen_ids == set()

    def test_history_hook_initialization_none(self) -> None:
        """Test HistoryHook with None metadata."""
        hook = HistoryHook(None)
        assert hook.meta == {}
        assert hook._seen_ids == set()

    def test_history_hook_vid_id(self) -> None:
        """Test HistoryHook._vid_id extraction."""
        hook = HistoryHook(None)

        # Test with id field
        assert hook._vid_id({"id": "abc123"}) == "abc123"

        # Test with fallback to _filename
        assert hook._vid_id({"_filename": "video.mp4"}) == "video.mp4"

        # Test with fallback to url
        assert hook._vid_id({"url": "https://example.com"}) == "https://example.com"

        # Test with no id fields
        assert hook._vid_id({}) == "unknown"

    def test_history_hook_infer_site(self) -> None:
        """Test HistoryHook._infer_site detection."""
        hook = HistoryHook({"site": "youtube"})
        assert hook._infer_site({}) == "youtube"

        hook_unknown = HistoryHook(None)
        assert hook_unknown._infer_site({"extractor_key": "youtube"}) == "youtube"
        assert hook_unknown._infer_site({"extractor": "nebula"}) == "nebula"
        assert hook_unknown._infer_site({}) == "unknown"

    @patch("QYT.HistoryLogger.log")
    def test_history_hook_call_finished(self, mock_log: MagicMock) -> None:
        """Test HistoryHook.__call__ logs on 'finished' status."""
        meta = {"site": "youtube", "type": "1080"}
        hook = HistoryHook(meta)

        event = {
            "status": "finished",
            "info_dict": {"id": "123", "title": "Test Video"},
        }
        hook(event)

        mock_log.assert_called_once()
        args = mock_log.call_args
        assert args[1]["success"] is True
        assert args[1]["url"] is None

    @patch("QYT.HistoryLogger.log")
    def test_history_hook_passes_url(self, mock_log: MagicMock) -> None:
        """Test HistoryHook.__call__ extracts and passes webpage_url."""
        meta = {"site": "youtube", "type": "1080"}
        hook = HistoryHook(meta)

        event = {
            "status": "finished",
            "info_dict": {
                "id": "abc",
                "title": "Video With URL",
                "webpage_url": "https://www.youtube.com/watch?v=abc",
            },
        }
        hook(event)

        mock_log.assert_called_once()
        assert mock_log.call_args[1]["url"] == "https://www.youtube.com/watch?v=abc"

    @patch("QYT.HistoryLogger.log")
    def test_history_hook_call_deduplication(
        self,
        mock_log: MagicMock,
    ) -> None:
        """Test HistoryHook deduplicates by video ID."""
        meta = {"site": "youtube", "type": "1080"}
        hook = HistoryHook(meta)

        event = {
            "status": "finished",
            "info_dict": {"id": "123", "title": "Test Video"},
        }

        hook(event)
        hook(event)  # Call again with same id

        # Should only log once
        assert mock_log.call_count == 1


class TestQYTQueue:
    """Tests for QYTQueue class."""

    def test_qytqueue_initialization(self) -> None:
        """Test QYTQueue initializes as a QThread."""
        queue = Queue()
        ydl_queue = QYTQueue(queue)
        assert ydl_queue.downloadQueue is queue
        assert ydl_queue.daemon is True
        assert hasattr(ydl_queue, "message_changed")
        assert hasattr(ydl_queue, "queue_empty")

    def test_run_keeps_worker_alive_when_download_raises_unlisted_error(self) -> None:
        """
        Test the worker loop survives an exception type it does not enumerate.

        yt-dlp calls into third-party plugins (the bgutil PO-token provider shells
        out to Deno), so arbitrary exception types can cross this boundary. Anything
        escaping QThread.run() aborts the whole PyQt process, so the item must fail
        while the queue keeps running.
        """

        class _StopLoop(Exception):
            """Breaks out of the otherwise infinite worker loop."""

        queue = MagicMock()
        item = (["https://youtube.com/watch?v=test"], {})
        queue.get.side_effect = [item, _StopLoop()]
        queue.empty.return_value = True

        ydl_queue = QYTQueue(queue)
        messages: list[str] = []
        ydl_queue.message_changed.connect(messages.append)

        timeout = subprocess.TimeoutExpired(cmd=["deno", "run"], timeout=15.0)
        with (
            patch("QYT.keep"),
            patch.object(ydl_queue, "download", side_effect=timeout),
            pytest.raises(_StopLoop),
        ):
            ydl_queue.run()

        # The loop asked for a second item, i.e. it outlived the failing one.
        assert queue.get.call_count == 2
        assert any("Download error" in message for message in messages)

    def test_run_processes_next_item_after_a_failure(self) -> None:
        """
        Test a failed item does not block a subsequent successful item.

        Regression coverage for the widened ``except Exception``: the worker
        must fully drain the queue -- fail item one, then still download and
        report success for item two -- rather than stalling or skipping it.
        """

        class _StopLoop(Exception):
            """Breaks out of the otherwise infinite worker loop."""

        queue = MagicMock()
        failing_item = (["https://youtube.com/watch?v=fails"], {})
        ok_item = (["https://youtube.com/watch?v=ok"], {})
        queue.get.side_effect = [failing_item, ok_item, _StopLoop()]
        queue.empty.return_value = True

        ydl_queue = QYTQueue(queue)
        messages: list[str] = []
        ydl_queue.message_changed.connect(messages.append)

        timeout = subprocess.TimeoutExpired(cmd=["deno", "run"], timeout=15.0)
        with (
            patch("QYT.keep"),
            patch.object(ydl_queue, "download", side_effect=[timeout, None]),
            pytest.raises(_StopLoop),
        ):
            ydl_queue.run()

        assert queue.get.call_count == 3
        assert any("Download error" in m and "fails" in m for m in messages)
        assert any("Finished downloading" in m and "ok" in m for m in messages)

    def test_extract_title_from_urls(self) -> None:
        """Test _extract_title returns URL if extraction fails."""
        queue = Queue()
        ydl_queue = QYTQueue(queue)

        # With empty list
        assert ydl_queue._extract_title([]) == "(unknown)"

        # With URL
        urls = ["https://youtube.com/watch?v=test"]
        # This will fail silently since YoutubeDL is not mocked
        title = ydl_queue._extract_title(urls)
        assert isinstance(title, str)
        # If extraction failed, should have returned URL or unknown
        assert title in (urls[0], "(unknown)")

    def test_try_720_fallback_conditions(self) -> None:
        """Test _try_720_fallback early exit conditions."""
        queue = Queue()
        ydl_queue = QYTQueue(queue)

        options = {}
        # Should return False if error_str doesn't indicate format unavailable
        success, error = ydl_queue._try_720_fallback(
            ["url"],
            options,
            "title",
            "youtube",
            "Some other error",
        )
        assert success is False
        assert error == "Some other error"

        # Should return False if already tried 720 fallback
        options = {"_tried_720_fallback": True}
        success, error = ydl_queue._try_720_fallback(
            ["url"],
            options,
            "title",
            "youtube",
            "Requested format is not available",
        )
        assert success is False

    def test_await_deno_warm_announces_once_while_pending(self) -> None:
        """
        The "Preparing downloader" message fires on the first pending call only.

        _await_deno_warm() runs at the top of every queue iteration, so a
        multi-item queue calls it repeatedly while the startup warm-up is
        still in flight; the message must not spam once per item.
        """
        queue = Queue()
        ydl_queue = QYTQueue(queue)
        messages: list[str] = []
        ydl_queue.message_changed.connect(messages.append)

        with (
            patch("QYT.deno_warmup_pending", return_value=True) as mock_pending,
            patch("QYT.wait_for_deno_warm") as mock_wait,
        ):
            ydl_queue._await_deno_warm()
            ydl_queue._await_deno_warm()
            ydl_queue._await_deno_warm()

        assert messages == ["Preparing downloader (warming Deno cache)..."]
        # `not self._announced_deno_wait and deno_warmup_pending()` short-circuits
        # once the flag latches True, so pending() is consulted exactly once per
        # process -- not once per queue iteration.
        assert mock_pending.call_count == 1
        # But the wait itself is unconditional every iteration -- that is what
        # actually gates the download, not the announcement.
        assert mock_wait.call_count == 3

    def test_await_deno_warm_no_message_when_never_pending(self) -> None:
        """No warm-up in flight (feature off / already finished): silent wait."""
        queue = Queue()
        ydl_queue = QYTQueue(queue)
        messages: list[str] = []
        ydl_queue.message_changed.connect(messages.append)

        with (
            patch("QYT.deno_warmup_pending", return_value=False),
            patch("QYT.wait_for_deno_warm") as mock_wait,
        ):
            ydl_queue._await_deno_warm()

        assert messages == []
        assert mock_wait.call_count == 1

    def test_await_deno_warm_flag_latches_after_first_call_regardless_of_result(
        self,
    ) -> None:
        """
        The announced flag latches on the very first call, pending() or not.

        Item 1 is dequeued while the warm-up has already finished (pending()
        False) -- no message, flag still latches True. Item 2's call must
        therefore skip the pending() check entirely (short-circuited), yet
        still perform the mandatory wait.
        """
        queue = Queue()
        ydl_queue = QYTQueue(queue)
        messages: list[str] = []
        ydl_queue.message_changed.connect(messages.append)

        with (
            patch("QYT.deno_warmup_pending", return_value=False) as mock_pending,
            patch("QYT.wait_for_deno_warm") as mock_wait,
        ):
            ydl_queue._await_deno_warm()
            ydl_queue._await_deno_warm()

        assert messages == []
        assert mock_pending.call_count == 1
        assert mock_wait.call_count == 2

    def test_try_without_sponsorblock_conditions(self) -> None:
        """Test _try_without_sponsorblock early exit conditions."""
        queue = Queue()
        ydl_queue = QYTQueue(queue)

        options = {}
        # Should return False if error doesn't mention SponsorBlock API
        success, error = ydl_queue._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "1080",
            "Some other error",
        )
        assert success is False
        assert error == "Some other error"

        # Should return False if already tried without sponsorblock
        options = {"_tried_without_sponsorblock": True}
        success, error = ydl_queue._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "1080",
            "Unable to communicate with SponsorBlock API",
        )
        assert success is False

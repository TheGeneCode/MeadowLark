"""Tests for download executor logic with mocked yt-dlp calls."""

import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from src.download_executor import ON_URL_START_KEY, DownloadExecutor


class TestDownloadExecutorInitialization:
    """Tests for DownloadExecutor initialization."""

    def test_initialization_with_callback(self) -> None:
        """Test executor initializes with message callback."""
        callback = Mock()
        executor = DownloadExecutor(message_callback=callback)
        assert executor.message_callback == callback

    def test_initialization_without_callback(self) -> None:
        """Test executor initializes with default no-op callback."""
        executor = DownloadExecutor()
        # Should not raise when called
        executor._emit_message("test")


class TestExtractTitle:
    """Tests for title extraction from URLs."""

    def test_extract_title_empty_urls(self) -> None:
        """Test extraction with empty URL list returns unknown."""
        executor = DownloadExecutor()
        title = executor._extract_title([])
        assert title == "(unknown)"

    @patch("src.download_executor.extract_playlist_info")
    def test_extract_title_success(self, mock_extract_func: Mock) -> None:
        """Test successful title extraction from first URL."""
        mock_extract_func.return_value = {
            "title": "Test Video Title",
        }

        executor = DownloadExecutor()
        title = executor._extract_title(["https://example.com/video"])
        assert title == "Test Video Title"

    @patch("src.download_executor.extract_playlist_info")
    def test_extract_title_fallback_to_url(self, mock_extract_func: Mock) -> None:
        """Test title extraction falls back to URL if title not found."""
        mock_extract_func.return_value = {}

        executor = DownloadExecutor()
        url = "https://example.com/video"
        title = executor._extract_title([url])
        assert title == url

    @patch("src.download_executor.extract_playlist_info")
    def test_extract_title_handles_download_error(
        self,
        mock_extract_func: Mock,
    ) -> None:
        """Test title extraction handles DownloadError gracefully."""
        mock_extract_func.side_effect = DownloadError(
            "Invalid URL",
        )

        executor = DownloadExecutor()
        title = executor._extract_title(["https://example.com/invalid"])
        assert title == "https://example.com/invalid"

    @patch("src.download_executor.extract_playlist_info")
    def test_extract_title_passes_cookiefile_from_options(
        self,
        mock_extract_func: Mock,
    ) -> None:
        """Test that cookiefile from options is forwarded to extract_playlist_info."""
        mock_extract_func.return_value = {"title": "Age Restricted Video"}

        executor = DownloadExecutor()
        options = {"cookiefile": "/path/to/cookies.txt", "format": "bestvideo+bestaudio"}
        title = executor._extract_title(["https://example.com/video"], options)

        assert title == "Age Restricted Video"
        _, kwargs = mock_extract_func.call_args
        assert kwargs.get("extra_opts") == {"cookiefile": "/path/to/cookies.txt"}

    @patch("src.download_executor.extract_playlist_info")
    def test_extract_title_no_extra_opts_when_no_cookiefile(
        self,
        mock_extract_func: Mock,
    ) -> None:
        """Test that extra_opts is None when options has no cookiefile."""
        mock_extract_func.return_value = {"title": "Some Video"}

        executor = DownloadExecutor()
        options = {"format": "bestvideo+bestaudio"}
        executor._extract_title(["https://example.com/video"], options)

        _, kwargs = mock_extract_func.call_args
        assert kwargs.get("extra_opts") is None


class TestTry720Fallback:
    """Tests for 720p fallback strategy."""

    def test_no_fallback_if_error_not_format_related(self) -> None:
        """Test no fallback if error is not format-related."""
        executor = DownloadExecutor()
        options = {}
        success, error = executor._try_720_fallback(
            ["url"],
            options,
            "title",
            "youtube",
            "Some other error",
        )
        assert success is False
        assert error == "Some other error"

    def test_no_fallback_if_already_tried(self) -> None:
        """Test no fallback if already attempted 720p."""
        executor = DownloadExecutor()
        options = {"_tried_720_fallback": True}
        success, error = executor._try_720_fallback(
            ["url"],
            options,
            "title",
            "youtube",
            "Requested format is not available",
        )
        assert success is False

    @patch("src.download_executor.YoutubeDL")
    def test_720_fallback_success(self, mock_ydl_class: Mock) -> None:
        """Test successful 720p fallback."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        callback = Mock()

        executor = DownloadExecutor(message_callback=callback)
        options = {
            "qmeta": {"site": "youtube", "type": "1080"},
        }
        success, error = executor._try_720_fallback(
            ["https://youtube.com/watch?v=test"],
            options,
            "Test Video",
            "youtube",
            "Requested format is not available",
        )
        assert success is True
        assert error == "Requested format is not available"
        # Verify message was emitted
        callback.assert_called_once()
        assert "720" in callback.call_args[0][0]

    @patch("src.download_executor.YoutubeDL")
    def test_720_fallback_failure(self, mock_ydl_class: Mock) -> None:
        """Test 720p fallback failure with error message."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("720p not available")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        options = {}
        success, error = executor._try_720_fallback(
            ["url"],
            options,
            "title",
            "youtube",
            "Requested format is not available",
        )
        assert success is False
        assert "720p not available" in error


class TestTryWithoutSponsorblock:
    """Tests for SponsorBlock fallback strategy."""

    def test_no_fallback_if_error_not_sponsorblock_related(self) -> None:
        """Test no fallback if error is not SponsorBlock-related."""
        executor = DownloadExecutor()
        options = {}
        success, error = executor._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "audio",
            "Some other error",
        )
        assert success is False

    def test_no_fallback_if_already_tried(self) -> None:
        """Test no fallback if already attempted without SponsorBlock."""
        executor = DownloadExecutor()
        options = {"_tried_without_sponsorblock": True}
        success, error = executor._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "audio",
            "Unable to communicate with SponsorBlock API",
        )
        assert success is False

    @patch("src.download_executor.remove_sponsorblock_postprocessor")
    @patch("src.download_executor.YoutubeDL")
    def test_sponsorblock_fallback_success(
        self,
        mock_ydl_class: Mock,
        mock_remove_sb: Mock,
    ) -> None:
        """Test successful SponsorBlock removal fallback."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_remove_sb.return_value = {"modified": True}
        callback = Mock()

        executor = DownloadExecutor(message_callback=callback)
        options = {}
        success, error = executor._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "audio",
            "Unable to communicate with SponsorBlock API",
        )
        assert success is True
        callback.assert_called_once()
        assert "SponsorBlock" in callback.call_args[0][0]

    @patch("src.download_executor.remove_sponsorblock_postprocessor")
    @patch("src.download_executor.YoutubeDL")
    def test_sponsorblock_fallback_failure(
        self,
        mock_ydl_class: Mock,
        mock_remove_sb: Mock,
    ) -> None:
        """Test SponsorBlock removal fallback failure."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("Download failed")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_remove_sb.return_value = {}

        executor = DownloadExecutor()
        options = {}
        success, error = executor._try_without_sponsorblock(
            ["url"],
            options,
            "title",
            "youtube",
            "audio",
            "Unable to communicate with SponsorBlock API",
        )
        assert success is False
        assert "Download failed" in error


class TestExecute:
    """Tests for main execute() method with fallback chain."""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_success_first_try(self, mock_ydl_class: Mock) -> None:
        """Test successful download on first attempt."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {"test": True})
        assert success is True
        assert error == ""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_reports_helper_process_timeout_as_failed_download(
        self,
        mock_ydl_class: Mock,
    ) -> None:
        """
        Test a helper-process timeout inside yt-dlp is reported, not propagated.

        The bgutil PO-token provider probes its Deno script with a hard 15s budget
        and raises subprocess.TimeoutExpired when Deno has to re-resolve its npm
        deps over the network. That is a failed download, not an app-level fault.
        """
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_ydl_instance.download.side_effect = subprocess.TimeoutExpired(
            cmd=["deno", "run", "generate_once.ts", "--version"],
            timeout=15.0,
        )

        executor = DownloadExecutor()
        success, error = executor.execute(
            ["https://youtube.com/watch?v=test"],
            {"qmeta": {"site": "youtube", "type": "1080"}},
        )

        assert success is False
        assert "timed out after 15.0 seconds" in error

    @patch("src.download_executor.YoutubeDL")
    def test_execute_1080_format_not_available_falls_back_to_720(
        self,
        mock_ydl_class: Mock,
    ) -> None:
        """Test 1080p download failure triggers 720p fallback."""
        # First call (main): raises format error
        # Second call (720p fallback): succeeds
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise DownloadError("Requested format is not available [1080]")
            # Second call succeeds

        mock_ydl_instance.download.side_effect = side_effect

        executor = DownloadExecutor()
        options = {
            "qmeta": {"type": "1080", "site": "youtube"},
        }
        success, error = executor.execute(["url"], options)
        assert success is True
        assert error == ""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_sponsorblock_api_error_falls_back(
        self,
        mock_ydl_class: Mock,
    ) -> None:
        """Test SponsorBlock API error triggers fallback."""
        mock_ydl_instance = MagicMock()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise DownloadError("Unable to communicate with SponsorBlock API")
            # Second call (without SponsorBlock) succeeds

        mock_ydl_instance.download.side_effect = side_effect
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        with patch("src.download_executor.remove_sponsorblock_postprocessor"):
            executor = DownloadExecutor()
            options = {
                "qmeta": {"type": "audio", "site": "youtube"},
                "postprocessors": [{"key": "SponsorBlock"}],
            }
            success, error = executor.execute(["url"], options)
            assert success is True
            assert error == ""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_all_fallbacks_fail(self, mock_ydl_class: Mock) -> None:
        """Test failure when all fallbacks are exhausted."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("Permanent failure")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {"test": True})
        assert success is False
        assert "Permanent failure" in error
        assert "site: unknown" in error

    @patch("src.download_executor.YoutubeDL")
    def test_execute_preserves_metadata(self, mock_ydl_class: Mock) -> None:
        """Test execute preserves site and type metadata in errors."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("Test error")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        options = {
            "qmeta": {"site": "nebula", "type": "720"},
        }
        success, error = executor.execute(["url"], options)
        assert success is False
        assert "site: nebula" in error
        assert "type: 720" in error

    def test_execute_with_message_callback(self) -> None:
        """Test execute emits messages via callback."""
        callback = Mock()
        executor = DownloadExecutor(message_callback=callback)

        with patch("src.download_executor.YoutubeDL") as mock_ydl_class:
            mock_ydl_instance = MagicMock()
            mock_ydl_instance.download.side_effect = DownloadError(
                "Requested format is not available",
            )
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

            options = {
                "qmeta": {"type": "1080", "site": "youtube"},
                "title": "Test",
            }
            executor.execute(["url"], options)

            # Should have emitted 720p fallback message
            assert any("720" in str(call) for call in callback.call_args_list)


class TestExtractBaseOutputDir:
    """Tests for _extract_base_output_dir boundary conditions."""

    def test_outtmpl_string_two_segment_path_returns_parent(self) -> None:
        """Nominal: string outtmpl with two segments returns first segment."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir(
            {"outtmpl": "E:/vid storage/%(playlist)s.%(ext)s"}
        )
        assert result == "E:/vid storage"

    def test_outtmpl_string_three_segment_path_returns_all_but_last(self) -> None:
        """String outtmpl with three segments returns all but last."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir(
            {"outtmpl": "E:/vid storage/playlists/%(title)s.%(ext)s"}
        )
        assert result == "E:/vid storage/playlists"

    def test_outtmpl_string_single_segment_returns_none(self) -> None:
        """String outtmpl with no slash (single segment) returns None — no parent to extract."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": "output.%(ext)s"})
        assert result is None

    def test_outtmpl_empty_string_returns_none(self) -> None:
        """Empty string outtmpl returns None."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": ""})
        assert result is None

    def test_outtmpl_missing_key_returns_none(self) -> None:
        """Missing outtmpl key returns None (defaults to empty string sentinel)."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({})
        assert result is None

    def test_outtmpl_dict_with_default_string_returns_parent(self) -> None:
        """Dict outtmpl with string 'default' key uses that value."""
        executor = DownloadExecutor()
        options = {"outtmpl": {"default": "E:/vid/%(playlist)s/%(title)s.%(ext)s"}}
        result = executor._extract_base_output_dir(options)
        assert result == "E:/vid/%(playlist)s"

    def test_outtmpl_dict_default_not_string_falls_back_to_other_value(self) -> None:
        """Dict outtmpl where 'default' is not a string falls back to first string value found."""
        executor = DownloadExecutor()
        options = {"outtmpl": {"default": 42, "chapter": "E:/vid/chapters/%(title)s.%(ext)s"}}
        result = executor._extract_base_output_dir(options)
        assert result == "E:/vid/chapters"

    def test_outtmpl_dict_no_string_values_returns_none(self) -> None:
        """Dict outtmpl with no string values at all returns None."""
        executor = DownloadExecutor()
        options = {"outtmpl": {"default": None, "chapter": 99}}
        result = executor._extract_base_output_dir(options)
        assert result is None

    def test_outtmpl_dict_empty_dict_returns_none(self) -> None:
        """Empty dict outtmpl returns None."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": {}})
        assert result is None

    def test_outtmpl_dict_default_empty_string_falls_back_to_other_value(self) -> None:
        """Dict outtmpl where 'default' is empty falls back to first non-empty string value."""
        executor = DownloadExecutor()
        options = {"outtmpl": {"default": "", "chapter": "E:/vid/chapters/%(title)s.%(ext)s"}}
        result = executor._extract_base_output_dir(options)
        assert result == "E:/vid/chapters"

    def test_outtmpl_non_string_non_dict_type_returns_none(self) -> None:
        """Outtmpl of an unexpected type (e.g. list) returns None without raising."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": ["E:/vid/%(title)s.%(ext)s"]})
        assert result is None

    def test_outtmpl_string_trailing_slash_single_useful_segment(self) -> None:
        """Outtmpl ending with a slash produces an empty last segment; all-but-last is returned."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": "E:/vid/"})
        assert result == "E:/vid"

    def test_outtmpl_string_only_slash_returns_none(self) -> None:
        """Outtmpl of just '/' produces an empty join which coerces to None via `or None`."""
        executor = DownloadExecutor()
        result = executor._extract_base_output_dir({"outtmpl": "/"})
        assert result is None


class TestRenameNaFolderIfNeeded:
    """Tests for _rename_na_folder_if_needed boundary conditions."""

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_nominal_invokes_rename(self, mock_rename: Mock) -> None:
        """Nominal: valid playlist_comments and extractable dir calls rename helper."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "E:/vid/%(playlist)s.%(ext)s",
            "qmeta": {
                "playlist_comments": {"PL123": "My Playlist"},
                "playlist_id": "PL123",
            },
        }
        executor._rename_na_folder_if_needed(options, ["https://youtube.com/playlist?list=PL123"])
        mock_rename.assert_called_once_with(
            "E:/vid",
            ["https://youtube.com/playlist?list=PL123"],
            {"PL123": "My Playlist"},
            direct_playlist_id="PL123",
        )

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_no_qmeta_key_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """Missing 'qmeta' key — rename is never called."""
        executor = DownloadExecutor()
        executor._rename_na_folder_if_needed({"outtmpl": "E:/vid/x.%(ext)s"}, ["url"])
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_qmeta_is_none_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """Explicit None qmeta — meta falls back to {}, no playlist_comments, rename not called."""
        executor = DownloadExecutor()
        executor._rename_na_folder_if_needed(
            {"outtmpl": "E:/vid/x.%(ext)s", "qmeta": None}, ["url"]
        )
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_playlist_comments_empty_dict_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """Empty playlist_comments dict is falsy — rename is not called."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "E:/vid/%(playlist)s.%(ext)s",
            "qmeta": {"playlist_comments": {}},
        }
        executor._rename_na_folder_if_needed(options, ["url"])
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_playlist_comments_none_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """Explicit None playlist_comments — rename is not called."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "E:/vid/%(playlist)s.%(ext)s",
            "qmeta": {"playlist_comments": None},
        }
        executor._rename_na_folder_if_needed(options, ["url"])
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_no_extractable_base_dir_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """When base dir cannot be extracted (single-segment path), rename is not called."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "output.%(ext)s",
            "qmeta": {"playlist_comments": {"PL123": "My Playlist"}},
        }
        executor._rename_na_folder_if_needed(options, ["url"])
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_missing_outtmpl_does_not_invoke_rename(self, mock_rename: Mock) -> None:
        """Missing outtmpl yields no base dir — rename is not called."""
        executor = DownloadExecutor()
        options = {"qmeta": {"playlist_comments": {"PL123": "My Playlist"}}}
        executor._rename_na_folder_if_needed(options, ["url"])
        mock_rename.assert_not_called()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_empty_urls_list_still_invokes_rename(self, mock_rename: Mock) -> None:
        """Empty URL list is valid input — rename is still called (path_utils handles the no-op)."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "E:/vid/%(playlist)s.%(ext)s",
            "qmeta": {"playlist_comments": {"PL123": "My Playlist"}},
        }
        executor._rename_na_folder_if_needed(options, [])
        mock_rename.assert_called_once()

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_no_playlist_id_in_qmeta_passes_none(self, mock_rename: Mock) -> None:
        """Missing playlist_id key in qmeta passes None as direct_playlist_id."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": "E:/vid/%(playlist)s.%(ext)s",
            "qmeta": {"playlist_comments": {"PL123": "My Playlist"}},
        }
        executor._rename_na_folder_if_needed(options, ["url"])
        _, kwargs = mock_rename.call_args
        assert kwargs["direct_playlist_id"] is None

    @patch("src.download_executor.rename_playlist_folders_from_comments")
    def test_dict_outtmpl_extracts_dir_and_invokes_rename(self, mock_rename: Mock) -> None:
        """Dict-form outtmpl is handled correctly — dir extracted and rename called."""
        executor = DownloadExecutor()
        options = {
            "outtmpl": {"default": "E:/vid/%(playlist)s/%(title)s.%(ext)s"},
            "qmeta": {"playlist_comments": {"PL123": "My Playlist"}},
        }
        executor._rename_na_folder_if_needed(options, ["url"])
        mock_rename.assert_called_once()
        call_args = mock_rename.call_args[0]
        assert call_args[0] == "E:/vid/%(playlist)s"


class TestExtractBaseOutputDirEdgeCaseBug:
    """Documents a latent boundary issue in _extract_base_output_dir."""

    def test_dict_default_empty_string_falls_back_to_non_empty_value(self) -> None:
        """When 'default' is '' the loop skips it and uses the first non-empty string value."""
        executor = DownloadExecutor()
        options = {"outtmpl": {"default": "", "chapter": "E:/vid/chapters/%(title)s.%(ext)s"}}
        result = executor._extract_base_output_dir(options)
        assert result == "E:/vid/chapters"


class TestExecuteEdgeCases:
    """Tests for edge cases in download execution."""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_extractor_error(self, mock_ydl_class: Mock) -> None:
        """Test handling of ExtractorError exceptions."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = ExtractorError("Extractor failed")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {"test": True})
        assert success is False
        assert "Extractor failed" in error

    @patch("src.download_executor.YoutubeDL")
    def test_execute_os_error(self, mock_ydl_class: Mock) -> None:
        """Test handling of OSError exceptions."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = OSError("Disk full")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {"test": True})
        assert success is False
        assert "Disk full" in error

    @patch("src.download_executor.YoutubeDL")
    def test_execute_called_process_error_reported_as_failed_download(
        self,
        mock_ydl_class: Mock,
    ) -> None:
        """
        Test a non-timeout SubprocessError subclass is also caught and reported.

        subprocess.CalledProcessError is a sibling of TimeoutExpired under
        SubprocessError (e.g. the bgutil PO-token provider's Deno probe exiting
        non-zero rather than timing out). YDL_DOWNLOAD_ERRORS must catch the
        SubprocessError base, not just the timeout subclass exercised elsewhere.
        """
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["deno", "run", "generate_once.ts", "--version"],
        )
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {"test": True})
        assert success is False
        assert "returned non-zero exit status" in error

    @patch("src.download_executor.YoutubeDL")
    def test_execute_with_empty_options(self, mock_ydl_class: Mock) -> None:
        """Test execute with minimal options."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        success, error = executor.execute(["url"], {})
        assert success is True
        assert error == ""

    @patch("src.download_executor.YoutubeDL")
    def test_execute_preserves_ydl_cache(
        self,
        mock_ydl_class: Mock,
    ) -> None:
        """
        yt-dlp cache must be preserved across downloads.

        Wiping ~/.cache/yt-dlp before every run destroys the cached YouTube
        JS-challenge solver library, forcing a fresh GitHub fetch each time and
        turning a transient upstream failure into an unrecoverable
        "Requested format is not available". The executor must never call
        ``cache.remove()``.
        """
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        executor = DownloadExecutor()
        executor.execute(["url"], {"test": True})

        mock_ydl_instance.cache.remove.assert_not_called()
        mock_ydl_instance.download.assert_called_once_with(["url"])


# ---------------------------------------------------------------------------
# New boundary tests for _extract_title cookiefile edge cases and the
# execute() → _extract_title options-forwarding path.
# ---------------------------------------------------------------------------


class TestExtractTitleCookiefileBoundaries:
    """Boundary tests for cookiefile handling in _extract_title."""

    @patch("src.download_executor.extract_playlist_info")
    def test_options_none_default_passes_no_extra_opts(self, mock_extract: Mock) -> None:
        """options=None (default) must not set extra_opts — backward compat."""
        mock_extract.return_value = {"title": "Some Video"}
        executor = DownloadExecutor()
        title = executor._extract_title(["https://example.com/video"])
        assert title == "Some Video"
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None

    @patch("src.download_executor.extract_playlist_info")
    def test_options_with_cookiefile_none_passes_no_extra_opts(self, mock_extract: Mock) -> None:
        """cookiefile=None in options — walrus produces None which is falsy, so no extra_opts."""
        mock_extract.return_value = {"title": "Video"}
        executor = DownloadExecutor()
        executor._extract_title(["https://example.com/video"], {"cookiefile": None})
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None

    @patch("src.download_executor.extract_playlist_info")
    def test_options_with_cookiefile_empty_string_passes_no_extra_opts(
        self, mock_extract: Mock
    ) -> None:
        """cookiefile="" is falsy — walrus short-circuits, extra_opts must remain None."""
        mock_extract.return_value = {"title": "Video"}
        executor = DownloadExecutor()
        executor._extract_title(["https://example.com/video"], {"cookiefile": ""})
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None

    @patch("src.download_executor.extract_playlist_info")
    def test_options_empty_dict_passes_no_extra_opts(self, mock_extract: Mock) -> None:
        """options={} (no cookiefile key at all) — extra_opts must remain None."""
        mock_extract.return_value = {"title": "Video"}
        executor = DownloadExecutor()
        executor._extract_title(["https://example.com/video"], {})
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None

    @patch("src.download_executor.extract_playlist_info")
    def test_options_cookiefile_whitespace_string_forwarded(self, mock_extract: Mock) -> None:
        """
        Cookiefile containing only whitespace is truthy — it IS forwarded.

        This documents the boundary: callers must sanitise the cookiefile value
        themselves; _extract_title forwards any truthy string without validation.
        """
        mock_extract.return_value = {"title": "Video"}
        executor = DownloadExecutor()
        executor._extract_title(["https://example.com/video"], {"cookiefile": "   "})
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") == {"cookiefile": "   "}

    @patch("src.download_executor.extract_playlist_info")
    def test_options_with_other_keys_but_no_cookiefile_passes_no_extra_opts(
        self, mock_extract: Mock
    ) -> None:
        """Options dict with many keys but no cookiefile must not produce extra_opts."""
        mock_extract.return_value = {"title": "Video"}
        executor = DownloadExecutor()
        executor._extract_title(
            ["https://example.com/video"],
            {"format": "bestvideo+bestaudio", "merge_output_format": "mp4", "quiet": True},
        )
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None


class TestExecuteForwardsCookiefileOnError:
    """Verify execute() passes options (including cookiefile) to _extract_title on error."""

    @patch("src.download_executor.extract_playlist_info")
    @patch("src.download_executor.YoutubeDL")
    def test_execute_passes_options_to_extract_title_on_download_error(
        self,
        mock_ydl_class: Mock,
        mock_extract: Mock,
    ) -> None:
        """
        When execute() catches a DownloadError, _extract_title gets options.

        This lets age-restricted lookups succeed.
        """
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("Auth required")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_extract.return_value = {"title": "Age Restricted Title"}

        executor = DownloadExecutor()
        options = {
            "cookiefile": "/cookies.txt",
            "qmeta": {"site": "youtube", "type": "audio"},
        }
        success, error = executor.execute(["https://youtube.com/watch?v=age"], options)

        assert success is False
        # extract_playlist_info was called with the cookiefile forwarded
        mock_extract.assert_called_once()
        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") == {"cookiefile": "/cookies.txt"}
        # The resolved title appears in the error message
        assert "Age Restricted Title" in error

    @patch("src.download_executor.extract_playlist_info")
    @patch("src.download_executor.YoutubeDL")
    def test_execute_no_cookiefile_in_options_extra_opts_is_none(
        self,
        mock_ydl_class: Mock,
        mock_extract: Mock,
    ) -> None:
        """execute() with no cookiefile in options must forward extra_opts=None."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = DownloadError("fail")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_extract.return_value = {"title": "Plain Video"}

        executor = DownloadExecutor()
        options = {"qmeta": {"site": "youtube", "type": "audio"}}
        executor.execute(["https://youtube.com/watch?v=x"], options)

        _, kwargs = mock_extract.call_args
        assert kwargs.get("extra_opts") is None


class TestRunDownloadUrlListener:
    """_run_download announces each top-level URL when a listener is installed."""

    @patch("src.download_executor.YoutubeDL")
    def test_run_download_without_listener_passes_whole_list(self, mock_ydl_class: Mock) -> None:
        ydl = mock_ydl_class.return_value.__enter__.return_value

        DownloadExecutor()._run_download({}, ["a", "b"])

        ydl.download.assert_called_once_with(["a", "b"])

    @patch("src.download_executor.YoutubeDL")
    def test_run_download_announces_each_url_before_its_download(
        self, mock_ydl_class: Mock
    ) -> None:
        events: list[tuple] = []
        ydl = mock_ydl_class.return_value.__enter__.return_value
        ydl.download.side_effect = lambda urls: events.append(("dl", urls))
        opts = {ON_URL_START_KEY: lambda url: events.append(("start", url))}

        DownloadExecutor()._run_download(opts, ["a", "b"])

        assert events == [("start", "a"), ("dl", ["a"]), ("start", "b"), ("dl", ["b"])]

    @patch("src.download_executor.YoutubeDL")
    def test_run_download_listener_error_does_not_stop_download(self, mock_ydl_class: Mock) -> None:
        ydl = mock_ydl_class.return_value.__enter__.return_value
        listener = Mock(side_effect=TypeError("boom"))

        DownloadExecutor()._run_download({ON_URL_START_KEY: listener}, ["a", "b"])

        assert [c.args[0] for c in ydl.download.call_args_list] == [["a"], ["b"]]

    @patch("src.download_executor.YoutubeDL")
    def test_run_download_download_error_stops_remaining_urls(self, mock_ydl_class: Mock) -> None:
        """Without ignoreerrors the first raise propagates, exactly as ydl.download(list) did."""
        ydl = mock_ydl_class.return_value.__enter__.return_value
        ydl.download.side_effect = DownloadError("fail")
        listener = Mock()

        with pytest.raises(DownloadError):
            DownloadExecutor()._run_download({ON_URL_START_KEY: listener}, ["a", "b"])

        listener.assert_called_once_with("a")

    @patch("src.download_executor.get_setting", new=Mock(return_value="mp4"))
    def test_rung_fallback_keeps_listener(self) -> None:
        def listener(_url: str) -> None:
            return None

        result = DownloadExecutor._rung_options_modifier(720)(
            {ON_URL_START_KEY: listener, "qmeta": {}}
        )

        assert result[ON_URL_START_KEY] is listener

    @patch.object(DownloadExecutor, "_extract_title", new=Mock(return_value="T"))
    @patch("src.download_executor.YoutubeDL")
    def test_execute_sponsorblock_fallback_announces_url_again(self, mock_ydl_class: Mock) -> None:
        """The real SponsorBlock modifier keeps the listener, so the retry re-tags the URL."""
        ydl = mock_ydl_class.return_value.__enter__.return_value
        sb_error = DownloadError("Unable to communicate with SponsorBlock API")
        ydl.download.side_effect = [sb_error, None]
        listener = Mock()
        options = {
            ON_URL_START_KEY: listener,
            "qmeta": {"type": "audio", "site": "youtube"},
            "postprocessors": [{"key": "SponsorBlock"}],
        }

        success, _ = DownloadExecutor().execute(["a"], options)

        assert success is True
        assert listener.call_args_list == [(("a",),), (("a",),)]
        retry_opts = mock_ydl_class.call_args_list[1].args[0]
        assert retry_opts[ON_URL_START_KEY] is listener

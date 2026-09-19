"""Unit tests for podcast_helpers module."""

from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from src.exceptions import PodcastResolutionError
from src.podcast_helpers import MAX_LOOKAHEAD, fetch_latest_accessible_entry

# Constants for tests
EXPECTED_MAX_LOOKAHEAD = 5
EXPECTED_CALL_COUNT = 4


class TestMaxLookahead:
    """Tests for MAX_LOOKAHEAD constant."""

    def test_max_lookahead_value(self) -> None:
        """Test that MAX_LOOKAHEAD is set to 5."""
        assert MAX_LOOKAHEAD == EXPECTED_MAX_LOOKAHEAD

    def test_max_lookahead_is_int(self) -> None:
        """Test that MAX_LOOKAHEAD is an integer."""
        assert isinstance(MAX_LOOKAHEAD, int)


class TestFetchLatestAccessibleEntry:
    """Tests for fetch_latest_accessible_entry function."""

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_extraction_carries_pot_provider_wiring(self, mock_ydl_class: MagicMock) -> None:
        """
        Metadata extraction must carry the shared PO-token provider wiring.

        A bare YoutubeDL falls back to the plugin's
        ~/bgutil-ytdlp-pot-provider default server_home, whose cold-cache
        Deno probe overruns yt-dlp's 15s budget and raises
        subprocess.TimeoutExpired into the podcast refresh.
        """
        from src.config import POT_PROVIDER_SERVER_HOME
        from src.ydl_options import JS_RUNTIMES_CONFIG

        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_ydl_instance.extract_info.return_value = {
            "entries": [{"id": "vid123", "title": "Episode 1"}],
            "id": "playlist123",
        }

        fetch_latest_accessible_entry("http://test.url")

        opts = mock_ydl_class.call_args.args[0]
        assert opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
            str(POT_PROVIDER_SERVER_HOME)
        ]
        assert opts["extractor_args"]["youtube"]["player_client"]
        assert opts["js_runtimes"] == JS_RUNTIMES_CONFIG
        # per-call options must survive the merge
        assert opts["playlist_items"] == "1"

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_pot_provider_wiring_carried_on_every_lookahead_retry(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """
        Every lookahead retry must carry the wiring, not just the first call.

        build_shared_extraction_opts() is invoked fresh inside the retry loop
        (once per ``n``). A regression that only wired the first iteration
        would leave later retries -- reached whenever the latest entry is a
        private video -- exposed to the stale-server_home bug again.
        """
        from src.config import POT_PROVIDER_SERVER_HOME

        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        mock_ydl_instance.extract_info.side_effect = [
            {"entries": [{"id": "p1", "title": "Private video"}], "id": "pl"},
            {"entries": [{"id": "p2", "title": "Public"}], "id": "pl"},
        ]

        fetch_latest_accessible_entry("http://test.url")

        assert mock_ydl_class.call_count == 2
        for call in mock_ydl_class.call_args_list:
            opts = call.args[0]
            assert opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
                str(POT_PROVIDER_SERVER_HOME)
            ]
        # playlist_items must increment per retry, proving each call rebuilds
        # (rather than reuses/caches) the merged opts.
        assert mock_ydl_class.call_args_list[0].args[0]["playlist_items"] == "1"
        assert mock_ydl_class.call_args_list[1].args[0]["playlist_items"] == "2"

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_single_iteration_success(self, mock_ydl_class: MagicMock) -> None:
        """Test successful fetch on first iteration (n=1)."""
        # Setup mock
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        mock_ydl_instance.extract_info.return_value = {
            "entries": [{"id": "vid123", "title": "Episode 1"}],
            "id": "playlist123",
        }

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert len(entries) == 1
        assert entries[0]["id"] == "vid123"
        assert skipped is False
        assert info["id"] == "playlist123"

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_skips_private_on_title(self, mock_ydl_class: MagicMock) -> None:
        """Test private video detected by title, retry succeeds."""
        # Setup: first call returns private video
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # First iteration: private video by title
        private_response = {
            "entries": [{"id": "private1", "title": "Private video"}],
            "id": "playlist123",
        }

        # Second iteration: accessible video
        public_response = {
            "entries": [
                {"id": "vid_old", "title": "Old Episode"},
                {"id": "vid_new", "title": "Episode 2"},
            ],
            "id": "playlist123",
        }

        mock_ydl_instance.extract_info.side_effect = [private_response, public_response]

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert len(entries) == 1
        assert entries[0]["id"] == "vid_new"
        assert skipped is True  # Private video was skipped

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_retries_on_private_video_exception(
        self,
        mock_ydl_class: MagicMock,
    ) -> None:
        """Test private video detected via exception, retry succeeds."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # First call raises exception for private video
        # Second call succeeds
        mock_ydl_instance.extract_info.side_effect = [
            ExtractorError("Private video encountered"),
            {
                "entries": [{"id": "vid456", "title": "Public Episode"}],
                "id": "playlist123",
            },
        ]

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert len(entries) == 1
        assert entries[0]["id"] == "vid456"
        assert skipped is True

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_raises_non_private_error_immediately(
        self,
        mock_ydl_class: MagicMock,
    ) -> None:
        """Test that non-private errors are raised immediately."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # First call raises a non-private error
        mock_ydl_instance.extract_info.side_effect = DownloadError("URL not found")

        # Execute and verify exception is raised
        with pytest.raises(DownloadError, match="URL not found"):
            fetch_latest_accessible_entry("http://invalid.url")

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_exhausts_lookahead_raises_podcast_resolution(
        self,
        mock_ydl_class: MagicMock,
    ) -> None:
        """Test that exhausting lookahead window raises PodcastResolutionError."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # All iterations return private videos
        mock_ydl_instance.extract_info.side_effect = ExtractorError(
            "Private video encountered",
        )

        # Execute and verify exception is raised
        with pytest.raises(PodcastResolutionError):
            fetch_latest_accessible_entry("http://all.private.url")

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_empty_playlist_raises_error(self, mock_ydl_class: MagicMock) -> None:
        """Test that empty playlist raises PodcastResolutionError."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # Return empty entries
        mock_ydl_instance.extract_info.return_value = {
            "entries": [],
            "id": "empty_playlist",
        }

        # Execute and verify exception is raised
        with pytest.raises(PodcastResolutionError):
            fetch_latest_accessible_entry("http://empty.url")

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_oserror_raised_immediately(self, mock_ydl_class: MagicMock) -> None:
        """Test that OSError is raised immediately without retry."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # OSError (network issue) should be raised immediately
        mock_ydl_instance.extract_info.side_effect = OSError("Network timeout")

        # Execute and verify exception is raised
        with pytest.raises(OSError, match="Network timeout"):
            fetch_latest_accessible_entry("http://test.url")

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_valueerror_raised_immediately(self, mock_ydl_class: MagicMock) -> None:
        """Test that ValueError is raised immediately without retry."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # ValueError should be raised immediately
        mock_ydl_instance.extract_info.side_effect = ValueError("Invalid URL format")

        # Execute and verify exception is raised
        with pytest.raises(ValueError, match="Invalid URL format"):
            fetch_latest_accessible_entry("http://test.url")

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_multiple_retries_on_private_videos(
        self,
        mock_ydl_class: MagicMock,
    ) -> None:
        """Test that function retries multiple times for private videos."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        # Simulate 3 private video attempts, then success on 4th
        mock_ydl_instance.extract_info.side_effect = [
            ExtractorError("Private video 1"),
            ExtractorError("Private video 2"),
            ExtractorError("Private video 3"),
            {
                "entries": [{"id": "vid789", "title": "Eventually Public"}],
                "id": "playlist123",
            },
        ]

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert len(entries) == 1
        assert entries[0]["id"] == "vid789"
        assert skipped is True
        assert mock_ydl_instance.extract_info.call_count == EXPECTED_CALL_COUNT

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_info_dict_returned_correctly(self, mock_ydl_class: MagicMock) -> None:
        """Test that the full info dict is returned for playlist metadata."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        expected_info = {
            "id": "playlist_123",
            "title": "My Podcast",
            "uploader": "Test Uploader",
            "entries": [{"id": "ep1", "title": "Episode 1"}],
        }

        mock_ydl_instance.extract_info.return_value = expected_info

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert info == expected_info
        assert info["title"] == "My Podcast"

    @patch("src.podcast_helpers.yt_dlp.YoutubeDL")
    def test_returns_list_of_entries(self, mock_ydl_class: MagicMock) -> None:
        """Test that entries are returned as a list."""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance

        mock_ydl_instance.extract_info.return_value = {
            "entries": [{"id": "ep1", "title": "Episode 1"}],
            "id": "playlist123",
        }

        # Execute
        entries, skipped, info = fetch_latest_accessible_entry("http://test.url")

        # Verify
        assert isinstance(entries, list)
        assert len(entries) == 1

"""Unit tests for utils module and extracted utility functions."""

from src.dict_utils import merge_dicts_recursive, remove_sponsorblock_postprocessor
from src.exceptions import PlaylistExtractionError
from src.path_utils import (
    resolve_playlist_label,
    sanitize_for_path,
    slugify_if_too_long,
)
from src.playlist_utils import detect_site_from_urls, is_primitive_technology
from src.version_utils import normalize_version


class TestVersionUtils:
    """Tests for version_utils module."""

    def test_normalize_version_valid(self) -> None:
        """Test normalize_version with valid version strings."""
        assert normalize_version("2025.08.27") == (2025, 8, 27)
        assert normalize_version("2025.8.27") == (2025, 8, 27)
        assert normalize_version("1.0.0") == (1, 0, 0)
        assert normalize_version("10.20.30.40") == (10, 20, 30, 40)

    def test_normalize_version_invalid(self) -> None:
        """Test normalize_version with invalid inputs."""
        assert normalize_version(None) == ()
        assert normalize_version("") == ()
        assert normalize_version("abc") == ()

    def test_normalize_version_partial(self) -> None:
        """Test normalize_version with partial numeric strings."""
        assert normalize_version("2025") == (2025,)
        assert normalize_version("v2025.08.27") == (2025, 8, 27)
        assert normalize_version("2025-08-27") == (2025, 8, 27)


class TestDictUtils:
    """Tests for dict_utils module."""

    def test_merge_dicts_recursive_basic(self) -> None:
        """Test basic dictionary merging."""
        base = {"a": 1, "b": 2}
        overrides = {"b": 3, "c": 4}
        result = merge_dicts_recursive(base, overrides)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_merge_dicts_recursive_nested(self) -> None:
        """Test nested dictionary merging."""
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        overrides = {"a": {"y": 20, "z": 30}}
        result = merge_dicts_recursive(base, overrides)
        assert result == {"a": {"x": 1, "y": 20, "z": 30}, "b": 3}

    def test_merge_dicts_recursive_preserves_input(self) -> None:
        """Test that merge doesn't mutate input dictionaries."""
        base = {"a": 1}
        overrides = {"b": 2}
        merge_dicts_recursive(base, overrides)
        assert base == {"a": 1}
        assert overrides == {"b": 2}

    def test_merge_dicts_recursive_lists(self) -> None:
        """Test list merging in dictionaries."""
        base = {"items": [1, 2]}
        overrides = {"items": [3, 4]}
        result = merge_dicts_recursive(base, overrides)
        assert result == {"items": [1, 2, 3, 4]}

    def test_remove_sponsorblock_postprocessor(self) -> None:
        """Test SponsorBlock postprocessor removal."""
        opts = {
            "postprocessors": [
                {"key": "SponsorBlock"},
                {"key": "FFmpegExtractAudio"},
            ],
        }
        result = remove_sponsorblock_postprocessor(opts)
        assert len(result["postprocessors"]) == 1
        assert result["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        # Verify original not mutated
        assert len(opts["postprocessors"]) == 2

    def test_remove_sponsorblock_postprocessor_none_postprocessors(self) -> None:
        opts = {"postprocessors": None, "format": "best"}
        result = remove_sponsorblock_postprocessor(opts)
        assert result["postprocessors"] is None
        assert result["format"] == "best"

    def test_remove_sponsorblock_postprocessor_string_postprocessors(self) -> None:
        opts = {"postprocessors": "not_a_list"}
        result = remove_sponsorblock_postprocessor(opts)
        assert result["postprocessors"] == "not_a_list"


class TestExceptions:
    """Tests for exceptions module."""

    def test_playlist_extraction_error_stores_original_exc(self) -> None:
        orig = ValueError("x")
        err = PlaylistExtractionError(original_exc=orig)
        assert err.original_exc is orig

    def test_playlist_extraction_error_uses_default_msg(self) -> None:
        err = PlaylistExtractionError()
        assert str(err) == PlaylistExtractionError.MSG
        assert err.original_exc is None


class TestPlaylistUtils:
    """Tests for playlist_utils module."""

    def test_detect_site_from_urls_youtube(self) -> None:
        """Test YouTube site detection."""
        assert detect_site_from_urls(["https://youtube.com/watch?v=123"]) == "youtube"
        assert detect_site_from_urls(["https://youtu.be/123"]) == "youtube"

    def test_detect_site_from_urls_nebula(self) -> None:
        """Test Nebula site detection."""
        assert detect_site_from_urls(["https://watchnebula.com/video"]) == "nebula"
        assert detect_site_from_urls(["https://nebula.tv/video"]) == "nebula"

    def test_detect_site_from_urls_unknown(self) -> None:
        """Test unknown site detection."""
        assert detect_site_from_urls(["https://example.com"]) == "unknown"
        assert detect_site_from_urls([]) == "unknown"

    def test_detect_site_from_urls_rejects_spoofed_host(self) -> None:
        """Domain names outside the hostname (path, query, lookalike host) don't count."""
        assert detect_site_from_urls(["https://evil.com/youtube.com"]) == "unknown"
        assert detect_site_from_urls(["https://evil.com/?u=youtu.be"]) == "unknown"
        assert detect_site_from_urls(["https://notyoutube.com/x"]) == "unknown"
        assert detect_site_from_urls(["https://www.youtube.com/x"]) == "youtube"

    def test_is_primitive_technology_channel(self) -> None:
        """Test Primitive Technology channel detection."""
        assert is_primitive_technology({"channel": "Primitive Technology"})
        assert is_primitive_technology({"uploader": "Primitive Technology"})
        assert is_primitive_technology({"uploader_id": "PRIMITIVE TECHNOLOGY"})

    def test_is_primitive_technology_title(self) -> None:
        """Test Primitive Technology title prefix detection."""
        assert is_primitive_technology({"title": "Primitive Technology: Building"})
        assert is_primitive_technology({"title": "primitive technology: something"})

    def test_is_primitive_technology_not(self) -> None:
        """Test non-Primitive Technology detection."""
        assert not is_primitive_technology({"channel": "Other Channel"})
        assert not is_primitive_technology({"title": "Some Other Video"})
        assert not is_primitive_technology({})


class TestPathUtils:
    """Tests for path_utils module."""

    def test_sanitize_for_path_removes_invalid(self) -> None:
        """Test invalid character removal."""
        assert sanitize_for_path("my<file>") == "my_file_"
        assert sanitize_for_path('my"folder"') == "my_folder_"
        assert sanitize_for_path("path:file") == "path_file"

    def test_sanitize_for_path_empty(self) -> None:
        """Test empty input handling."""
        assert sanitize_for_path("") == "misc"
        assert sanitize_for_path("   ") == "misc"

    def test_sanitize_for_path_preserves_valid(self) -> None:
        """Test valid characters are preserved."""
        assert sanitize_for_path("my_folder-123") == "my_folder-123"
        assert sanitize_for_path("My Folder123") == "My Folder123"

    def test_slugify_if_too_long_short_path(self) -> None:
        """Test slugify with short path."""
        result = slugify_if_too_long("/base", "label")
        assert result == "label"

    def test_slugify_if_too_long_long_path(self) -> None:
        """Test slugify with very long path."""
        long_label = "a" * 200
        result = slugify_if_too_long("/base", long_label, max_total=50)
        # Should create a slug
        assert len(result) < len(long_label)
        assert "-" in result or result == "misc"

    def test_resolve_playlist_label_from_title(self) -> None:
        """Test label resolution from title."""
        info = {"title": "My Playlist"}
        assert resolve_playlist_label(info, "url") == "My Playlist"

    def test_resolve_playlist_label_from_uploader(self) -> None:
        """Test label resolution from uploader."""
        info = {"uploader": "Channel Name"}
        assert resolve_playlist_label(info, "url") == "Channel Name"

    def test_resolve_playlist_label_fallback(self) -> None:
        """Test label resolution fallback."""
        info = {}
        # Should not crash and return something
        result = resolve_playlist_label(info, "https://example.com/path")
        assert isinstance(result, str)
        assert len(result) > 0

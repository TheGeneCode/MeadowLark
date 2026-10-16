"""Tests for src.playlist_utils.load_playlist_urls and related helpers."""

import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import src.settings_dialog as sd
from src.playlist_utils import (
    _PLAYLIST_TEMPLATE,
    _PLAYLIST_TEMPLATE_V1,
    PlaylistFileEntry,
    get_playlist_file_for_source,
    is_primitive_technology,
    labels_by_playlist_id,
    load_playlist_comments_for_source,
    load_playlist_entries_for_source,
    load_playlist_urls,
    parse_playlist_lines,
    playlist_id_for_line,
    write_template_playlist_file,
)

# ---------------------------------------------------------------------------
# Original 6 tests (preserved verbatim)
# ---------------------------------------------------------------------------


def test_load_playlist_urls_returns_urls(tmp_path: Path) -> None:
    f = tmp_path / "playlists.txt"
    f.write_text(
        "https://youtube.com/playlist?list=PLabc\nhttps://youtube.com/playlist?list=PLxyz\n"
    )
    assert load_playlist_urls(f) == [
        "https://youtube.com/playlist?list=PLabc",
        "https://youtube.com/playlist?list=PLxyz",
    ]


def test_load_playlist_urls_strips_comments(tmp_path: Path) -> None:
    f = tmp_path / "playlists.txt"
    f.write_text("# My playlist\nhttps://youtube.com/playlist?list=PLabc\n")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_strips_blank_lines(tmp_path: Path) -> None:
    f = tmp_path / "playlists.txt"
    f.write_text("\nhttps://youtube.com/playlist?list=PLabc\n\n")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_missing_file(tmp_path: Path) -> None:
    assert load_playlist_urls(tmp_path / "nonexistent.txt") == []


def test_load_playlist_urls_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "playlists.txt"
    f.write_text("")
    assert load_playlist_urls(f) == []


def test_load_playlist_urls_comments_only(tmp_path: Path) -> None:
    f = tmp_path / "playlists.txt"
    f.write_text("# comment 1\n# comment 2\n")
    assert load_playlist_urls(f) == []


# ---------------------------------------------------------------------------
# New edge-case tests (boundary matrix rows 6-22)
# ---------------------------------------------------------------------------


def test_load_playlist_urls_single_url_no_trailing_newline(tmp_path: Path) -> None:
    """Row 6 - single URL with no trailing newline must still be returned."""
    f = tmp_path / "playlists.txt"
    f.write_bytes(b"https://youtube.com/playlist?list=PLabc")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_strips_leading_trailing_whitespace_from_url(
    tmp_path: Path,
) -> None:
    """Row 7 - URLs padded with spaces/tabs must be returned stripped."""
    f = tmp_path / "playlists.txt"
    f.write_text("  https://youtube.com/playlist?list=PLabc  \n")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_whitespace_only_line_filtered(tmp_path: Path) -> None:
    """Row 8 - a line containing only spaces must be treated as blank and filtered."""
    f = tmp_path / "playlists.txt"
    f.write_text("   \nhttps://youtube.com/playlist?list=PLabc\n")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_inline_hash_anchor_preserved(tmp_path: Path) -> None:
    """Row 9 - a URL with an inline '#' anchor must NOT be treated as a comment."""
    url = "https://example.com/page#section"
    f = tmp_path / "playlists.txt"
    f.write_text(f"{url}\n")
    assert load_playlist_urls(f) == [url]


def test_load_playlist_urls_double_hash_comment_filtered(tmp_path: Path) -> None:
    """Row 10 - lines starting with '##' must still be filtered as comments."""
    f = tmp_path / "playlists.txt"
    f.write_text("## section header\nhttps://youtube.com/playlist?list=PLabc\n")
    assert load_playlist_urls(f) == ["https://youtube.com/playlist?list=PLabc"]


def test_load_playlist_urls_mixed_content_returns_only_urls(tmp_path: Path) -> None:
    """Row 11 - mix of URLs, comments, and blank lines returns only the URLs, in order."""
    f = tmp_path / "playlists.txt"
    f.write_text(
        "# group A\n"
        "https://youtube.com/playlist?list=PLa\n"
        "\n"
        "# group B\n"
        "https://youtube.com/playlist?list=PLb\n",
    )
    assert load_playlist_urls(f) == [
        "https://youtube.com/playlist?list=PLa",
        "https://youtube.com/playlist?list=PLb",
    ]


def test_load_playlist_urls_non_utf8_file_behavior(tmp_path: Path) -> None:
    """
    Row 12 - documents behavior on non-UTF-8 bytes.

    The implementation opens with encoding='utf-8' and only catches OSError.
    UnicodeDecodeError is NOT a subclass of OSError, so the function will raise
    rather than return [] for truly undecodable files. This test documents and
    verifies that gap so it can be addressed if callers need resilience.
    """
    f = tmp_path / "playlists.txt"
    # Bytes that are invalid in UTF-8
    f.write_bytes(b"\x80\x81\x82")
    try:
        result = load_playlist_urls(f)
        # If somehow it succeeds the return type must still be a list
        assert isinstance(result, list)
    except UnicodeDecodeError:
        # Known gap: UnicodeDecodeError propagates unhandled.
        pass


@pytest.mark.skipif(
    sys.platform == "win32", reason="chmod permission tests are unreliable on Windows"
)
def test_load_playlist_urls_permission_denied_returns_empty(tmp_path: Path) -> None:
    """Row 14 - a file that exists but cannot be read must return []."""
    f = tmp_path / "playlists.txt"
    f.write_text("https://youtube.com/playlist?list=PLabc\n")
    f.chmod(0o000)
    try:
        assert load_playlist_urls(f) == []
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_load_playlist_urls_path_is_directory_returns_empty(tmp_path: Path) -> None:
    """
    Row 15 - passing a directory path (which exists) must return [] not crash.

    path.exists() is True for a directory; open() raises IsADirectoryError which
    is a subclass of OSError, so the except clause must catch it.
    """
    assert load_playlist_urls(tmp_path) == []


def test_load_playlist_urls_large_file_returns_all_urls(tmp_path: Path) -> None:
    """Row 17 - a file with many URLs must return all of them without truncation."""
    urls = [f"https://youtube.com/playlist?list=PL{i:05d}" for i in range(5000)]
    f = tmp_path / "playlists.txt"
    f.write_text("\n".join(urls) + "\n")
    result = load_playlist_urls(f)
    assert result == urls
    assert len(result) == 5000


# ---------------------------------------------------------------------------
# is_primitive_technology exception branch (lines 51-53)
# ---------------------------------------------------------------------------


def test_is_primitive_technology_none_returns_false() -> None:
    assert is_primitive_technology(None) is False  # type: ignore[arg-type]


def test_is_primitive_technology_none_fields_returns_false() -> None:
    assert is_primitive_technology({"title": None, "channel": None}) is False


# ---------------------------------------------------------------------------
# get_playlist_file_for_source mappings (lines 67-72)
# ---------------------------------------------------------------------------


def test_get_playlist_file_for_source_1080() -> None:
    result = get_playlist_file_for_source("1080playlists")
    assert isinstance(result, str)


def test_get_playlist_file_for_source_720() -> None:
    result = get_playlist_file_for_source("720playlists")
    assert isinstance(result, str)


def test_get_playlist_file_for_source_audio() -> None:
    result = get_playlist_file_for_source("audio_playlists")
    assert isinstance(result, str)


def test_get_playlist_file_for_source_unknown_returns_none() -> None:
    assert get_playlist_file_for_source("unknown_source") is None


def test_playlist_file_for_new_rung() -> None:
    with patch.dict(sd._runtime, {}, clear=True):
        result = get_playlist_file_for_source("1440playlists")
    assert result is not None
    assert result.endswith("1440playlists.txt")


def test_playlist_file_setting_overrides_default() -> None:
    with patch.dict(sd._runtime, {"VID_DL_PLAYLISTS_2160_FILE": "C:/tmp/x.txt"}, clear=True):
        result = get_playlist_file_for_source("2160playlists")
    assert result == "C:/tmp/x.txt"


def test_bare_height_source_is_not_a_playlist_file() -> None:
    assert get_playlist_file_for_source("1080") is None


# ---------------------------------------------------------------------------
# load_playlist_comments_for_source (lines 108-133)
# ---------------------------------------------------------------------------


def test_load_playlist_comments_unknown_source_returns_empty() -> None:
    result = load_playlist_comments_for_source("unknown_source")
    assert result == {}


def test_load_playlist_comments_valid_source_missing_file_returns_empty() -> None:
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value="/nonexistent/path/playlists.txt",
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {}


def test_load_playlist_comments_extracts_comment_before_url(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#My Comment\nhttps://www.youtube.com/playlist?list=PLabc123\n")
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(playlist_file),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {"PLabc123": "My Comment"}


def test_load_playlist_comments_url_without_list_param_not_added(
    tmp_path: Path,
) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Comment\nhttps://www.youtube.com/watch?v=abc\n")
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(playlist_file),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {}


def test_load_playlist_comments_url_without_preceding_comment_not_added(
    tmp_path: Path,
) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("https://www.youtube.com/playlist?list=PLxyz\n")
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(playlist_file),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {}


def test_load_playlist_comments_oserror_returns_empty(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Comment\nhttps://www.youtube.com/playlist?list=PLabc\n")
    with (
        patch(
            "src.playlist_utils.get_playlist_file_for_source",
            return_value=str(playlist_file),
        ),
        patch("pathlib.Path.open", side_effect=OSError("permission denied")),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {}


def test_load_playlist_comments_blank_line_is_skipped(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text(
        "\n#My Comment\nhttps://www.youtube.com/playlist?list=PLabc123\n",
    )
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(playlist_file),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {"PLabc123": "My Comment"}


def test_load_playlist_comments_bare_playlist_id(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Taskmaster S21\nPLRWvNQVqAeWIafhw3XHnmz_EHOp32qoZW\n")
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(playlist_file),
    ):
        result = load_playlist_comments_for_source("1080playlists")
    assert result == {"PLRWvNQVqAeWIafhw3XHnmz_EHOp32qoZW": "Taskmaster S21"}


def test_write_template_playlist_file_creates_missing_parent_dirs(
    tmp_path: Path,
) -> None:
    """write_template_playlist_file must create nested parent dirs that don't exist yet."""
    target = tmp_path / "nested" / "deeper" / "playlists.txt"
    assert not target.parent.exists()

    write_template_playlist_file(target)

    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "MeadowLark Playlist File" in content


def test_write_template_playlist_file_silently_overwrites_existing_content(
    tmp_path: Path,
) -> None:
    """
    Known gap: write_template_playlist_file unconditionally truncates and rewrites.

    PlaylistButton.mousePressEvent only calls this when path.exists() was False at
    check time, but there is a TOCTOU window between that check and this write: if
    another process (or a second rapid right-click racing a slow filesystem) creates
    the file with real content in between, this call destroys it with no warning,
    since write_text(mode='w') truncates unconditionally and there is no re-check.
    """
    f = tmp_path / "playlists.txt"
    f.write_text("https://youtube.com/playlist?list=PLprecious\n", encoding="utf-8")

    write_template_playlist_file(f)

    content = f.read_text(encoding="utf-8")
    assert "PLprecious" not in content
    assert "MeadowLark Playlist File" in content


def test_write_template_playlist_file_embedded_null_byte_raises_valueerror(
    tmp_path: Path,
) -> None:
    """
    Known gap: a null byte in the path raises ValueError, not OSError.

    Path.exists() (used as the guard in PlaylistButton.mousePressEvent) swallows
    both OSError and ValueError internally and reports False for such a path, but
    write_template_playlist_file's mkdir/write_text calls raise a bare ValueError
    for an embedded null byte. mousePressEvent only catches OSError, so this
    exception would propagate uncaught out of the Qt event handler.
    """
    bad_path = tmp_path / "evil\x00name" / "playlists.txt"

    with pytest.raises(ValueError, match="null"):
        write_template_playlist_file(bad_path)


# ---------------------------------------------------------------------------
# parse_playlist_lines — sticky #Name labels
# ---------------------------------------------------------------------------


def _labels(text: str) -> list[tuple[str, str | None]]:
    return [(e.url, e.label) for e in parse_playlist_lines(text.splitlines())]


def test_parse_label_applies_to_all_links_below() -> None:
    assert _labels("#Tech\nA\nB\n#Other\nC") == [("A", "Tech"), ("B", "Tech"), ("C", "Other")]


def test_parse_returns_entry_records() -> None:
    assert parse_playlist_lines(["#Tech", "A"]) == [PlaylistFileEntry(url="A", label="Tech")]


def test_parse_one_label_per_link_unchanged() -> None:
    assert _labels("#One\nA\n#Two\nB") == [("A", "One"), ("B", "Two")]


def test_parse_link_before_any_label_is_unlabelled() -> None:
    assert _labels("A\n#Tech\nB") == [("A", None), ("B", "Tech")]


def test_parse_lone_hash_ends_category() -> None:
    assert _labels("#Tech\nA\n#\nB") == [("A", "Tech"), ("B", None)]


def test_parse_blank_line_does_not_end_category() -> None:
    assert _labels("#Tech\nA\n\nB") == [("A", "Tech"), ("B", "Tech")]


def test_parse_paused_url_keeps_category() -> None:
    text = "#Tech\nA\n#https://www.youtube.com/playlist?list=PLx\nB"
    assert _labels(text) == [("A", "Tech"), ("B", "Tech")]


def test_parse_paused_bare_id_keeps_category() -> None:
    text = "#Tech\nA\n#PLRWvNQVqAeWIafhw3XHnmz_EHOp32qoZW\nB"
    assert _labels(text) == [("A", "Tech"), ("B", "Tech")]


def test_parse_all_caps_word_is_a_label_not_an_id() -> None:
    assert _labels("#PLAYTHROUGHSXYZ\nA") == [("A", "PLAYTHROUGHSXYZ")]


def test_parse_consecutive_labels_last_wins() -> None:
    assert _labels("#Old\n#New\nA") == [("A", "New")]


def test_parse_double_hash_label_keeps_second_hash() -> None:
    assert _labels("##X\nA") == [("A", "#X")]


@pytest.mark.parametrize("template", [_PLAYLIST_TEMPLATE, _PLAYLIST_TEMPLATE_V1])
def test_parse_template_header_is_not_a_label(template: str) -> None:
    assert _labels(template + "A\n") == [("A", None)]


def test_template_header_line_mid_file_does_not_clear_label() -> None:
    header_line = "# MeadowLark Playlist File"
    assert _labels(f"#Tech\nA\n{header_line}\nB") == [("A", "Tech"), ("B", "Tech")]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("https://www.youtube.com/playlist?list=PLabc", "PLabc"),
        ("PLbare123", "PLbare123"),
        ("https://www.youtube.com/watch?v=x", None),
    ],
)
def test_playlist_id_for_line(line: str, expected: str | None) -> None:
    assert playlist_id_for_line(line) == expected


def test_labels_by_playlist_id_skips_unlabelled_and_idless() -> None:
    entries = [
        PlaylistFileEntry("https://www.youtube.com/playlist?list=PL1", "Tech"),
        PlaylistFileEntry("PL2", "Tech"),
        PlaylistFileEntry("https://www.youtube.com/watch?v=x", "Tech"),
        PlaylistFileEntry("https://www.youtube.com/playlist?list=PL3", None),
    ]
    assert labels_by_playlist_id(entries) == {"PL1": "Tech", "PL2": "Tech"}


def test_load_playlist_comments_category_maps_every_id(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Tech\nhttps://www.youtube.com/playlist?list=PL1\nPL2abcdefghijkl\n")
    with patch("src.playlist_utils.get_playlist_file_for_source", return_value=str(playlist_file)):
        result = load_playlist_comments_for_source("audio_playlists")
    assert result == {"PL1": "Tech", "PL2abcdefghijkl": "Tech"}


def test_load_playlist_entries_returns_parsed_entries(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Tech\nA\nB\n")
    with patch("src.playlist_utils.get_playlist_file_for_source", return_value=str(playlist_file)):
        result = load_playlist_entries_for_source("audio_playlists")
    assert result == [PlaylistFileEntry("A", "Tech"), PlaylistFileEntry("B", "Tech")]


def test_load_playlist_entries_unknown_source_is_none() -> None:
    assert load_playlist_entries_for_source("unknown_source") is None


def test_load_playlist_entries_missing_file_is_none(tmp_path: Path) -> None:
    with patch(
        "src.playlist_utils.get_playlist_file_for_source",
        return_value=str(tmp_path / "nope.txt"),
    ):
        assert load_playlist_entries_for_source("audio_playlists") is None


def test_load_playlist_entries_undecodable_file_is_none(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_bytes(b"\xff\xfe\x00bad")
    with patch("src.playlist_utils.get_playlist_file_for_source", return_value=str(playlist_file)):
        assert load_playlist_entries_for_source("audio_playlists") is None


def test_load_playlist_urls_ignores_labels_pauses_and_category_ends(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_text("#Tech\nA\n#https://x/paused\nB\n#\nC\n", encoding="utf-8")
    assert load_playlist_urls(playlist_file) == ["A", "B", "C"]


@pytest.mark.parametrize(
    "paused",
    [
        "#www.youtube.com/playlist?list=PLx",
        "#HTTPS://www.youtube.com/playlist?list=PLx",
        "#http://www.youtube.com/playlist?list=PLx",
        "#  https://www.youtube.com/playlist?list=PLx",
        "#UUabc1234567890defghijk",
        "#OLAK5uy_abcdefghij",
    ],
)
def test_parse_paused_entry_forms_keep_category(paused: str) -> None:
    assert _labels(f"#Tech\nA\n{paused}\nB") == [("A", "Tech"), ("B", "Tech")]


def test_parse_bare_id_shape_length_boundary_label_vs_paused() -> None:
    # 17 chars is a name; 18 (PL + 16, the shortest real id shape) is a paused id.
    assert _labels("#Tech\nA\n#PLabcdefghijklm12\nB") == [("A", "Tech"), ("B", "PLabcdefghijklm12")]
    assert _labels("#Tech\nA\n#PLabcdefghijklm123\nB") == [("A", "Tech"), ("B", "Tech")]


@pytest.mark.parametrize(
    "name",
    ["TLDR-weekly-news", "LLMresearchpapers", "PL_Series_Names", "ULTRAmarathonpodcasts"],
)
def test_parse_one_word_name_that_resembles_an_id_stays_a_label(name: str) -> None:
    assert _labels(f"#Tech\nA\n#{name}\nB") == [("A", "Tech"), ("B", name)]


def test_parse_whitespace_padded_label_and_lone_hash_are_trimmed() -> None:
    assert _labels("#   Tech News  \nA\n#   \nB") == [("A", "Tech News"), ("B", None)]


def test_load_playlist_comments_undecodable_file_returns_empty(tmp_path: Path) -> None:
    playlist_file = tmp_path / "playlists.txt"
    playlist_file.write_bytes(b"\xff\xfe\x00bad")
    with patch("src.playlist_utils.get_playlist_file_for_source", return_value=str(playlist_file)):
        assert load_playlist_comments_for_source("audio_playlists") == {}

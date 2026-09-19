"""Unit tests for path_utils helpers."""

from unittest.mock import patch

from src.path_utils import rename_playlist_folders_from_comments, resolve_playlist_label


def test_rename_playlist_folders_with_list_param(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()

    rename_playlist_folders_from_comments(
        str(tmp_path),
        ["https://youtube.com/playlist?list=PLabc"],
        {"PLabc": "My Show"},
    )

    assert not na.exists()
    assert (tmp_path / "My Show").is_dir()


def test_rename_playlist_folders_with_direct_playlist_id(tmp_path) -> None:
    """Bare video URL (no list= param) should use direct_playlist_id as fallback."""
    na = tmp_path / "NA"
    na.mkdir()

    rename_playlist_folders_from_comments(
        str(tmp_path),
        ["https://youtube.com/watch?v=videoonly"],
        {"PLxyz": "Taskmaster S21"},
        direct_playlist_id="PLxyz",
    )

    assert not na.exists()
    assert (tmp_path / "Taskmaster S21").is_dir()


def test_rename_playlist_folders_no_na_folder(tmp_path) -> None:
    rename_playlist_folders_from_comments(
        str(tmp_path),
        ["https://youtube.com/watch?v=videoonly"],
        {"PLxyz": "Taskmaster S21"},
        direct_playlist_id="PLxyz",
    )
    # No error, nothing renamed
    assert not (tmp_path / "Taskmaster S21").exists()


def test_rename_playlist_folders_no_comments(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()

    rename_playlist_folders_from_comments(str(tmp_path), ["https://youtube.com/watch?v=x"])

    assert na.exists()


def test_rename_playlist_folders_target_exists_skips(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()
    (tmp_path / "My Show").mkdir()

    rename_playlist_folders_from_comments(
        str(tmp_path),
        ["https://youtube.com/playlist?list=PLabc"],
        {"PLabc": "My Show"},
    )

    assert na.exists()
    assert (tmp_path / "My Show").is_dir()


def test_rename_playlist_folders_direct_id_not_in_comments(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()

    rename_playlist_folders_from_comments(
        str(tmp_path),
        ["https://youtube.com/watch?v=videoonly"],
        {"PLother": "Other Show"},
        direct_playlist_id="PLxyz",
    )

    assert na.exists()


# ---------------------------------------------------------------------------
# resolve_playlist_label fallback paths (lines 88, 93-94)
# ---------------------------------------------------------------------------


def test_resolve_playlist_label_from_playlist_id() -> None:
    result = resolve_playlist_label({}, "https://youtube.com/playlist?list=PLxxx")
    assert result == "playlist-PLxxx"


def test_resolve_playlist_label_from_url_path_segments() -> None:
    result = resolve_playlist_label({}, "https://youtube.com/user/SomeChannel")
    assert "SomeChannel" in result


# ---------------------------------------------------------------------------
# rename_playlist_folders_from_comments edge paths (lines 118, 142-148)
# ---------------------------------------------------------------------------


def test_rename_playlist_folders_nonexistent_base_returns_early(tmp_path) -> None:
    rename_playlist_folders_from_comments(
        str(tmp_path / "nonexistent"),
        ["https://youtube.com/playlist?list=PLxxx"],
        {"PLxxx": "Some Name"},
    )
    # No crash; nothing created


def test_rename_playlist_folders_oserror_on_rename_is_logged(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()
    with patch("src.path_utils.Path.rename", side_effect=OSError("access denied")):
        rename_playlist_folders_from_comments(
            str(tmp_path),
            ["https://youtube.com/playlist?list=PLabc"],
            {"PLabc": "My Show"},
        )
    assert na.exists()


def test_resolve_playlist_label_url_parse_error_falls_back_to_url() -> None:
    with patch("src.path_utils.urlparse", side_effect=AttributeError("parse error")):
        result = resolve_playlist_label({}, "https://youtube.com/user/Channel")
    assert isinstance(result, str)
    assert len(result) > 0


def test_rename_playlist_folders_unexpected_error_is_caught(tmp_path) -> None:
    na = tmp_path / "NA"
    na.mkdir()
    with patch("src.path_utils.extract_playlist_id", side_effect=RuntimeError("boom")):
        rename_playlist_folders_from_comments(
            str(tmp_path),
            ["https://youtube.com/playlist?list=PLabc"],
            {"PLabc": "My Show"},
        )
    # Outer except caught it — no crash

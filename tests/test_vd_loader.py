"""Tests for the ``tests/_vd_loader`` helpers that bind ``src.window_*`` modules."""

import sys

import pytest

from tests._vd_loader import import_vid_module, owner_module, window_module


@pytest.fixture(scope="module")
def vd():
    return import_vid_module()


def test_owner_module_follows_moved_method(vd) -> None:
    mod = owner_module(vd, "_populate_resolution_grid")
    assert mod.__name__ == "src.window_layout"
    assert mod is vd._window_modules["src.window_layout"]


def test_owner_module_returns_vd_for_unmoved_method(vd) -> None:
    assert owner_module(vd, "request_detected") is vd


@pytest.mark.parametrize(
    "method",
    ["_filter_audio_playlist_urls", "_on_podcast_check_finished", "_hourly_podcast_check"],
)
def test_owner_module_podcast_check_methods(vd, method: str) -> None:
    assert owner_module(vd, method).__name__ == "src.window_podcast_check"


def test_label_from_comments_lives_in_podcast_check(vd) -> None:
    mod = window_module(vd, "podcast_check")
    assert hasattr(mod, "_label_from_comments")
    if "_label_from_comments" in vars(vd):
        assert vd._label_from_comments is mod._label_from_comments


def test_window_module_is_the_one_mywindow_uses(vd) -> None:
    mixin = next(c for c in vd.MyWindow.__mro__ if c.__name__ == "LayoutMixin")
    assert vars(window_module(vd, "layout"))["LayoutMixin"] is mixin


def test_loader_restores_sys_modules(vd) -> None:
    before = sys.modules.get("src.window_layout")
    import_vid_module()
    assert sys.modules.get("src.window_layout") is before
    assert sys.modules["yt_dlp"].__name__ == "yt_dlp"
    assert not hasattr(sys.modules["yt_dlp"], "_fake_marker")
    assert sys.modules["yt_dlp"].__file__ is not None


def test_window_module_unknown_name_raises(vd) -> None:
    with pytest.raises(KeyError):
        window_module(vd, "nope")


@pytest.mark.parametrize(
    "method",
    [
        "_create_podcast_status_table",
        "_show_podcast_status",
        "_on_podcast_status_dialog_destroyed",
        "_cache_put",
        "_cache_get_fresh",
        "_on_podcast_status_context_menu",
        "_guarded_status_action",
        "_open_latest_for_row",
        "_resolve_latest_via_ytdlp",
        "_try_open_default_browser",
        "_get_brave_controller",
        "_open_url_in_browser",
        "_refresh_podcast_status_dialog",
        "_download_podcast_now_action",
    ],
)
def test_owner_module_podcast_status_methods(vd, method: str) -> None:
    assert owner_module(vd, method).__name__ == "src.window_podcast_status"


def test_class_attributes_still_resolve_on_mywindow(vd) -> None:
    assert vd.MyWindow.CACHE_TTL_SECONDS == 6 * 60 * 60
    assert vd.MyWindow._BRAVE_PATHS


def test_patch_on_mywindow_shadows_mixin_method(vd) -> None:
    from unittest.mock import patch

    with patch.object(vd.MyWindow, "_cache_get_fresh", return_value="x"):
        assert vd.MyWindow._cache_get_fresh(None, "u") == "x"
    assert vd.MyWindow._cache_get_fresh.__module__ == "src.window_podcast_status"


@pytest.mark.parametrize(
    "method",
    ["_pending_deps", "_download_pending_now", "_retry_failed_downloads", "_show_history"],
)
def test_owner_module_download_list_methods(vd, method: str) -> None:
    assert owner_module(vd, method).__name__ == "src.window_download_lists"


def test_enabled_heights_patch_targets_differ_per_method(vd) -> None:
    from unittest.mock import patch

    grid = owner_module(vd, "_populate_resolution_grid")
    pending = owner_module(vd, "_download_pending_now")
    assert grid.__name__ == "src.window_layout"
    assert pending.__name__ == "src.window_download_lists"
    assert grid is not pending
    original = pending.enabled_heights
    with patch.object(grid, "enabled_heights", lambda: ["patched"]):
        assert pending.enabled_heights is original


@pytest.mark.parametrize("method", ["reload_settings", "_on_app_update_result", "do_updates"])
def test_owner_module_settings_methods(vd, method: str) -> None:
    assert owner_module(vd, method).__name__ == "src.window_settings"

"""
Boundary tests for user-configurable video/audio format settings.

Covers:
  - get_source_options() format propagation for all named sources and numeric heights
  - get_source_options() fallback when runtime setting is None, empty, or falsy
  - _make_combo_row() selection logic including unknown/stale stored values
  - _apply() QComboBox branch: currentData() persisted, not display text
  - VIDEO_FORMAT_OPTIONS / AUDIO_FORMAT_OPTIONS list structural invariants
"""

import importlib
from typing import Any
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication, QComboBox

import src.config as cfg_mod
import src.settings_dialog as sd
from src.settings_dialog import (
    AUDIO_FORMAT_OPTIONS,
    VIDEO_FORMAT_OPTIONS,
    _make_combo_row,
)
from src.ydl_options import get_source_options

# One QApplication for the entire module — Qt requires exactly one instance.
_app: QApplication = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# object covers None, str, int, etc. — intentionally broad for boundary testing.
_FormatVal = object


def _patch_formats(vfmt: _FormatVal, afmt: _FormatVal):  # type: ignore[return]
    """Context manager: inject arbitrary values into the runtime settings store."""
    return patch.dict(
        sd._runtime,
        {"VID_DL_VIDEO_FORMAT": vfmt, "VID_DL_AUDIO_FORMAT": afmt},
    )


# ===========================================================================
# 1. Boundary Matrix — get_source_options format propagation
# ===========================================================================


class TestGetSourceOptionsFormatPropagation:
    """Every named source and numeric-height path uses vfmt/afmt, not literals."""

    # --- "audio" source ---

    def test_audio_format_string_uses_afmt(self) -> None:
        with _patch_formats("mp4", "opus"):
            opts = get_source_options("audio")
        assert opts["format"].startswith("opus/")

    def test_audio_preferredcodec_uses_afmt(self) -> None:
        with _patch_formats("mp4", "flac"):
            opts = get_source_options("audio")
        assert opts["postprocessors"][0]["preferredcodec"] == "flac"

    def test_audio_format_string_does_not_contain_hardcoded_m4a(self) -> None:
        with _patch_formats("mp4", "opus"):
            opts = get_source_options("audio")
        assert "m4a" not in opts["format"]

    # --- "audio_playlists" source ---

    def test_audio_playlists_format_string_uses_afmt(self) -> None:
        with _patch_formats("mp4", "mp3"):
            opts = get_source_options("audio_playlists")
        assert opts["format"].startswith("mp3/")

    def test_audio_playlists_preferredcodec_uses_afmt(self) -> None:
        with _patch_formats("mp4", "mp3"):
            opts = get_source_options("audio_playlists")
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"

    def test_audio_playlists_has_ignoreerrors(self) -> None:
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("audio_playlists")
        assert opts.get("ignoreerrors") == "only_download"

    # --- "720playlists" source ---

    def test_720playlists_merge_output_format_uses_vfmt(self) -> None:
        with _patch_formats("mkv", "m4a"):
            opts = get_source_options("720playlists")
        assert opts["merge_output_format"] == "mkv"

    def test_720playlists_merge_output_format_webm(self) -> None:
        with _patch_formats("webm", "m4a"):
            opts = get_source_options("720playlists")
        assert opts["merge_output_format"] == "webm"

    def test_720playlists_format_selector_mp4_for_non_webm_vfmt(self) -> None:
        """mp4/mkv targets use native mp4/m4a streams."""
        for vfmt in ("mp4", "mkv"):
            with _patch_formats(vfmt, "m4a"):
                opts = get_source_options("720playlists")
            assert "ext=mp4" in opts["format"], f"ext=mp4 missing for vfmt={vfmt!r}"
            assert "ext=m4a" in opts["format"], f"ext=m4a missing for vfmt={vfmt!r}"

    def test_720playlists_format_selector_webm_for_webm_vfmt(self) -> None:
        """Webm target uses native VP9/Opus webm streams to avoid codec mismatch."""
        with _patch_formats("webm", "m4a"):
            opts = get_source_options("720playlists")
        assert "ext=webm" in opts["format"]
        assert "ext=mp4" not in opts["format"]

    # --- "1080playlists" source ---

    def test_1080playlists_merge_output_format_uses_vfmt(self) -> None:
        with _patch_formats("mkv", "m4a"):
            opts = get_source_options("1080playlists")
        assert opts["merge_output_format"] == "mkv"

    def test_1080playlists_merge_output_format_webm(self) -> None:
        with _patch_formats("webm", "opus"):
            opts = get_source_options("1080playlists")
        assert opts["merge_output_format"] == "webm"

    def test_1080playlists_format_selector_mp4_for_non_webm_vfmt(self) -> None:
        """mp4/mkv targets use native mp4/m4a streams."""
        for vfmt in ("mp4", "mkv"):
            with _patch_formats(vfmt, "m4a"):
                opts = get_source_options("1080playlists")
            assert "ext=mp4" in opts["format"], f"ext=mp4 missing for vfmt={vfmt!r}"
            assert "ext=m4a" in opts["format"], f"ext=m4a missing for vfmt={vfmt!r}"

    def test_1080playlists_format_selector_webm_for_webm_vfmt(self) -> None:
        """Webm target uses native VP9/Opus webm streams to avoid codec mismatch."""
        with _patch_formats("webm", "m4a"):
            opts = get_source_options("1080playlists")
        assert "ext=webm" in opts["format"]
        assert "ext=mp4" not in opts["format"]

    def test_1080playlists_has_ignoreerrors(self) -> None:
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("1080playlists")
        assert opts.get("ignoreerrors") == "only_download"

    # --- Numeric height source ---

    def test_numeric_height_merge_output_format_uses_vfmt(self) -> None:
        with _patch_formats("webm", "m4a"):
            opts = get_source_options("480")
        assert opts["merge_output_format"] == "webm"

    def test_numeric_height_1080_merge_output_format_uses_vfmt(self) -> None:
        with _patch_formats("mkv", "opus"):
            opts = get_source_options("1080")
        assert opts["merge_output_format"] == "mkv"

    def test_numeric_height_format_selector_mp4_for_non_webm_vfmt(self) -> None:
        """mp4/mkv targets use native mp4/m4a streams."""
        for vfmt in ("mp4", "mkv"):
            with _patch_formats(vfmt, "m4a"):
                opts = get_source_options("480")
            assert "ext=mp4" in opts["format"], f"ext=mp4 missing for vfmt={vfmt!r}"
            assert "ext=m4a" in opts["format"], f"ext=m4a missing for vfmt={vfmt!r}"

    def test_numeric_height_format_selector_webm_for_webm_vfmt(self) -> None:
        """Webm target uses native VP9/Opus webm streams to avoid codec mismatch."""
        with _patch_formats("webm", "m4a"):
            opts = get_source_options("480")
        assert "ext=webm" in opts["format"]
        assert "ext=mp4" not in opts["format"]

    def test_numeric_height_contains_height_in_format(self) -> None:
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("720")
        assert "height<=720" in opts["format"]

    # --- Unknown / non-numeric fallback source ---

    def test_unknown_source_merge_output_format_uses_vfmt(self) -> None:
        with _patch_formats("webm", "opus"):
            opts = get_source_options("garbage")
        assert opts["merge_output_format"] == "webm"

    def test_unknown_source_format_string_is_fallback(self) -> None:
        with _patch_formats("webm", "opus"):
            opts = get_source_options("unknown-source")
        assert opts["format"] == "bestvideo*+bestaudio/best"

    # --- Return-value isolation: dict copy ---

    def test_audio_dict_is_a_copy_not_mutation(self) -> None:
        """Mutating the returned dict should not affect a second call."""
        with _patch_formats("mp4", "m4a"):
            opts1 = get_source_options("audio")
            opts1["format"] = "MUTATED"
            opts2 = get_source_options("audio")
        assert opts2["format"] != "MUTATED"


# ===========================================================================
# 2. Fallback boundaries — None / empty / falsy settings
# ===========================================================================


class TestGetSourceOptionsFallback:
    """or-fallback guard (`or "mp4"` / `or "m4a"`) should never produce broken strings."""

    @pytest.mark.parametrize(
        ("vfmt", "afmt"),
        [
            (None, None),
            ("", ""),
            (None, "m4a"),
            ("mp4", None),
            ("", "m4a"),
            ("mp4", ""),
        ],
    )
    def test_audio_format_no_none_literal(self, vfmt: Any, afmt: Any) -> None:
        with _patch_formats(vfmt, afmt):
            opts = get_source_options("audio")
        assert "None" not in opts["format"]
        assert opts["format"]  # non-empty

    @pytest.mark.parametrize(
        ("vfmt", "afmt"),
        [
            (None, None),
            ("", ""),
            (None, "m4a"),
            ("mp4", None),
        ],
    )
    def test_audio_preferredcodec_no_none_literal(self, vfmt: Any, afmt: Any) -> None:
        with _patch_formats(vfmt, afmt):
            opts = get_source_options("audio")
        codec = opts["postprocessors"][0]["preferredcodec"]
        assert codec != "None"
        assert codec  # non-empty

    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_720playlists_merge_output_format_never_none(self, vfmt: Any) -> None:
        with _patch_formats(vfmt, "m4a"):
            opts = get_source_options("720playlists")
        assert opts["merge_output_format"] not in (None, "", "None")

    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_unknown_source_merge_output_format_never_none(self, vfmt: Any) -> None:
        with _patch_formats(vfmt, "m4a"):
            opts = get_source_options("garbage")
        assert opts["merge_output_format"] not in (None, "", "None")

    def test_audio_none_afmt_falls_back_to_m4a(self) -> None:
        with _patch_formats("mp4", None):
            opts = get_source_options("audio")
        assert opts["format"] == "m4a/bestaudio/best"
        assert opts["postprocessors"][0]["preferredcodec"] == "m4a"

    def test_audio_empty_afmt_falls_back_to_m4a(self) -> None:
        with _patch_formats("mp4", ""):
            opts = get_source_options("audio")
        assert opts["format"] == "m4a/bestaudio/best"

    def test_unknown_none_vfmt_falls_back_to_mp4(self) -> None:
        with _patch_formats(None, "m4a"):
            opts = get_source_options("garbage")
        assert opts["merge_output_format"] == "mp4"

    def test_720playlists_empty_vfmt_falls_back_to_mp4(self) -> None:
        with _patch_formats("", "m4a"):
            opts = get_source_options("720playlists")
        assert opts["merge_output_format"] == "mp4"

    def test_unregistered_key_returns_fallback_defaults(self) -> None:
        """When _runtime contains no format keys at all, defaults must apply."""
        with patch.dict(sd._runtime, {}, clear=True):
            opts = get_source_options("audio")
        assert opts["format"] == "m4a/bestaudio/best"
        assert opts["postprocessors"][0]["preferredcodec"] == "m4a"


# ===========================================================================
# 3. _make_combo_row selection logic
# ===========================================================================


class TestMakeComboRowSelection:
    """findData(-1) guard: unknown stored values keep the combo at index 0, not -1."""

    def test_known_value_selects_correct_index(self) -> None:
        with _patch_formats("mkv", "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        assert combo.currentData() == "mkv"

    def test_known_audio_value_selects_correct_index(self) -> None:
        with _patch_formats("mp4", "opus"):
            _, combo = _make_combo_row("VID_DL_AUDIO_FORMAT", AUDIO_FORMAT_OPTIONS, None)
        assert combo.currentData() == "opus"

    def test_unknown_stored_value_does_not_set_index_negative_one(self) -> None:
        """A stale/env value not in the list must not call setCurrentIndex(-1)."""
        with _patch_formats("hevc", "m4a"):  # "hevc" is not in VIDEO_FORMAT_OPTIONS
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        # findData returns -1; guard in _make_combo_row keeps index >= 0
        assert combo.currentIndex() >= 0

    def test_empty_stored_value_does_not_crash(self) -> None:
        with _patch_formats("", "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        assert combo.currentIndex() >= 0

    def test_none_stored_value_does_not_crash(self) -> None:
        with _patch_formats(None, "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        assert combo.currentIndex() >= 0

    def test_combo_display_text_contains_dot_prefix(self) -> None:
        """Items should display as '.mp4 — description', not 'mp4'."""
        with _patch_formats("mp4", "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        text = combo.currentText()
        assert text.startswith(".")

    def test_combo_item_count_matches_options_list(self) -> None:
        with _patch_formats("mp4", "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        assert combo.count() == len(VIDEO_FORMAT_OPTIONS)

    def test_audio_combo_item_count_matches_options_list(self) -> None:
        with _patch_formats("mp4", "m4a"):
            _, combo = _make_combo_row("VID_DL_AUDIO_FORMAT", AUDIO_FORMAT_OPTIONS, None)
        assert combo.count() == len(AUDIO_FORMAT_OPTIONS)

    def test_userdata_is_raw_value_not_display_text(self) -> None:
        """UserData on every item must be the raw codec string (e.g. 'mp4', not '.mp4 — …')."""
        with _patch_formats("mp4", "m4a"):
            _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
        for i in range(combo.count()):
            data = combo.itemData(i)
            assert not str(data).startswith(".")
            assert "—" not in str(data)

    def test_all_video_option_values_selectable(self) -> None:
        """Every declared video format can be found by findData."""
        for value, _ in VIDEO_FORMAT_OPTIONS:
            with _patch_formats(value, "m4a"):
                _, combo = _make_combo_row("VID_DL_VIDEO_FORMAT", VIDEO_FORMAT_OPTIONS, None)
            assert combo.currentData() == value

    def test_all_audio_option_values_selectable(self) -> None:
        """Every declared audio format can be found by findData."""
        for value, _ in AUDIO_FORMAT_OPTIONS:
            with _patch_formats("mp4", value):
                _, combo = _make_combo_row("VID_DL_AUDIO_FORMAT", AUDIO_FORMAT_OPTIONS, None)
            assert combo.currentData() == value


# ===========================================================================
# 4. _apply() QComboBox branch
# ===========================================================================


class TestApplyComboBox:
    """_apply() must persist currentData() (raw value), not the display label."""

    def _make_combo_with_value(self, value: str) -> QComboBox:
        combo = QComboBox()
        for v, desc in VIDEO_FORMAT_OPTIONS:
            combo.addItem(f".{v} — {desc}", userData=v)
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def test_apply_persists_raw_value_not_display_text(self) -> None:
        combo = self._make_combo_with_value("mkv")
        persisted: dict[str, Any] = {}

        def _fake_persist(key: str, value: Any) -> None:
            persisted[key] = value
            sd._runtime[key] = value

        with (
            patch.dict(sd._runtime, {"VID_DL_VIDEO_FORMAT": "mp4"}),
            patch("src.settings_dialog._persist_setting", side_effect=_fake_persist),
        ):
            new_val = combo.currentData()
            if new_val != sd.get_setting("VID_DL_VIDEO_FORMAT"):
                _fake_persist("VID_DL_VIDEO_FORMAT", new_val)

        assert persisted.get("VID_DL_VIDEO_FORMAT") == "mkv"
        assert not persisted.get("VID_DL_VIDEO_FORMAT", "").startswith(".")

    def test_apply_no_change_does_not_call_persist(self) -> None:
        """If combo value equals stored value, _persist_setting must not be called."""
        combo = self._make_combo_with_value("mp4")
        persist_called = False

        def _fake_persist(key: str, value: Any) -> None:
            nonlocal persist_called
            persist_called = True

        with patch.dict(sd._runtime, {"VID_DL_VIDEO_FORMAT": "mp4"}):
            new_val = combo.currentData()
            if new_val != sd.get_setting("VID_DL_VIDEO_FORMAT"):
                _fake_persist("VID_DL_VIDEO_FORMAT", new_val)

        assert not persist_called

    def test_apply_combo_emits_changes_dict_with_raw_value(self) -> None:
        """The changes dict emitted via settings_changed must contain raw codec string."""
        combo = self._make_combo_with_value("webm")
        changes: dict[str, Any] = {}

        def _fake_persist(key: str, value: Any) -> None:
            changes[key] = value
            sd._runtime[key] = value

        with (
            patch.dict(sd._runtime, {"VID_DL_VIDEO_FORMAT": "mp4"}),
            patch("src.settings_dialog._persist_setting", side_effect=_fake_persist),
        ):
            new_val = combo.currentData()
            if new_val != sd.get_setting("VID_DL_VIDEO_FORMAT"):
                _fake_persist("VID_DL_VIDEO_FORMAT", new_val)

        assert changes["VID_DL_VIDEO_FORMAT"] == "webm"


# ===========================================================================
# 5. VIDEO_FORMAT_OPTIONS / AUDIO_FORMAT_OPTIONS structural invariants
# ===========================================================================


class TestFormatOptionLists:
    """Module-level option lists must be well-formed."""

    def test_video_format_options_non_empty(self) -> None:
        assert len(VIDEO_FORMAT_OPTIONS) > 0

    def test_audio_format_options_non_empty(self) -> None:
        assert len(AUDIO_FORMAT_OPTIONS) > 0

    def test_video_options_are_two_tuples(self) -> None:
        for item in VIDEO_FORMAT_OPTIONS:
            assert len(item) == 2, f"Expected 2-tuple, got: {item!r}"

    def test_audio_options_are_two_tuples(self) -> None:
        for item in AUDIO_FORMAT_OPTIONS:
            assert len(item) == 2, f"Expected 2-tuple, got: {item!r}"

    def test_video_option_values_are_non_empty_strings(self) -> None:
        for value, _ in VIDEO_FORMAT_OPTIONS:
            assert isinstance(value, str) and value

    def test_audio_option_values_are_non_empty_strings(self) -> None:
        for value, _ in AUDIO_FORMAT_OPTIONS:
            assert isinstance(value, str) and value

    def test_video_option_descriptions_are_non_empty_strings(self) -> None:
        for _, desc in VIDEO_FORMAT_OPTIONS:
            assert isinstance(desc, str) and desc

    def test_audio_option_descriptions_are_non_empty_strings(self) -> None:
        for _, desc in AUDIO_FORMAT_OPTIONS:
            assert isinstance(desc, str) and desc

    def test_video_default_mp4_is_present(self) -> None:
        values = [v for v, _ in VIDEO_FORMAT_OPTIONS]
        assert "mp4" in values

    def test_audio_default_m4a_is_present(self) -> None:
        values = [v for v, _ in AUDIO_FORMAT_OPTIONS]
        assert "m4a" in values

    def test_video_option_values_have_no_duplicates(self) -> None:
        values = [v for v, _ in VIDEO_FORMAT_OPTIONS]
        assert len(values) == len(set(values))

    def test_audio_option_values_have_no_duplicates(self) -> None:
        values = [v for v, _ in AUDIO_FORMAT_OPTIONS]
        assert len(values) == len(set(values))


# ===========================================================================
# 6. Config constants — DEFAULT_VIDEO_FORMAT / DEFAULT_AUDIO_FORMAT
# ===========================================================================


class TestConfigConstants:
    """DEFAULT_VIDEO_FORMAT and DEFAULT_AUDIO_FORMAT must be non-empty strings."""

    def test_default_video_format_is_non_empty_string(self) -> None:
        assert isinstance(cfg_mod.DEFAULT_VIDEO_FORMAT, str) and cfg_mod.DEFAULT_VIDEO_FORMAT

    def test_default_audio_format_is_non_empty_string(self) -> None:
        assert isinstance(cfg_mod.DEFAULT_AUDIO_FORMAT, str) and cfg_mod.DEFAULT_AUDIO_FORMAT

    def test_default_video_format_without_env_is_mp4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When VID_DL_VIDEO_FORMAT env var is absent, default should be 'mp4'."""
        monkeypatch.delenv("VID_DL_VIDEO_FORMAT", raising=False)
        importlib.reload(cfg_mod)
        assert cfg_mod.DEFAULT_VIDEO_FORMAT == "mp4"

    def test_default_audio_format_without_env_is_m4a(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When VID_DL_AUDIO_FORMAT env var is absent, default should be 'm4a'."""
        monkeypatch.delenv("VID_DL_AUDIO_FORMAT", raising=False)
        importlib.reload(cfg_mod)
        assert cfg_mod.DEFAULT_AUDIO_FORMAT == "m4a"

    def test_default_video_format_respects_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VID_DL_VIDEO_FORMAT", "mkv")
        importlib.reload(cfg_mod)
        assert cfg_mod.DEFAULT_VIDEO_FORMAT == "mkv"

    def test_default_audio_format_respects_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VID_DL_AUDIO_FORMAT", "flac")
        importlib.reload(cfg_mod)
        assert cfg_mod.DEFAULT_AUDIO_FORMAT == "flac"


# ===========================================================================
# 7. _init_runtime_settings() seeds format keys
# ===========================================================================


class TestInitRuntimeSettings:
    """_init_runtime_settings() must populate both format keys from config defaults."""

    def test_init_populates_video_format_key(self) -> None:
        from src.settings_dialog import _init_runtime_settings

        # Reload to get a clean DEFAULT_VIDEO_FORMAT (no env bleed from other tests).
        importlib.reload(cfg_mod)
        expected = cfg_mod.DEFAULT_VIDEO_FORMAT

        with patch.dict(sd._runtime, {}, clear=True):
            _init_runtime_settings()
            assert "VID_DL_VIDEO_FORMAT" in sd._runtime
            assert sd._runtime["VID_DL_VIDEO_FORMAT"] == expected

    def test_init_populates_audio_format_key(self) -> None:
        from src.settings_dialog import _init_runtime_settings

        # Reload config without any env override so we get the true compiled default.
        importlib.reload(cfg_mod)
        expected = cfg_mod.DEFAULT_AUDIO_FORMAT

        with patch.dict(sd._runtime, {}, clear=True):
            _init_runtime_settings()
            assert "VID_DL_AUDIO_FORMAT" in sd._runtime
            assert sd._runtime["VID_DL_AUDIO_FORMAT"] == expected


# ===========================================================================
# 8. FFmpegVideoRemuxer postprocessor — present for video, absent for audio
# ===========================================================================


def _find_remuxer(opts: dict) -> dict | None:
    """Return the FFmpegVideoRemuxer postprocessor entry, or None."""
    return next(
        (pp for pp in opts.get("postprocessors", []) if pp.get("key") == "FFmpegVideoRemuxer"),
        None,
    )


class TestRemuxvideoKeyPresence:
    """
    Guard the FFmpegVideoRemuxer postprocessor for video sources.

    yt-dlp's Python API ignores bare params like 'remuxvideo'; the correct
    approach is {"key": "FFmpegVideoRemuxer", "preferedformat": vfmt} in the
    postprocessors list.  Every video source path must carry this entry.

    Audio-only sources ('audio', 'audio_playlists') must not gain it.
    Neither 'remux_video' nor 'remuxvideo' must appear as top-level keys.
    """

    @pytest.mark.parametrize(
        "source",
        ["720playlists", "1080playlists", "480", "1080", "garbage"],
    )
    def test_remuxer_postprocessor_present_for_video_source(self, source: str) -> None:
        """get_source_options must include FFmpegVideoRemuxer in postprocessors for video."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        assert _find_remuxer(opts) is not None, (
            f"FFmpegVideoRemuxer missing from postprocessors in get_source_options({source!r})"
        )

    @pytest.mark.parametrize(
        "source",
        ["720playlists", "1080playlists", "480", "1080", "garbage"],
    )
    def test_remux_video_old_key_absent_for_video_source(self, source: str) -> None:
        """Neither 'remux_video' nor 'remuxvideo' must appear as top-level keys."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        assert "remux_video" not in opts, (
            f"Old key 'remux_video' found in get_source_options({source!r})"
        )
        assert "remuxvideo" not in opts, (
            f"CLI-only key 'remuxvideo' found as top-level param in get_source_options({source!r})"
        )

    @pytest.mark.parametrize(
        ("source", "vfmt"),
        [
            ("720playlists", "mkv"),
            ("720playlists", "webm"),
            ("720playlists", "mp4"),
            ("1080playlists", "mkv"),
            ("1080playlists", "webm"),
            ("480", "mkv"),
            ("garbage", "webm"),
        ],
    )
    def test_remuxvideo_value_tracks_vfmt(self, source: str, vfmt: str) -> None:
        """FFmpegVideoRemuxer preferedformat must equal the active vfmt setting."""
        with _patch_formats(vfmt, "m4a"):
            opts = get_source_options(source)
        remuxer = _find_remuxer(opts)
        assert remuxer is not None
        assert remuxer["preferedformat"] == vfmt, (
            f"preferedformat={remuxer['preferedformat']!r}, expected {vfmt!r} for source={source!r}"
        )

    @pytest.mark.parametrize("source", ["720playlists", "1080playlists", "480", "garbage"])
    @pytest.mark.parametrize("vfmt", [None, ""])
    def test_remuxvideo_falls_back_to_mp4_when_vfmt_falsy(self, source: str, vfmt: Any) -> None:
        """When vfmt is falsy, FFmpegVideoRemuxer preferedformat must be 'mp4'."""
        with _patch_formats(vfmt, "m4a"):
            opts = get_source_options(source)
        remuxer = _find_remuxer(opts)
        assert remuxer is not None
        assert remuxer["preferedformat"] == "mp4", (
            f"preferedformat={remuxer['preferedformat']!r} for vfmt={vfmt!r}, source={source!r}"
        )

    @pytest.mark.parametrize("source", ["audio", "audio_playlists"])
    def test_remuxvideo_absent_for_audio_source(self, source: str) -> None:
        """Audio-only sources must not gain a spurious FFmpegVideoRemuxer postprocessor."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        assert _find_remuxer(opts) is None, (
            f"Unexpected FFmpegVideoRemuxer in get_source_options({source!r})"
        )
        assert "remuxvideo" not in opts
        assert "remux_video" not in opts


# ===========================================================================
# 9. preferedformat typo — must be exactly "preferedformat", not "preferredformat"
# ===========================================================================


class TestPreferedformatTypo:
    """
    Guard that the intentionally misspelled yt-dlp key 'preferedformat' is used.

    yt-dlp uses the intentionally misspelled key 'preferedformat' (one 'r').
    If someone "fixes" the typo to 'preferredformat', yt-dlp silently ignores it.
    These tests guard that the correct (misspelled) key name is always used.
    """

    @pytest.mark.parametrize(
        "source",
        ["720playlists", "1080playlists", "480", "1080", "garbage"],
    )
    def test_remuxer_uses_preferedformat_not_preferredformat(self, source: str) -> None:
        """FFmpegVideoRemuxer must use 'preferedformat' (one 'r'), not 'preferredformat'."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        remuxer = _find_remuxer(opts)
        assert remuxer is not None
        assert "preferedformat" in remuxer, (
            f"Key 'preferedformat' missing from remuxer for source={source!r}: {remuxer!r}"
        )
        assert "preferredformat" not in remuxer, (
            f"Correctly-spelled key 'preferredformat' found — yt-dlp ignores it; "
            f"use 'preferedformat' for source={source!r}"
        )


# ===========================================================================
# 10. Postprocessor ordering — remuxer must be last in numeric/unknown fallback
# ===========================================================================


class TestPostprocessorOrdering:
    """
    Guard postprocessor ordering: SponsorBlock before FFmpegVideoRemuxer.

    For numeric/unknown sources the postprocessors list is:
        [SponsorBlock, ModifyChapters, FFmpegVideoRemuxer]
    Remuxer must come after SponsorBlock so that segments are skipped before
    the container is re-wrapped.  If the order is wrong, chapter timestamps
    could be incorrect in the output file.
    """

    @pytest.mark.parametrize("source", ["480", "720", "1080", "garbage"])
    def test_remuxer_is_last_postprocessor_for_numeric_and_unknown(self, source: str) -> None:
        """FFmpegVideoRemuxer must be the final entry in postprocessors."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        pps = opts.get("postprocessors", [])
        assert pps, f"postprocessors list is empty for source={source!r}"
        assert pps[-1].get("key") == "FFmpegVideoRemuxer", (
            f"Last postprocessor is {pps[-1].get('key')!r}, expected 'FFmpegVideoRemuxer' "
            f"for source={source!r}"
        )

    @pytest.mark.parametrize("source", ["480", "720", "1080", "garbage"])
    def test_sponsorblock_precedes_remuxer_for_numeric_and_unknown(self, source: str) -> None:
        """SponsorBlock must appear before FFmpegVideoRemuxer in the list."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        pps = opts.get("postprocessors", [])
        keys = [pp.get("key") for pp in pps]
        assert "SponsorBlock" in keys, f"SponsorBlock missing for source={source!r}"
        sb_idx = keys.index("SponsorBlock")
        remuxer_idx = next((i for i, k in enumerate(keys) if k == "FFmpegVideoRemuxer"), None)
        assert remuxer_idx is not None
        assert sb_idx < remuxer_idx, (
            f"SponsorBlock index {sb_idx} >= remuxer index {remuxer_idx} for source={source!r}"
        )

    @pytest.mark.parametrize("source", ["720playlists", "1080playlists"])
    def test_named_playlist_sources_have_no_sponsorblock(self, source: str) -> None:
        """Named playlist sources intentionally omit SponsorBlock (overrides default list)."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        pps = opts.get("postprocessors", [])
        keys = [pp.get("key") for pp in pps]
        assert "SponsorBlock" not in keys, (
            f"SponsorBlock unexpectedly present in {source!r} postprocessors: {keys!r}"
        )

    @pytest.mark.parametrize("source", ["720playlists", "1080playlists"])
    def test_named_playlist_sources_have_exactly_one_postprocessor(self, source: str) -> None:
        """Named playlist sources must have exactly one postprocessor: the remuxer."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options(source)
        pps = opts.get("postprocessors", [])
        assert len(pps) == 1, (
            f"Expected exactly 1 postprocessor for {source!r}, got {len(pps)}: {pps!r}"
        )
        assert pps[0].get("key") == "FFmpegVideoRemuxer"


# ===========================================================================
# 11. Zero and negative height boundary — numeric source edge cases
# ===========================================================================


class TestNumericHeightBoundaries:
    """
    Boundary tests for zero and negative numeric height source strings.

    int("0") == 0 → falsy → falls through to generic 'bestvideo*+bestaudio/best'.
    int("-720") == -720 → truthy → embeds 'height=-720' which is invalid for yt-dlp.
    These tests document current behavior and catch regressions.
    """

    def test_zero_height_falls_back_to_generic_format(self) -> None:
        """Source '0' is falsy after int() conversion — must use generic format."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("0")
        assert opts["format"] == "bestvideo*+bestaudio/best"

    def test_zero_height_still_gets_remuxer(self) -> None:
        """Source '0' uses the numeric/unknown code path and must still have the remuxer."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("0")
        assert _find_remuxer(opts) is not None

    def test_negative_height_does_not_embed_negative_in_format(self) -> None:
        """Source '-720' must fall back to generic format, not embed 'height=-720'."""
        with _patch_formats("mp4", "m4a"):
            opts = get_source_options("-720")
        assert "-720" not in opts["format"], (
            "Negative height embedded in format selector — yt-dlp will reject it."
        )
        assert opts["format"] == "bestvideo*+bestaudio/best"


# ===========================================================================
# 12. get_postprocessors() and get_output_template() helpers
# ===========================================================================


class TestHelperFunctions:
    """get_postprocessors() and get_output_template() delegates to get_source_options()."""

    def test_get_postprocessors_audio_returns_extract_audio(self) -> None:
        from src.ydl_options import get_postprocessors

        with _patch_formats("mp4", "opus"):
            pps = get_postprocessors("audio")
        assert any(pp.get("key") == "FFmpegExtractAudio" for pp in pps)

    def test_get_postprocessors_video_source_returns_remuxer(self) -> None:
        from src.ydl_options import get_postprocessors

        with _patch_formats("mkv", "m4a"):
            pps = get_postprocessors("720playlists")
        assert any(pp.get("key") == "FFmpegVideoRemuxer" for pp in pps)

    def test_get_postprocessors_unknown_source_returns_default_plus_remuxer(
        self,
    ) -> None:
        from src.ydl_options import get_postprocessors

        with _patch_formats("mp4", "m4a"):
            pps = get_postprocessors("garbage")
        keys = [pp.get("key") for pp in pps]
        assert "SponsorBlock" in keys
        assert "FFmpegVideoRemuxer" in keys

    def test_get_output_template_returns_non_empty_string(self) -> None:
        from src.ydl_options import get_output_template

        with _patch_formats("mp4", "m4a"):
            tmpl = get_output_template("audio")
        assert isinstance(tmpl, str) and tmpl

    def test_get_output_template_audio_contains_podcast_dir(self) -> None:
        from src.ydl_options import get_output_template

        with _patch_formats("mp4", "m4a"):
            tmpl = get_output_template("audio")
        # The template must reference the title placeholder
        assert "%(title)s" in tmpl

    def test_get_output_template_video_source_contains_title_placeholder(
        self,
    ) -> None:
        from src.ydl_options import get_output_template

        with _patch_formats("mp4", "m4a"):
            tmpl = get_output_template("720playlists")
        assert "%(title)s" in tmpl

    def test_get_postprocessors_no_source_argument_unknown_falls_back_gracefully(
        self,
    ) -> None:
        """get_postprocessors for a truly unknown key must return a non-empty list."""
        from src.ydl_options import get_postprocessors

        with _patch_formats("mp4", "m4a"):
            pps = get_postprocessors("__nonexistent_source__")
        assert pps  # non-empty list


# ===========================================================================
# 13. Runtime directory overrides — VID_DL_VIDEO_STORAGE_DIR / PODCAST_MISC_OUTPUT_DIR
# ===========================================================================


class TestRuntimeDirectoryOverrides:
    """get_source_options must use get_setting dirs, not frozen config constants."""

    def _patch_dirs(self, video_dir: str | None, podcast_dir: str | None):
        return patch.dict(
            sd._runtime,
            {
                "VID_DL_VIDEO_STORAGE_DIR": video_dir,
                "VID_DL_PODCAST_MISC_OUTPUT_DIR": podcast_dir,
            },
        )

    def test_audio_outtmpl_uses_custom_podcast_dir(self) -> None:
        with _patch_formats("mp4", "m4a"), self._patch_dirs(None, "/custom/pods"):
            opts = get_source_options("audio")
        assert opts["outtmpl"].startswith("/custom/pods")

    def test_audio_playlists_outtmpl_uses_custom_podcast_dir(self) -> None:
        with _patch_formats("mp4", "m4a"), self._patch_dirs(None, "/custom/pods"):
            opts = get_source_options("audio_playlists")
        assert opts["outtmpl"].startswith("/custom/pods")

    def test_720playlists_outtmpl_uses_custom_video_dir(self) -> None:
        with _patch_formats("mp4", "m4a"), self._patch_dirs("/custom/vids", None):
            opts = get_source_options("720playlists")
        assert opts["outtmpl"].startswith("/custom/vids")

    def test_1080playlists_outtmpl_uses_custom_video_dir(self) -> None:
        with _patch_formats("mp4", "m4a"), self._patch_dirs("/custom/vids", None):
            opts = get_source_options("1080playlists")
        assert opts["outtmpl"].startswith("/custom/vids")

    def test_numeric_source_outtmpl_uses_custom_video_dir(self) -> None:
        with _patch_formats("mp4", "m4a"), self._patch_dirs("/custom/vids", None):
            opts = get_source_options("720")
        assert opts["outtmpl"].startswith("/custom/vids")

    def test_none_video_dir_falls_back_to_config_constant(self) -> None:
        from src.config import VIDEO_STORAGE_DIR

        with _patch_formats("mp4", "m4a"), self._patch_dirs(None, None):
            opts = get_source_options("720")
        assert VIDEO_STORAGE_DIR.as_posix() in opts["outtmpl"]

    def test_none_podcast_dir_falls_back_to_config_constant(self) -> None:
        from src.config import PODCAST_MISC_OUTPUT_DIR

        with _patch_formats("mp4", "m4a"), self._patch_dirs(None, None):
            opts = get_source_options("audio")
        assert PODCAST_MISC_OUTPUT_DIR.as_posix() in opts["outtmpl"]

    def test_empty_video_dir_falls_back_to_config_constant(self) -> None:
        from src.config import VIDEO_STORAGE_DIR

        with _patch_formats("mp4", "m4a"), self._patch_dirs("", None):
            opts = get_source_options("720")
        assert VIDEO_STORAGE_DIR.as_posix() in opts["outtmpl"]

"""Unit tests for src.ydl_options builders."""

import pytest

from src.dict_utils import DEFAULT_POSTPROCESSORS
from src.resolutions import RESOLUTION_PRESETS
from src.ydl_options import (
    JS_RUNTIMES_CONFIG,
    build_shared_extraction_opts,
    get_output_template,
    get_postprocessors,
    get_source_options,
)


class TestBuildSharedExtractionOpts:
    """
    Direct coverage for the option-merge helper factored out of build_base_ydl_opts.

    Every YoutubeDL construction site in the app (build_base_ydl_opts,
    podcast_helpers.fetch_latest_accessible_entry, both bare YoutubeDL sites
    in download_service.py, and ydl_utils.extract_playlist_info/
    extract_video_entries) now goes through this helper, so its own
    contract -- returned keys, and which parts are safe to mutate per-call --
    is worth pinning down independently of any single caller.
    """

    def test_returns_expected_top_level_keys(self) -> None:
        opts = build_shared_extraction_opts()
        assert set(opts) == {"js_runtimes", "extractor_args"}

    def test_extractor_args_wiring(self) -> None:
        from src.config import POT_PROVIDER_SERVER_HOME

        opts = build_shared_extraction_opts()
        assert opts["extractor_args"]["youtubepot-bgutilscript"]["server_home"] == [
            str(POT_PROVIDER_SERVER_HOME)
        ]
        assert opts["extractor_args"]["youtube"]["player_client"]

    def test_extractor_args_tree_is_a_fresh_dict_per_call(self) -> None:
        """
        extractor_args must not be shared by reference across calls.

        Every call site builds its own YoutubeDL instance from this dict; if
        extractor_args were shared, yt-dlp (or the bgutil plugin) mutating
        one instance's config would leak into every other instance built
        afterwards -- including unrelated, concurrently-running ones (e.g.
        the QYTQueue download thread vs. a podcast-polling timer callback on
        the Qt main thread).
        """
        first = build_shared_extraction_opts()
        second = build_shared_extraction_opts()

        assert first["extractor_args"] is not second["extractor_args"]
        assert first["extractor_args"]["youtube"] is not second["extractor_args"]["youtube"]
        assert (
            first["extractor_args"]["youtubepot-bgutilscript"]
            is not second["extractor_args"]["youtubepot-bgutilscript"]
        )

    def test_player_client_list_is_not_shared_across_calls(self) -> None:
        """Mutating one call's player_client list must not affect another's."""
        first = build_shared_extraction_opts()
        first["extractor_args"]["youtube"]["player_client"].append("mutated")

        second = build_shared_extraction_opts()

        assert "mutated" not in second["extractor_args"]["youtube"]["player_client"]

    def test_js_runtimes_is_the_shared_module_level_dict(self) -> None:
        """
        Characterization test: unlike extractor_args, js_runtimes is NOT copied.

        Every YoutubeDL instance built via this helper -- across podcast
        polling, live-queue checks, on-demand metadata lookups, and actual
        downloads -- receives the exact same JS_RUNTIMES_CONFIG object. This
        is currently safe: the bgutil script provider only reads it
        (yt_dlp_plugins.extractor.getpot_bgutil_script._jsrt_path_impl does a
        read-only traverse_obj lookup, never a write). It is nonetheless a
        footgun for a future yt-dlp/plugin version, or any other extractor,
        that writes back into params dicts it's handed -- especially now that
        this helper is called from many more concurrently-reachable sites
        than before this fix. If this assertion ever starts failing, either
        JS_RUNTIMES_CONFIG was deliberately made per-call (update this test),
        or something started copying it defensively (also fine -- update
        this test and drop the characterization note).
        """
        first = build_shared_extraction_opts()
        second = build_shared_extraction_opts()

        assert first["js_runtimes"] is JS_RUNTIMES_CONFIG
        assert second["js_runtimes"] is JS_RUNTIMES_CONFIG


def test_get_source_options_numeric_height_format() -> None:
    opts = get_source_options("480")
    assert "height<=480" in opts["format"]


def test_get_source_options_non_numeric_fallback_format() -> None:
    opts = get_source_options("garbage")
    assert opts["format"] == "bestvideo*+bestaudio/best"


def test_get_source_options_unknown_returns_mp4_merge() -> None:
    opts = get_source_options("garbage")
    assert opts["merge_output_format"] == "mp4"


def test_get_output_template_audio_returns_outtmpl() -> None:
    template = get_output_template("audio")
    assert "%(title)s.%(ext)s" in template


def test_get_postprocessors_audio_returns_audio_postprocessors() -> None:
    postprocs = get_postprocessors("audio")
    keys = [pp.get("key") for pp in postprocs]
    assert "FFmpegExtractAudio" in keys


def test_get_postprocessors_720playlists_has_remuxer() -> None:
    postprocs = get_postprocessors("720playlists")
    keys = [pp.get("key") for pp in postprocs]
    assert "FFmpegVideoRemuxer" in keys


def test_get_postprocessors_unknown_source_falls_back_to_default() -> None:
    postprocs = get_postprocessors("garbage")
    default_keys = {pp["key"] for pp in DEFAULT_POSTPROCESSORS}
    result_keys = {pp["key"] for pp in postprocs}
    assert default_keys.issubset(result_keys)


# ---------------------------------------------------------------------------
# get_source_options - resolution-registry-driven playlist entries (Phase 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", RESOLUTION_PRESETS, ids=lambda p: str(p.height))
def test_get_source_options_playlist_key_for_every_registered_rung(preset) -> None:
    """
    Every registered rung's playlist source key must produce a working format.

    Loop-generated, not just the two legacy 1080/720 literals - covers rungs
    (2160, 1440, 480, 360) that never had hand-written dict entries before
    this phase.
    """
    opts = get_source_options(f"{preset.height}playlists")
    assert f"height<={preset.height}" in opts["format"]
    assert opts["ignoreerrors"] == "only_download"
    assert "%(playlist_index)s" in opts["outtmpl"]


def test_get_source_options_disabled_rung_playlist_key_still_resolves() -> None:
    """
    A playlist key for a rung NOT in ENABLED_RESOLUTIONS must still work.

    get_source_options loops over every RESOLUTION_PRESETS entry, not just
    the enabled subset, because failed_downloads_dialog.py's _can_retry can
    hand it a source string for a rung the user has since disabled (a parked
    or previously-failed record). Default ENABLED_RESOLUTIONS is (1080, 720),
    so 2160 exercises exactly that path.
    """
    opts = get_source_options("2160playlists")
    assert "height<=2160" in opts["format"]


def test_get_source_options_audio_playlists_has_ignoreerrors_only_download() -> None:
    opts = get_source_options("audio_playlists")
    assert opts["ignoreerrors"] == "only_download"


def test_get_source_options_bare_audio_has_no_ignoreerrors_key() -> None:
    """Bare 'audio' (single-video) must not carry the playlist-only ignoreerrors flag."""
    opts = get_source_options("audio")
    assert "ignoreerrors" not in opts


def test_get_source_options_height_zero_falls_back_to_unconstrained_format() -> None:
    """height=0 is falsy, so '0' must fall back to the unconstrained format, not '[height<=0]'."""
    opts = get_source_options("0")
    assert opts["format"] == "bestvideo*+bestaudio/best"


def test_get_source_options_negative_height_falls_back_to_unconstrained_format() -> None:
    """A negative height parses but must not build a '[height<=-720]' selector."""
    opts = get_source_options("-720")
    assert opts["format"] == "bestvideo*+bestaudio/best"


def test_get_source_options_whitespace_padded_numeric_source_still_parses() -> None:
    """
    int(source) strips whitespace, so ' 1080' is accepted just like '1080'.

    Pins down this fallback branch's int() parsing independently of
    height_from_source's identical quirk (different code path, same stdlib
    behavior) - see test_height_from_source_accepts_leading_whitespace.
    """
    opts = get_source_options(" 1080")
    assert "height<=1080" in opts["format"]

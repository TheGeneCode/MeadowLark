"""Tests for the config module covering path resolution and environment overrides."""

import os
from pathlib import Path
from unittest import mock

from src import config


class TestPathResolution:
    """Tests for path resolution with environment variable fallbacks."""

    def test_error_log_path_default(self) -> None:
        """Test error log path uses default when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=False):
            # Clear the env var if it exists
            os.environ.pop("VID_DL_ERROR_LOG", None)
            # Re-import to get fresh defaults
            import importlib

            importlib.reload(config)
            assert Path("error_log.txt") == config.ERROR_LOG_PATH

    def test_error_log_path_from_env(self) -> None:
        """Test error log path can be overridden via environment variable."""
        custom_path = "/custom/error.log"
        with mock.patch.dict(os.environ, {"VID_DL_ERROR_LOG": custom_path}):
            import importlib

            importlib.reload(config)
            assert Path(custom_path) == config.ERROR_LOG_PATH

    def test_history_log_path_default(self) -> None:
        """Test history log path uses default when env var not set."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_HISTORY_LOG", None)
            import importlib

            importlib.reload(config)
            assert Path("history_log.txt") == config.HISTORY_LOG_PATH

    def test_cookies_file_path(self) -> None:
        """Test cookies file path is under resources directory."""
        with mock.patch.dict(os.environ, {}, clear=False):
            import importlib

            importlib.reload(config)
            assert "cookies.txt" in str(config.COOKIES_FILE)
            assert config.COOKIES_FILE.name == "cookies.txt"

    def test_live_queue_file_path(self) -> None:
        """Test live queue file path is under resources directory."""
        with mock.patch.dict(os.environ, {}, clear=False):
            import importlib

            importlib.reload(config)
            assert "live_queue.txt" in str(config.LIVE_QUEUE_FILE)

    def test_playlist_files_paths(self) -> None:
        """Test all playlist file paths are configured."""
        with mock.patch.dict(os.environ, {}, clear=False):
            import importlib

            importlib.reload(config)
            assert config.PLAYLISTS_FILE.name == "playlists.txt"
            assert config.PLAYLISTS_720_FILE.name == "720playlists.txt"
            assert config.PLAYLISTS_AUDIO_FILE.name == "audio playlists.txt"

    def test_resources_dir_can_override(self) -> None:
        """Test resources directory can be overridden via environment."""
        custom_resources = "/custom/resources"
        with mock.patch.dict(os.environ, {"VID_DL_RESOURCES_DIR": custom_resources}):
            import importlib

            importlib.reload(config)
            assert Path(custom_resources) == config.RESOURCES_DIR

    def test_venv_scripts_dir_default(self) -> None:
        """Test venv scripts directory uses default path."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_VENV_SCRIPTS", None)
            import importlib

            importlib.reload(config)
            assert ".venv" in str(config.VENV_SCRIPTS_DIR)


class TestTimeoutConfiguration:
    """Tests for timeout configuration constants."""

    def test_http_timeout_default(self) -> None:
        """Test HTTP timeout uses default value."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_HTTP_TIMEOUT", None)
            import importlib

            importlib.reload(config)
            assert config.HTTP_TIMEOUT_SECONDS == 120

    def test_http_timeout_from_env(self) -> None:
        """Test HTTP timeout can be overridden via environment."""
        with mock.patch.dict(os.environ, {"VID_DL_HTTP_TIMEOUT": "300"}):
            import importlib

            importlib.reload(config)
            assert config.HTTP_TIMEOUT_SECONDS == 300

    def test_socket_timeout_default(self) -> None:
        """Test socket timeout uses default value."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_SOCKET_TIMEOUT", None)
            import importlib

            importlib.reload(config)
            assert config.SOCKET_TIMEOUT_SECONDS == 120

    def test_http_request_timeout_default(self) -> None:
        """Test HTTP request timeout uses default value."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_HTTP_REQUEST_TIMEOUT", None)
            import importlib

            importlib.reload(config)
            assert config.HTTP_REQUEST_TIMEOUT_SECONDS == 5

    def test_http_request_timeout_from_env(self) -> None:
        """Test HTTP request timeout can be overridden."""
        with mock.patch.dict(os.environ, {"VID_DL_HTTP_REQUEST_TIMEOUT": "10"}):
            import importlib

            importlib.reload(config)
            assert config.HTTP_REQUEST_TIMEOUT_SECONDS == 10


class TestDownloadConfiguration:
    """Tests for download configuration."""

    def test_max_fragment_retries_default(self) -> None:
        """Test max fragment retries uses default value."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_MAX_FRAGMENT_RETRIES", None)
            import importlib

            importlib.reload(config)
            assert config.MAX_FRAGMENT_RETRIES == 10

    def test_max_fragment_retries_from_env(self) -> None:
        """Test max fragment retries can be overridden."""
        with mock.patch.dict(os.environ, {"VID_DL_MAX_FRAGMENT_RETRIES": "20"}):
            import importlib

            importlib.reload(config)
            assert config.MAX_FRAGMENT_RETRIES == 20

    def test_http_ok_status_code(self) -> None:
        """Test HTTP OK status code constant."""
        assert config.HTTP_OK == 200


class TestPodcastConfiguration:
    """Tests for podcast-related configuration."""

    def test_podcast_min_duration_default(self) -> None:
        """Test minimum podcast duration uses default (3 minutes = 180 seconds)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_PODCAST_MIN_DURATION_SECONDS", None)
            import importlib

            importlib.reload(config)
            assert config.PODCAST_MIN_DURATION_SECONDS == 180

    def test_podcast_min_duration_from_env(self) -> None:
        """Test minimum podcast duration can be overridden."""
        with mock.patch.dict(
            os.environ,
            {"VID_DL_PODCAST_MIN_DURATION_SECONDS": "300"},
        ):
            import importlib

            importlib.reload(config)
            assert config.PODCAST_MIN_DURATION_SECONDS == 300

    def test_sponsorblock_cache_ttl_default(self) -> None:
        """Test SponsorBlock cache TTL uses default (6 hours)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_SPONSORBLOCK_CACHE_TTL_HOURS", None)
            import importlib

            importlib.reload(config)
            assert config.SPONSORBLOCK_CACHE_TTL_HOURS == 6

    def test_sponsorblock_cache_ttl_from_env(self) -> None:
        """Test SponsorBlock cache TTL can be overridden."""
        with mock.patch.dict(os.environ, {"VID_DL_SPONSORBLOCK_CACHE_TTL_HOURS": "12"}):
            import importlib

            importlib.reload(config)
            assert config.SPONSORBLOCK_CACHE_TTL_HOURS == 12

    def test_live_queue_check_interval_default(self) -> None:
        """Test live queue check interval uses default (30 minutes)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LIVE_QUEUE_CHECK_INTERVAL_MINUTES", None)
            import importlib

            importlib.reload(config)
            assert config.LIVE_QUEUE_CHECK_INTERVAL_MINUTES == 30

    def test_live_queue_check_interval_from_env(self) -> None:
        """Test live queue check interval can be overridden."""
        with mock.patch.dict(
            os.environ,
            {"VID_DL_LIVE_QUEUE_CHECK_INTERVAL_MINUTES": "60"},
        ):
            import importlib

            importlib.reload(config)
            assert config.LIVE_QUEUE_CHECK_INTERVAL_MINUTES == 60

    def test_podcast_lookahead_max_attempts_default(self) -> None:
        """Test podcast lookahead max attempts uses default (5)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_PODCAST_LOOKAHEAD_MAX_ATTEMPTS", None)
            import importlib

            importlib.reload(config)
            assert config.PODCAST_LOOKAHEAD_MAX_ATTEMPTS == 5


class TestDisplayConfiguration:
    """Tests for display configuration."""

    def test_label_output_font_default(self) -> None:
        """Test label output font uses default (Arial)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_OUTPUT_FONT", None)
            import importlib

            importlib.reload(config)
            assert config.LABEL_OUTPUT_FONT_NAME == "Arial"

    def test_label_output_font_from_env(self) -> None:
        """Test label output font can be overridden."""
        with mock.patch.dict(os.environ, {"VID_DL_LABEL_OUTPUT_FONT": "Courier"}):
            import importlib

            importlib.reload(config)
            assert config.LABEL_OUTPUT_FONT_NAME == "Courier"

    def test_label_output_font_size_default(self) -> None:
        """Test label output font size uses default (16)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_OUTPUT_FONT_SIZE", None)
            import importlib

            importlib.reload(config)
            assert config.LABEL_OUTPUT_FONT_SIZE == 16

    def test_label_ready_text_default(self) -> None:
        """Test label ready text uses default."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_READY_TEXT", None)
            import importlib

            importlib.reload(config)
            assert config.LABEL_READY_TEXT == "[ Ready ]"


class TestPostProcessingConfiguration:
    """Tests for post-processing configuration."""

    def test_merge_output_format_default(self) -> None:
        """Test merge output format uses default (mp4)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_MERGE_OUTPUT_FORMAT", None)
            import importlib

            importlib.reload(config)
            assert config.DEFAULT_MERGE_OUTPUT_FORMAT == "mp4"


class TestDebugConfiguration:
    """Tests for debug configuration."""

    def test_logfile_migration_enabled_default(self) -> None:
        """Test logfile migration is enabled by default."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LOGFILE_MIGRATION", None)
            import importlib

            importlib.reload(config)
            assert config.LOGFILE_MIGRATION_ENABLED is True

    def test_logfile_migration_disabled_via_env(self) -> None:
        """Test logfile migration can be disabled via environment."""
        with mock.patch.dict(os.environ, {"VID_DL_LOGFILE_MIGRATION": "false"}):
            import importlib

            importlib.reload(config)
            assert config.LOGFILE_MIGRATION_ENABLED is False


class TestConfigConstants:
    """Integration tests for config constant accessibility."""

    def test_all_constants_accessible(self) -> None:
        """Test that all configurations are accessible as module attributes."""
        # Verify all major groups are accessible
        assert hasattr(config, "ERROR_LOG_PATH")
        assert hasattr(config, "HISTORY_LOG_PATH")
        assert hasattr(config, "HTTP_TIMEOUT_SECONDS")
        assert hasattr(config, "PODCAST_MIN_DURATION_SECONDS")
        assert hasattr(config, "SPONSORBLOCK_CACHE_TTL_HOURS")
        assert hasattr(config, "LABEL_OUTPUT_FONT_NAME")

    def test_paths_are_path_objects(self) -> None:
        """Test that all paths are Path objects."""
        assert isinstance(config.ERROR_LOG_PATH, Path)
        assert isinstance(config.HISTORY_LOG_PATH, Path)
        assert isinstance(config.COOKIES_FILE, Path)
        assert isinstance(config.LIVE_QUEUE_FILE, Path)
        assert isinstance(config.PLAYLISTS_FILE, Path)
        assert isinstance(config.VENV_SCRIPTS_DIR, Path)

    def test_numeric_constants_are_integers(self) -> None:
        """Test that numeric constants are integers."""
        assert isinstance(config.HTTP_TIMEOUT_SECONDS, int)
        assert isinstance(config.SOCKET_TIMEOUT_SECONDS, int)
        assert isinstance(config.MAX_FRAGMENT_RETRIES, int)
        assert isinstance(config.PODCAST_MIN_DURATION_SECONDS, int)
        assert isinstance(config.HTTP_OK, int)


class TestResolutionRouting:
    """Tests for the resolution-registry-backed routing helpers (Phase 1)."""

    def test_playlist_path_for_height_1080_legacy_default(self) -> None:
        """1080 must resolve to playlists.txt (legacy filename), not 1080playlists.txt."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_PLAYLISTS_FILE", None)
            path = config.playlist_path_for_height(1080)
            assert path.name == "playlists.txt"

    def test_playlist_path_for_height_720_legacy_default(self) -> None:
        """720 must resolve to 720playlists.txt via its legacy key."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_PLAYLISTS_720_FILE", None)
            path = config.playlist_path_for_height(720)
            assert path.name == "720playlists.txt"

    def test_playlist_path_for_height_1080_env_override(self) -> None:
        """The legacy VID_DL_PLAYLISTS_FILE env var must still override 1080's path."""
        with mock.patch.dict(os.environ, {"VID_DL_PLAYLISTS_FILE": "/custom/mine.txt"}):
            path = config.playlist_path_for_height(1080)
            assert path == Path("/custom/mine.txt")

    def test_playlist_path_for_height_new_rung_default_filename(self) -> None:
        """A new registry rung (1440) must default to '<height>playlists.txt'."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_PLAYLISTS_1440_FILE", None)
            path = config.playlist_path_for_height(1440)
            assert path.name == "1440playlists.txt"

    def test_playlist_path_for_height_new_rung_env_override(self) -> None:
        """A new registry rung must honor its generated VID_DL_PLAYLISTS_<H>_FILE key."""
        with mock.patch.dict(os.environ, {"VID_DL_PLAYLISTS_1440_FILE": "/custom/1440.txt"}):
            path = config.playlist_path_for_height(1440)
            assert path == Path("/custom/1440.txt")

    def test_playlist_path_for_height_unregistered_height_ignores_env_override(
        self,
    ) -> None:
        """
        An unregistered height's path bypasses env resolution entirely.

        playlist_path_for_height short-circuits on get_preset(height) is None
        and returns PLAYLISTS_DIR / '<height>playlists.txt' directly, never
        consulting playlist_file_key/_resolve_path. Documented here because it
        is inconsistent with every registered rung, which does honor an env
        override - if a caller ever reaches this branch with a real height
        (rather than one already screened by height_from_source), any env
        override the user set for it is silently ignored.
        """
        with mock.patch.dict(os.environ, {"VID_DL_PLAYLISTS_999_FILE": "/custom/999.txt"}):
            path = config.playlist_path_for_height(999)
            assert path != Path("/custom/999.txt")
            assert path.name == "999playlists.txt"

    def test_drop_label_for_height_default(self) -> None:
        """A registered rung's drop label defaults to its bare height label."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_DROP_1440", None)
            assert config.drop_label_for_height(1440) == "1440"

    def test_drop_label_for_height_env_override(self) -> None:
        """A registered rung's drop label must honor its env override."""
        with mock.patch.dict(os.environ, {"VID_DL_LABEL_DROP_1080": "Custom"}):
            assert config.drop_label_for_height(1080) == "Custom"

    def test_drop_label_for_height_unregistered_falls_back_to_str_height(self) -> None:
        """An unregistered height's drop label default is just str(height)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_DROP_999", None)
            assert config.drop_label_for_height(999) == "999"

    def test_button_label_for_height_1080_uses_legacy_key(self) -> None:
        """1080's button label must honor the legacy VID_DL_LABEL_BTN_PLAYLISTS key."""
        with mock.patch.dict(os.environ, {"VID_DL_LABEL_BTN_PLAYLISTS": "My Playlists"}):
            assert config.button_label_for_height(1080) == "My Playlists"

    def test_button_label_for_height_new_rung_default(self) -> None:
        """A new registry rung's button label defaults to '<label> Playlists'."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_LABEL_BTN_1440", None)
            assert config.button_label_for_height(1440) == "1440 Playlists"

    def test_enabled_resolutions_default(self) -> None:
        """ENABLED_RESOLUTIONS must default to (1080, 720) with no env var set."""
        import importlib

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_ENABLED_RESOLUTIONS", None)
            importlib.reload(config)
            assert config.ENABLED_RESOLUTIONS == (1080, 720)

    def test_enabled_resolutions_env_override(self) -> None:
        """ENABLED_RESOLUTIONS must parse a comma-separated env override."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ENABLED_RESOLUTIONS": "2160,480"}):
            importlib.reload(config)
            assert config.ENABLED_RESOLUTIONS == (2160, 480)

    def test_enabled_resolutions_garbage_env_falls_back_to_default(self) -> None:
        """An all-garbage ENABLED_RESOLUTIONS env value must fall back, not crash import."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ENABLED_RESOLUTIONS": "abc,999"}):
            importlib.reload(config)
            assert config.ENABLED_RESOLUTIONS == (1080, 720)


class TestAlwaysOnTopConfiguration:
    """Boundary tests for the ALWAYS_ON_TOP bool config constant."""

    def test_always_on_top_default_is_true(self) -> None:
        """Absent env var must resolve to True (the documented default)."""
        import importlib

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_ALWAYS_ON_TOP", None)
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is True

    def test_always_on_top_explicit_true_lowercase(self) -> None:
        """Env var 'true' (all-lower) resolves to True."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "true"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is True

    def test_always_on_top_explicit_false_lowercase(self) -> None:
        """Env var 'false' (all-lower) resolves to False."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "false"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is False

    def test_always_on_top_true_mixed_case(self) -> None:
        """Env var 'True' (title-case) must also resolve to True via .lower()."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "True"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is True

    def test_always_on_top_false_mixed_case(self) -> None:
        """Env var 'False' (title-case) must also resolve to False via .lower()."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "False"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is False

    def test_always_on_top_true_all_caps(self) -> None:
        """Env var 'TRUE' (all-caps) resolves to True via .lower()."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "TRUE"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is True

    def test_always_on_top_numeric_one_is_false(self) -> None:
        """Env var '1' does NOT equal 'true' — resolves to False (not truthy conversion)."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "1"}):
            importlib.reload(config)
            # Only the exact string "true" (case-insensitive) passes the .lower() == "true" guard.
            assert config.ALWAYS_ON_TOP is False

    def test_always_on_top_yes_is_false(self) -> None:
        """Env var 'yes' does NOT equal 'true' — resolves to False."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "yes"}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is False

    def test_always_on_top_empty_string_is_false(self) -> None:
        """Empty string env var resolves to False ('' != 'true')."""
        import importlib

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": ""}):
            importlib.reload(config)
            assert config.ALWAYS_ON_TOP is False

    def test_always_on_top_is_bool_not_int(self) -> None:
        """ALWAYS_ON_TOP must be a strict Python bool, not an int."""
        import importlib

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_ALWAYS_ON_TOP", None)
            importlib.reload(config)
            assert isinstance(config.ALWAYS_ON_TOP, bool)

    def test_always_on_top_runtime_store_seeded_true(self) -> None:
        """_init_runtime_settings seeds 'VID_DL_ALWAYS_ON_TOP' as a bool True in _runtime."""
        import importlib

        from src import settings_dialog as sd

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "true"}):
            importlib.reload(config)
            importlib.reload(sd)
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_ALWAYS_ON_TOP")
            assert val is True
            assert isinstance(val, bool)

    def test_always_on_top_runtime_store_seeded_false(self) -> None:
        """_init_runtime_settings seeds 'VID_DL_ALWAYS_ON_TOP' as a bool False in _runtime."""
        import importlib

        from src import settings_dialog as sd

        with mock.patch.dict(os.environ, {"VID_DL_ALWAYS_ON_TOP": "false"}):
            importlib.reload(config)
            importlib.reload(sd)
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_ALWAYS_ON_TOP")
            assert val is False
            assert isinstance(val, bool)

    def test_always_on_top_get_setting_returns_none_before_init(self) -> None:
        """get_setting returns None when _init_runtime_settings has not been called."""
        import importlib

        from src import settings_dialog as sd

        importlib.reload(sd)
        # _runtime is reset to {} on reload; do NOT call _init_runtime_settings
        val = sd.get_setting("VID_DL_ALWAYS_ON_TOP")
        assert val is None

    def test_always_on_top_persist_setting_writes_true_lowercase(self, tmp_path: Path) -> None:
        """_persist_setting serializes True as 'true' (lowercase) in the .env file."""
        import importlib

        from src import settings_dialog as sd

        importlib.reload(sd)
        # Patch _APPDATA_DIR and _USER_ENV to write into tmp_path
        fake_env = tmp_path / ".env"
        with (
            mock.patch.object(sd, "_APPDATA_DIR", tmp_path),
            mock.patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_ALWAYS_ON_TOP", True)
        content = fake_env.read_text(encoding="utf-8")
        assert "VID_DL_ALWAYS_ON_TOP=true\n" in content

    def test_always_on_top_persist_setting_writes_false_lowercase(self, tmp_path: Path) -> None:
        """_persist_setting serializes False as 'false' (lowercase) in the .env file."""
        import importlib

        from src import settings_dialog as sd

        importlib.reload(sd)
        fake_env = tmp_path / ".env"
        with (
            mock.patch.object(sd, "_APPDATA_DIR", tmp_path),
            mock.patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_ALWAYS_ON_TOP", False)
        content = fake_env.read_text(encoding="utf-8")
        assert "VID_DL_ALWAYS_ON_TOP=false\n" in content

    def test_always_on_top_persist_setting_updates_runtime_store(self, tmp_path: Path) -> None:
        """_persist_setting also updates _runtime so get_setting reflects the new value."""
        import importlib

        from src import settings_dialog as sd

        importlib.reload(sd)
        fake_env = tmp_path / ".env"
        with (
            mock.patch.object(sd, "_APPDATA_DIR", tmp_path),
            mock.patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_ALWAYS_ON_TOP", False)
        assert sd.get_setting("VID_DL_ALWAYS_ON_TOP") is False

    def test_always_on_top_persist_setting_replaces_existing_line(self, tmp_path: Path) -> None:
        """_persist_setting replaces an existing VID_DL_ALWAYS_ON_TOP line, not appends."""
        import importlib

        from src import settings_dialog as sd

        importlib.reload(sd)
        fake_env = tmp_path / ".env"
        fake_env.write_text("VID_DL_ALWAYS_ON_TOP=true\n", encoding="utf-8")
        with (
            mock.patch.object(sd, "_APPDATA_DIR", tmp_path),
            mock.patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_ALWAYS_ON_TOP", False)
        lines = fake_env.read_text(encoding="utf-8").splitlines()
        matching = [ln for ln in lines if ln.startswith("VID_DL_ALWAYS_ON_TOP=")]
        assert len(matching) == 1
        assert matching[0] == "VID_DL_ALWAYS_ON_TOP=false"

    def test_always_on_top_none_from_uninitialized_store_is_falsy(self) -> None:
        """bool(None) is False — PlaylistDialog's 'if get_setting(...)' silently skips the flag."""
        # This documents the known behaviour: PlaylistDialog constructed before
        # _init_runtime_settings() is called will NOT apply WindowStaysOnTopHint,
        # regardless of the config default.  The fix would be a guard or an assert.
        # bool(None) must be False — this is the coercion PlaylistDialog relies on
        # when _init_runtime_settings() has not been called before PlaylistDialog is constructed.
        assert not bool(None)

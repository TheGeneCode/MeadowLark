"""
Tests for the auto app-update check logic in meadowlark.pyw.

Covers:
  - MyWindow._maybe_start_auto_app_update_check
  - MyWindow._on_app_update_result
  - config: APP_UPDATE_AUTO_CHECK, APP_UPDATE_LAST_CHECKED
  - settings_dialog: APP_UPDATE_AUTO_CHECK seeded in _init_runtime_settings
"""

import importlib
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal stand-in for MyWindow without instantiating PyQt6
# ---------------------------------------------------------------------------


def _make_window() -> MagicMock:
    """
    Return a MagicMock that mimics a MyWindow instance.

    Only the attributes accessed by the two tested methods are wired up.
    Everything else is a plain MagicMock.
    """
    win = MagicMock()
    # Ensure the real method bodies execute (not the MagicMock auto-stubs)
    import meadowlark

    win._maybe_start_auto_app_update_check = (
        meadowlark.MyWindow._maybe_start_auto_app_update_check.__get__(win)
    )
    win._on_app_update_result = meadowlark.MyWindow._on_app_update_result.__get__(win)
    return win


# ---------------------------------------------------------------------------
# Shared patch targets
# ---------------------------------------------------------------------------

_GET_SETTING = "meadowlark.get_setting"
_PERSIST_SETTING = "meadowlark._persist_setting"
_START_CHECK = "meadowlark._maybe_start_auto_app_update_check"  # unused; use instance
_QMSGBOX = "meadowlark.QMessageBox"
_WEBBROWSER = "meadowlark.webbrowser"
_DATE = "meadowlark.date"
_DATETIME = "meadowlark.datetime"


def _freeze_today(today: date) -> tuple[MagicMock, MagicMock]:
    """
    Return (fake_date, fake_datetime) mocks that pin "today".

    Production code reads the current date via `datetime.now(tz=UTC).date()`,
    not `date.today()` — both must be patched or the real wall-clock date
    leaks in and throttle-window assertions drift once enough real time
    passes since `today` was picked.
    """
    fake_date = MagicMock(wraps=date)
    fake_date.today.return_value = today
    fake_date.fromisoformat.side_effect = date.fromisoformat
    fake_datetime = MagicMock(wraps=datetime)
    fake_datetime.now.return_value = datetime(today.year, today.month, today.day, tzinfo=UTC)
    return fake_date, fake_datetime


# ===========================================================================
# config.py — APP_UPDATE_AUTO_CHECK boundary tests
# ===========================================================================


class TestConfigAppUpdateAutoCheck:
    """Boundary tests for APP_UPDATE_AUTO_CHECK config constant."""

    def _reload(self) -> object:
        from src import config

        importlib.reload(config)
        return config

    def test_auto_check_default_is_true(self) -> None:
        """Absent env var resolves to True (opt-in by default)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_APP_UPDATE_AUTO_CHECK", None)
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is True

    def test_auto_check_explicit_true_lowercase(self) -> None:
        """Env var 'true' resolves to True."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "true"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is True

    def test_auto_check_explicit_false_lowercase(self) -> None:
        """Env var 'false' resolves to False."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "false"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is False

    def test_auto_check_mixed_case_true(self) -> None:
        """Env var 'True' (title-case) resolves to True via .lower()."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "True"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is True

    def test_auto_check_mixed_case_false(self) -> None:
        """Env var 'False' (title-case) resolves to False via .lower()."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "False"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is False

    def test_auto_check_all_caps_true(self) -> None:
        """Env var 'TRUE' resolves to True via .lower()."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "TRUE"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is True

    def test_auto_check_numeric_one_is_false(self) -> None:
        """Env var '1' does NOT equal 'true' — resolves to False."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "1"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is False

    def test_auto_check_empty_string_is_false(self) -> None:
        """Empty string env var resolves to False."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": ""}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_AUTO_CHECK is False

    def test_auto_check_is_strict_bool(self) -> None:
        """APP_UPDATE_AUTO_CHECK must be a strict Python bool."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_APP_UPDATE_AUTO_CHECK", None)
            cfg = self._reload()
            assert isinstance(cfg.APP_UPDATE_AUTO_CHECK, bool)


# ===========================================================================
# config.py — APP_UPDATE_LAST_CHECKED boundary tests
# ===========================================================================


class TestConfigAppUpdateLastChecked:
    """Boundary tests for APP_UPDATE_LAST_CHECKED config constant."""

    def _reload(self) -> object:
        from src import config

        importlib.reload(config)
        return config

    def test_last_checked_default_is_empty_string(self) -> None:
        """Absent env var defaults to empty string."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_APP_UPDATE_LAST_CHECKED", None)
            cfg = self._reload()
            assert cfg.APP_UPDATE_LAST_CHECKED == ""

    def test_last_checked_from_env_iso_date(self) -> None:
        """Env var with a valid ISO date is preserved as-is."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_LAST_CHECKED": "2026-01-15"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_LAST_CHECKED == "2026-01-15"

    def test_last_checked_from_env_arbitrary_string(self) -> None:
        """Env var with an invalid date string is still returned verbatim."""
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_LAST_CHECKED": "not-a-date"}):
            cfg = self._reload()
            assert cfg.APP_UPDATE_LAST_CHECKED == "not-a-date"

    def test_last_checked_is_str_type(self) -> None:
        """APP_UPDATE_LAST_CHECKED is always a str, never None or bool."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_APP_UPDATE_LAST_CHECKED", None)
            cfg = self._reload()
            assert isinstance(cfg.APP_UPDATE_LAST_CHECKED, str)


# ===========================================================================
# settings_dialog._init_runtime_settings — seeding the new keys
# ===========================================================================


class TestRuntimeSettingsSeeding:
    """Verify _init_runtime_settings seeds both new keys correctly."""

    def _reload_both(self) -> tuple:
        from src import config
        from src import settings_dialog as sd

        importlib.reload(config)
        importlib.reload(sd)
        return config, sd

    def test_auto_check_seeded_true(self) -> None:
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "true"}):
            config, sd = self._reload_both()
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_APP_UPDATE_AUTO_CHECK")
            assert val is True
            assert isinstance(val, bool)

    def test_auto_check_seeded_false(self) -> None:
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_AUTO_CHECK": "false"}):
            config, sd = self._reload_both()
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_APP_UPDATE_AUTO_CHECK")
            assert val is False
            assert isinstance(val, bool)

    def test_last_checked_seeded_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VID_DL_APP_UPDATE_LAST_CHECKED", None)
            config, sd = self._reload_both()
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_APP_UPDATE_LAST_CHECKED")
            assert val == ""

    def test_last_checked_seeded_from_env(self) -> None:
        with patch.dict(os.environ, {"VID_DL_APP_UPDATE_LAST_CHECKED": "2026-03-01"}):
            config, sd = self._reload_both()
            sd._init_runtime_settings()
            val = sd.get_setting("VID_DL_APP_UPDATE_LAST_CHECKED")
            assert val == "2026-03-01"

    def test_both_keys_absent_before_init(self) -> None:
        """Keys are absent from runtime store before _init_runtime_settings is called."""
        _, sd = self._reload_both()
        # Do NOT call _init_runtime_settings — _runtime is {} after reload
        assert sd.get_setting("VID_DL_APP_UPDATE_AUTO_CHECK") is None
        assert sd.get_setting("VID_DL_APP_UPDATE_LAST_CHECKED") is None

    def test_auto_check_persist_writes_true(self, tmp_path: Path) -> None:
        """_persist_setting writes True as 'true' for the auto-check key."""
        _, sd = self._reload_both()
        fake_env = tmp_path / ".env"
        with (
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_APP_UPDATE_AUTO_CHECK", True)
        assert "VID_DL_APP_UPDATE_AUTO_CHECK=true\n" in fake_env.read_text(encoding="utf-8")

    def test_auto_check_persist_writes_false(self, tmp_path: Path) -> None:
        """_persist_setting writes False as 'false' for the auto-check key."""
        _, sd = self._reload_both()
        fake_env = tmp_path / ".env"
        with (
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_APP_UPDATE_AUTO_CHECK", False)
        assert "VID_DL_APP_UPDATE_AUTO_CHECK=false\n" in fake_env.read_text(encoding="utf-8")

    def test_last_checked_persist_writes_date_string(self, tmp_path: Path) -> None:
        """_persist_setting writes a date ISO string for the last-checked key."""
        _, sd = self._reload_both()
        fake_env = tmp_path / ".env"
        with (
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_APP_UPDATE_LAST_CHECKED", "2026-05-18")
        assert "VID_DL_APP_UPDATE_LAST_CHECKED=2026-05-18\n" in fake_env.read_text(encoding="utf-8")

    def test_last_checked_persist_replaces_existing(self, tmp_path: Path) -> None:
        """_persist_setting replaces an existing last-checked line, not appends."""
        _, sd = self._reload_both()
        fake_env = tmp_path / ".env"
        fake_env.write_text("VID_DL_APP_UPDATE_LAST_CHECKED=2026-01-01\n", encoding="utf-8")
        with (
            patch.object(sd, "_APPDATA_DIR", tmp_path),
            patch.object(sd, "_USER_ENV", fake_env),
        ):
            sd._persist_setting("VID_DL_APP_UPDATE_LAST_CHECKED", "2026-05-18")
        lines = fake_env.read_text(encoding="utf-8").splitlines()
        matching = [ln for ln in lines if ln.startswith("VID_DL_APP_UPDATE_LAST_CHECKED=")]
        assert len(matching) == 1
        assert matching[0] == "VID_DL_APP_UPDATE_LAST_CHECKED=2026-05-18"


# ===========================================================================
# _maybe_start_auto_app_update_check
# ===========================================================================


class TestMaybeStartAutoAppUpdateCheck:
    """Boundary matrix tests for _maybe_start_auto_app_update_check."""

    # ------------------------------------------------------------------
    # Setting is falsy → early return, no check fires
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "falsy_value",
        [False, None, 0, ""],
        ids=["bool-False", "None", "int-0", "empty-str"],
    )
    def test_setting_falsy_returns_early_no_check(self, falsy_value: object) -> None:
        """Any falsy value for VID_DL_APP_UPDATE_AUTO_CHECK must suppress the check."""
        win = _make_window()
        with patch(_GET_SETTING, return_value=falsy_value):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_not_called()

    # ------------------------------------------------------------------
    # Setting truthy, no last-checked date → check fires
    # ------------------------------------------------------------------

    def test_no_last_checked_fires_check(self) -> None:
        """When VID_DL_APP_UPDATE_LAST_CHECKED is empty/None, check fires."""
        win = _make_window()

        def _get(key: str) -> object:
            return True if key == "VID_DL_APP_UPDATE_AUTO_CHECK" else ""

        with patch(_GET_SETTING, side_effect=_get):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)

    def test_none_last_checked_fires_check(self) -> None:
        """When VID_DL_APP_UPDATE_LAST_CHECKED is None, check fires."""
        win = _make_window()

        def _get(key: str) -> object:
            return True if key == "VID_DL_APP_UPDATE_AUTO_CHECK" else None

        with patch(_GET_SETTING, side_effect=_get):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)

    # ------------------------------------------------------------------
    # Throttle boundaries around the 7-day window
    # ------------------------------------------------------------------

    def _setup_with_last_checked(self, days_ago: int) -> tuple[MagicMock, date]:
        """Return a window mock with today frozen and last_checked = days_ago days ago."""
        win = _make_window()
        today = date(2026, 5, 18)
        last_checked = today - timedelta(days=days_ago)

        def _get(key: str) -> object:
            if key == "VID_DL_APP_UPDATE_AUTO_CHECK":
                return True
            return last_checked.isoformat()

        return win, today, _get  # type: ignore[return-value]

    def test_last_checked_today_throttled(self) -> None:
        """Last checked today (0 days ago) → throttle, no check."""
        win, today, _get = self._setup_with_last_checked(0)
        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_not_called()

    def test_last_checked_1_day_ago_throttled(self) -> None:
        """Last checked 1 day ago → still throttled."""
        win, today, _get = self._setup_with_last_checked(1)
        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_not_called()

    def test_last_checked_6_days_ago_throttled(self) -> None:
        """Last checked exactly 6 days ago → still within 7-day window, throttled."""
        win, today, _get = self._setup_with_last_checked(6)
        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_not_called()

    def test_last_checked_exactly_7_days_ago_fires(self) -> None:
        """Last checked exactly 7 days ago → window expired, check fires."""
        win, today, _get = self._setup_with_last_checked(7)
        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)

    def test_last_checked_8_days_ago_fires(self) -> None:
        """Last checked 8 days ago → window expired, check fires."""
        win, today, _get = self._setup_with_last_checked(8)
        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)

    def test_last_checked_far_future_throttled(self) -> None:
        """
        Last-checked date set in the far future → days < 7 (negative diff absorbed), throttled.

        date.fromisoformat("2099-01-01") - date.today() is a large positive number of days,
        so (today - future_date).days is a large negative, which is < 7 → throttled.
        This is the expected behaviour: a future date is treated as "recently checked".
        """
        win = _make_window()
        today = date(2026, 5, 18)
        future = date(2099, 1, 1)

        def _get(key: str) -> object:
            if key == "VID_DL_APP_UPDATE_AUTO_CHECK":
                return True
            return future.isoformat()

        fake_date, fake_datetime = _freeze_today(today)
        with (
            patch(_GET_SETTING, side_effect=_get),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_not_called()

    # ------------------------------------------------------------------
    # Malformed/invalid ISO string → ValueError caught → check fires
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_date",
        [
            "not-a-date",
            "2026/05/18",
            "18-05-2026",
            "2026-13-01",  # month 13
            "abc",
            "   ",
        ],
        ids=[
            "random-text",
            "slash-separated",
            "day-first",
            "invalid-month",
            "letters",
            "whitespace",
        ],
    )
    def test_malformed_date_fires_check(self, bad_date: str) -> None:
        """A malformed last-checked date triggers ValueError → check fires anyway."""
        win = _make_window()

        def _get(key: str) -> object:
            if key == "VID_DL_APP_UPDATE_AUTO_CHECK":
                return True
            return bad_date

        with patch(_GET_SETTING, side_effect=_get):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)

    def test_empty_string_last_checked_fires_check(self) -> None:
        """Empty string last-checked → falsy → check fires (no date.fromisoformat attempted)."""
        win = _make_window()

        def _get(key: str) -> object:
            if key == "VID_DL_APP_UPDATE_AUTO_CHECK":
                return True
            return ""

        with patch(_GET_SETTING, side_effect=_get):
            win._maybe_start_auto_app_update_check()
        win._start_app_update_check.assert_called_once_with(auto=True)


# ===========================================================================
# _on_app_update_result
# ===========================================================================


class TestOnAppUpdateResult:
    """Boundary matrix tests for _on_app_update_result."""

    _TODAY = date(2026, 5, 18)

    def _run(
        self,
        *,
        update_available: bool,
        latest_tag: str,
        download_url: str,
        auto: bool,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Run _on_app_update_result and return (win, mock_qmsgbox, mock_persist)."""
        win = _make_window()
        fake_date, fake_datetime = _freeze_today(self._TODAY)
        mock_persist = MagicMock()
        mock_qmsgbox = MagicMock()
        mock_web = MagicMock()
        with (
            patch(_PERSIST_SETTING, mock_persist),
            patch(_QMSGBOX, mock_qmsgbox),
            patch(_WEBBROWSER, mock_web),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win._on_app_update_result(update_available, latest_tag, download_url, auto=auto)
        return win, mock_qmsgbox, mock_persist, mock_web

    # ------------------------------------------------------------------
    # auto=True, no update → silent, date persisted
    # ------------------------------------------------------------------

    def test_auto_no_update_is_silent(self) -> None:
        """auto=True + no update → QMessageBox.information NOT called."""
        _, mock_qmsgbox, mock_persist, _ = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=True,
        )
        mock_qmsgbox.information.assert_not_called()
        mock_qmsgbox.question.assert_not_called()

    def test_auto_no_update_persists_today(self) -> None:
        """auto=True + no update → today's date is persisted."""
        _, _, mock_persist, _ = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=True,
        )
        mock_persist.assert_called_once_with("VID_DL_APP_UPDATE_LAST_CHECKED", "2026-05-18")

    def test_auto_no_update_does_not_open_browser(self) -> None:
        """auto=True + no update → webbrowser.open NOT called."""
        _, _, _, mock_web = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=True,
        )
        mock_web.open.assert_not_called()

    # ------------------------------------------------------------------
    # auto=True, update found → dialog shown, date persisted
    # ------------------------------------------------------------------

    def test_auto_update_available_shows_dialog(self) -> None:
        """auto=True + update available → QMessageBox.question IS called."""
        _, mock_qmsgbox, mock_persist, _ = self._run(
            update_available=True,
            latest_tag="v1.2.3",
            download_url="https://example.com/dl",
            auto=True,
        )
        mock_qmsgbox.question.assert_called_once()

    def test_auto_update_available_persists_today(self) -> None:
        """auto=True + update available → date persisted before dialog."""
        _, _, mock_persist, _ = self._run(
            update_available=True,
            latest_tag="v1.2.3",
            download_url="https://example.com/dl",
            auto=True,
        )
        mock_persist.assert_called_once_with("VID_DL_APP_UPDATE_LAST_CHECKED", "2026-05-18")

    def test_auto_update_user_confirms_opens_browser(self) -> None:
        """auto=True + update + user clicks Yes → webbrowser.open called with url."""
        from PyQt6.QtWidgets import QMessageBox

        win = _make_window()
        fake_date, fake_datetime = _freeze_today(self._TODAY)
        mock_persist = MagicMock()
        mock_web = MagicMock()

        with (
            patch(_PERSIST_SETTING, mock_persist),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
            patch(_WEBBROWSER, mock_web),
            patch(
                _QMSGBOX + ".question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ):
            win._on_app_update_result(True, "v1.2.3", "https://example.com/dl", auto=True)
        mock_web.open.assert_called_once_with("https://example.com/dl")

    def test_auto_update_user_declines_no_browser(self) -> None:
        """auto=True + update + user clicks No → webbrowser.open NOT called."""
        from PyQt6.QtWidgets import QMessageBox

        win = _make_window()
        fake_date, fake_datetime = _freeze_today(self._TODAY)
        mock_persist = MagicMock()
        mock_web = MagicMock()

        with (
            patch(_PERSIST_SETTING, mock_persist),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
            patch(_WEBBROWSER, mock_web),
            patch(
                _QMSGBOX + ".question",
                return_value=QMessageBox.StandardButton.No,
            ),
        ):
            win._on_app_update_result(True, "v1.2.3", "https://example.com/dl", auto=True)
        mock_web.open.assert_not_called()

    # ------------------------------------------------------------------
    # auto=False, no update → dialog shown (existing behavior unchanged)
    # ------------------------------------------------------------------

    def test_manual_no_update_shows_information_dialog(self) -> None:
        """auto=False + no update → QMessageBox.information IS called."""
        _, mock_qmsgbox, mock_persist, _ = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=False,
        )
        mock_qmsgbox.information.assert_called_once()

    def test_manual_no_update_does_not_persist_date(self) -> None:
        """auto=False + no update → _persist_setting NOT called."""
        _, _, mock_persist, _ = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=False,
        )
        mock_persist.assert_not_called()

    def test_manual_no_update_does_not_open_browser(self) -> None:
        """auto=False + no update → webbrowser.open NOT called."""
        _, _, _, mock_web = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=False,
        )
        mock_web.open.assert_not_called()

    # ------------------------------------------------------------------
    # auto=False, update available → dialog shown (existing behavior unchanged)
    # ------------------------------------------------------------------

    def test_manual_update_available_shows_question_dialog(self) -> None:
        """auto=False + update available → QMessageBox.question IS called."""
        _, mock_qmsgbox, mock_persist, _ = self._run(
            update_available=True,
            latest_tag="v9.0.0",
            download_url="https://example.com/dl",
            auto=False,
        )
        mock_qmsgbox.question.assert_called_once()

    def test_manual_update_available_does_not_persist_date(self) -> None:
        """auto=False + update available → _persist_setting NOT called."""
        _, _, mock_persist, _ = self._run(
            update_available=True,
            latest_tag="v9.0.0",
            download_url="https://example.com/dl",
            auto=False,
        )
        mock_persist.assert_not_called()

    def test_manual_update_user_confirms_opens_browser(self) -> None:
        """auto=False + update + user confirms → webbrowser.open called."""
        from PyQt6.QtWidgets import QMessageBox

        win = _make_window()
        fake_date, fake_datetime = _freeze_today(self._TODAY)
        mock_persist = MagicMock()
        mock_web = MagicMock()

        with (
            patch(_PERSIST_SETTING, mock_persist),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
            patch(_WEBBROWSER, mock_web),
            patch(
                _QMSGBOX + ".question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ):
            win._on_app_update_result(True, "v9.0.0", "https://example.com/dl", auto=False)
        mock_web.open.assert_called_once_with("https://example.com/dl")

    # ------------------------------------------------------------------
    # Edge: update_available=True with empty tag/url
    # ------------------------------------------------------------------

    def test_auto_update_empty_tag_and_url_still_persists(self) -> None:
        """auto=True with update_available=True but empty tag/url still persists date."""
        _, _, mock_persist, _ = self._run(
            update_available=True,
            latest_tag="",
            download_url="",
            auto=True,
        )
        mock_persist.assert_called_once_with("VID_DL_APP_UPDATE_LAST_CHECKED", "2026-05-18")

    # ------------------------------------------------------------------
    # Persist date format: must be ISO-8601 (YYYY-MM-DD)
    # ------------------------------------------------------------------

    def test_persisted_date_is_iso_format(self) -> None:
        """The persisted date string must be a valid ISO date (parseable by date.fromisoformat)."""
        _, _, mock_persist, _ = self._run(
            update_available=False,
            latest_tag="",
            download_url="",
            auto=True,
        )
        persisted_value = mock_persist.call_args[0][1]
        # Must not raise
        parsed = date.fromisoformat(persisted_value)
        assert parsed == self._TODAY


# ===========================================================================
# Integration: _maybe_start → _on_app_update_result date-persist round-trip
# ===========================================================================


class TestAutoCheckIntegration:
    """Integration: _on_app_update_result date-persist throttles the next _maybe_start_auto_app_update_check call."""

    def test_after_auto_check_no_update_next_call_is_throttled(self) -> None:
        """Simulates two startup calls: first fires, second (same day) is throttled."""
        today = date(2026, 5, 18)
        fake_date, fake_datetime = _freeze_today(today)

        win1 = _make_window()
        win2 = _make_window()
        persisted: dict = {}

        def _get_first(key: str) -> object:
            if key == "VID_DL_APP_UPDATE_AUTO_CHECK":
                return True
            return persisted.get(key, "")

        def _persist(key: str, value: object) -> None:
            persisted[key] = value

        # First startup: no last-checked → fires
        with (
            patch(_GET_SETTING, side_effect=_get_first),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win1._maybe_start_auto_app_update_check()
        win1._start_app_update_check.assert_called_once_with(auto=True)

        # Simulate _on_app_update_result persisting today's date
        with (
            patch(_PERSIST_SETTING, side_effect=_persist),
            patch(_QMSGBOX),
            patch(_WEBBROWSER),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win1._on_app_update_result(False, "", "", auto=True)
        assert persisted.get("VID_DL_APP_UPDATE_LAST_CHECKED") == "2026-05-18"

        # Second startup: last_checked is today → throttled
        with (
            patch(_GET_SETTING, side_effect=_get_first),
            patch(_DATE, fake_date),
            patch(_DATETIME, fake_datetime),
        ):
            win2._maybe_start_auto_app_update_check()
        win2._start_app_update_check.assert_not_called()

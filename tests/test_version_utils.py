"""Unit tests for yt-dlp version and update utilities."""

from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock, Mock, patch

import requests

from src.version_utils import (
    APP_VERSION,
    _resolve_app_version,
    get_current_yt_dlp_version,
    get_latest_app_release,
    get_latest_yt_dlp_version,
    get_publish_date,
    is_app_update_available,
    is_yt_dlp_update_available,
    normalize_version,
)

# --- _resolve_app_version ---


def test_resolve_app_version_returns_package_version_when_installed() -> None:
    with patch("src.version_utils._pkg_version", return_value="1.2.3"):
        assert _resolve_app_version() == "1.2.3"


def test_resolve_app_version_returns_dev_when_package_not_found() -> None:
    with patch(
        "src.version_utils._pkg_version",
        side_effect=PackageNotFoundError("meadowlark"),
    ):
        assert _resolve_app_version() == "dev"


def test_app_version_is_string() -> None:
    assert isinstance(APP_VERSION, str)
    assert len(APP_VERSION) > 0


# --- get_publish_date ---


def test_get_publish_date_returns_formatted_date_for_valid_version() -> None:
    with patch("src.version_utils.APP_VERSION", "0.1.4_2025-08-27"):
        assert get_publish_date() == "August 27, 2025"


def test_get_publish_date_returns_none_when_no_underscore() -> None:
    with patch("src.version_utils.APP_VERSION", "0.1.4"):
        assert get_publish_date() is None


def test_get_publish_date_returns_none_for_dev_version() -> None:
    with patch("src.version_utils.APP_VERSION", "dev"):
        assert get_publish_date() is None


def test_get_publish_date_returns_none_for_malformed_date_suffix() -> None:
    with patch("src.version_utils.APP_VERSION", "0.1.4_not-a-date"):
        assert get_publish_date() is None


def test_get_publish_date_returns_none_for_empty_suffix() -> None:
    # Edge: underscore present but nothing after it
    with patch("src.version_utils.APP_VERSION", "0.1.4_"):
        assert get_publish_date() is None


# --- normalize_version ---


def test_normalize_version_parses_numeric_segments() -> None:
    assert normalize_version("2025.08.27") == (2025, 8, 27)
    assert normalize_version("2025.8.27") == (2025, 8, 27)
    assert normalize_version("1.2") == (1, 2)


def test_normalize_version_returns_empty_for_invalid_input() -> None:
    assert normalize_version("") == ()
    assert normalize_version(None) == ()  # type: ignore[arg-type]
    assert normalize_version("abc") == ()


def test_get_current_yt_dlp_version_uses_version_attribute() -> None:
    module = MagicMock()
    module.version.__version__ = "2026.01.01"
    with patch("src.version_utils.yt_dlp", module):
        assert get_current_yt_dlp_version() == "2026.01.01"


def test_get_latest_yt_dlp_version_returns_none_on_error() -> None:
    with patch(
        "src.version_utils.requests.get",
        side_effect=requests.exceptions.RequestException("fail"),
    ):
        assert get_latest_yt_dlp_version() is None


def test_get_latest_yt_dlp_version_success() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"info": {"version": "2026.02.02"}}
    with patch("src.version_utils.requests.get", return_value=response):
        assert get_latest_yt_dlp_version() == "2026.02.02"


def test_is_yt_dlp_update_available_detects_newer_version() -> None:
    with (
        patch(
            "src.version_utils.get_current_yt_dlp_version",
            return_value="2026.01.01",
        ),
        patch(
            "src.version_utils.get_latest_yt_dlp_version",
            return_value="2026.02.02",
        ),
    ):
        available, current, latest = is_yt_dlp_update_available()
        assert available is True
        assert current == (2026, 1, 1)
        assert latest == (2026, 2, 2)


def test_get_current_yt_dlp_version_falls_back_to_module_attribute() -> None:
    module = MagicMock()
    version_mock = MagicMock(spec=[])  # no __version__ in spec → AttributeError
    module.version = version_mock
    module.__version__ = "2026.03.03"
    with patch("src.version_utils.yt_dlp", module):
        result = get_current_yt_dlp_version()
    assert result == "2026.03.03"


def test_get_latest_yt_dlp_version_returns_none_for_non_200_status() -> None:
    response = Mock()
    response.status_code = 404
    with patch("src.version_utils.requests.get", return_value=response):
        assert get_latest_yt_dlp_version() is None


def test_get_current_yt_dlp_version_returns_none_on_import_error() -> None:
    class FakeYtDlp:
        @property
        def version(self):
            raise ImportError("yt_dlp.version not available")

    with patch("src.version_utils.yt_dlp", FakeYtDlp()):
        result = get_current_yt_dlp_version()
    assert result is None


def test_is_yt_dlp_update_available_returns_false_when_up_to_date() -> None:
    with (
        patch(
            "src.version_utils.get_current_yt_dlp_version",
            return_value="2026.02.02",
        ),
        patch(
            "src.version_utils.get_latest_yt_dlp_version",
            return_value="2026.02.02",
        ),
    ):
        available, current, latest = is_yt_dlp_update_available()
        assert available is False
        assert current == (2026, 2, 2)
        assert latest == (2026, 2, 2)


def test_is_yt_dlp_update_available_flag_is_bool_when_version_unknown() -> None:
    with (
        patch("src.version_utils.get_current_yt_dlp_version", return_value=None),
        patch(
            "src.version_utils.get_latest_yt_dlp_version",
            return_value="2026.02.02",
        ),
    ):
        available, current, latest = is_yt_dlp_update_available()
        assert available is False
        assert current is None
        assert latest == (2026, 2, 2)


# --- get_latest_app_release ---


def test_get_latest_app_release_returns_first_release() -> None:
    releases = [{"tag_name": "v0.2.0"}, {"tag_name": "v0.1.0"}]
    response = Mock()
    response.status_code = 200
    response.json.return_value = releases
    with patch("src.version_utils.requests.get", return_value=response):
        result = get_latest_app_release()
    assert result == {"tag_name": "v0.2.0"}


def test_get_latest_app_release_returns_none_for_empty_list() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = []
    with patch("src.version_utils.requests.get", return_value=response):
        assert get_latest_app_release() is None


def test_get_latest_app_release_returns_none_for_non_200() -> None:
    response = Mock()
    response.status_code = 403
    with patch("src.version_utils.requests.get", return_value=response):
        assert get_latest_app_release() is None


def test_get_latest_app_release_returns_none_on_network_error() -> None:
    with patch(
        "src.version_utils.requests.get",
        side_effect=requests.exceptions.RequestException("timeout"),
    ):
        assert get_latest_app_release() is None


# --- is_app_update_available ---


def test_is_app_update_available_detects_newer_version() -> None:
    release = {
        "tag_name": "v99.9.9",
        "html_url": "https://github.com/TheGeneCode/MeadowLark/releases/tag/v99.9.9",
        "assets": [
            {
                "name": "MeadowLark-Setup-99.9.9.exe",
                "browser_download_url": "https://example.com/setup.exe",
            },
        ],
    }
    with (
        patch("src.version_utils.APP_VERSION", "0.1.0"),
        patch("src.version_utils.get_latest_app_release", return_value=release),
    ):
        available, tag, url = is_app_update_available()
    assert available is True
    assert tag == "v99.9.9"
    assert url == "https://example.com/setup.exe"


def test_is_app_update_available_falls_back_to_html_url_when_no_exe_asset() -> None:
    release = {
        "tag_name": "v99.9.9",
        "html_url": "https://github.com/TheGeneCode/MeadowLark/releases/tag/v99.9.9",
        "assets": [],
    }
    with (
        patch("src.version_utils.APP_VERSION", "0.1.0"),
        patch("src.version_utils.get_latest_app_release", return_value=release),
    ):
        available, tag, url = is_app_update_available()
    assert available is True
    assert url == "https://github.com/TheGeneCode/MeadowLark/releases/tag/v99.9.9"


def test_is_app_update_available_returns_false_when_up_to_date() -> None:
    release = {"tag_name": APP_VERSION, "html_url": "", "assets": []}
    with patch("src.version_utils.get_latest_app_release", return_value=release):
        available, tag, url = is_app_update_available()
    assert available is False
    assert tag is None
    assert url is None


def test_is_app_update_available_returns_false_on_api_failure() -> None:
    with patch("src.version_utils.get_latest_app_release", return_value=None):
        available, tag, url = is_app_update_available()
    assert available is False
    assert tag is None
    assert url is None

"""Version checking and comparison utilities for yt-dlp and the app itself."""

import http
import re
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import requests
import yt_dlp


def _resolve_app_version() -> str:
    try:
        return _pkg_version("meadowlark")
    except PackageNotFoundError:
        return "dev"


APP_VERSION: str = _resolve_app_version()
GITHUB_REPO_URL: str = "https://github.com/TheGeneCode/MeadowLark"


def get_publish_date() -> str | None:
    """
    Extract the publish date from the APP_VERSION string.

    Expects version format like "0.1.4_2025-08-27" where the date is appended
    after an underscore. If no date is found, returns None.

    Returns:
        Formatted date string (e.g., "August 27, 2025") or None.
    """
    if "_" not in APP_VERSION:
        return None
    date_part = APP_VERSION.split("_", 1)[-1]
    try:
        dt = datetime.strptime(date_part, "%Y-%m-%d").date()
        return dt.strftime("%B %d, %Y")
    except ValueError:
        return None


_PYPI_API_TIMEOUT: int = 3
_GITHUB_API_TIMEOUT: int = 5
_GITHUB_RELEASES_URL: str = "https://api.github.com/repos/TheGeneCode/MeadowLark/releases"


def normalize_version(version: str | None) -> tuple[int, ...]:
    """
    Normalize a version string like '2025.08.27' or '2025.8.27' to a tuple of ints.

    Args:
        version: Version string to normalize (e.g., '2025.08.27').

    Returns:
        Tuple of integers, e.g., (2025, 8, 27). Returns empty tuple if not a string.
    """
    if not isinstance(version, str):
        return ()
    # Match all dot-separated numeric sequences
    parts = re.findall(r"\d+", version)
    return tuple(int(x) for x in parts)


def get_current_yt_dlp_version() -> str | None:
    """
    Safely get the installed yt-dlp version.

    Returns:
        Version string (e.g., '2025.08.27') or None if unable to determine.
    """
    try:
        try:
            return yt_dlp.version.__version__
        except AttributeError:
            return yt_dlp.__version__
    except ImportError:
        return None


def get_latest_yt_dlp_version() -> str | None:
    """
    Fetch the latest yt-dlp version from PyPI.

    Returns:
        Latest version string or None if unable to fetch.
    """
    try:
        r = requests.get("https://pypi.org/pypi/yt-dlp/json", timeout=_PYPI_API_TIMEOUT)
        if r.status_code == http.HTTPStatus.OK:
            return r.json()["info"]["version"]
        return None
    except requests.exceptions.RequestException:
        return None


def is_yt_dlp_update_available() -> tuple[
    bool,
    tuple[int, ...] | None,
    tuple[int, ...] | None,
]:
    """
    Check whether a newer yt-dlp version is available.

    Returns:
        Tuple of (update_available: bool, current_version: tuple, latest_version: tuple).
    """
    current = normalize_version(get_current_yt_dlp_version() or "")
    latest = normalize_version(get_latest_yt_dlp_version() or "")
    update = (current and latest) and (current != latest)
    return update, current or None, latest or None


def get_latest_app_release() -> dict | None:
    """
    Fetch the most recent app release from GitHub (includes pre-releases).

    Returns:
        Release dict from the GitHub API, or None on any error.
    """
    try:
        r = requests.get(_GITHUB_RELEASES_URL, timeout=_GITHUB_API_TIMEOUT)
        if r.status_code == http.HTTPStatus.OK:
            releases = r.json()
            return releases[0] if releases else None
        return None
    except requests.exceptions.RequestException:
        return None


def is_app_update_available() -> tuple[bool, str | None, str | None]:
    """
    Check whether a newer app version is available on GitHub.

    Returns:
        Tuple of (update_available, latest_tag_name, download_url).
        download_url is the first .exe asset URL, falling back to the release html_url.
        Returns (False, None, None) on any API failure.
    """
    release = get_latest_app_release()
    if release is None:
        return False, None, None

    tag = release.get("tag_name", "")
    current = normalize_version(APP_VERSION)
    latest = normalize_version(tag)

    if not (current and latest) or latest <= current:
        return False, None, None

    assets: list[dict] = release.get("assets", [])
    exe_asset = next((a for a in assets if a.get("name", "").endswith(".exe")), None)
    download_url: str = (
        exe_asset["browser_download_url"] if exe_asset else release.get("html_url", "")
    )

    return True, tag, download_url

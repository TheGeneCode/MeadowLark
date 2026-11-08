"""
Guards that the bgutil plugin and the vendored server stay on the same version.

The ``bgutil-ytdlp-pot-provider`` plugin (a PyPI dependency) and the server script it
runs (vendored under ``vendor/bgutil-pot-provider/``) must move together. A
lockfile-only bump of the plugin makes its ``_check_version`` fail, so no GVS PO token
is minted and every 1080p download 403s at the media stage.

On failure, follow "Updating the pin" in ``vendor/bgutil-pot-provider/README.md``.
"""

import json
import re
import tomllib
from pathlib import Path

from src import pot_provider

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _REPO_ROOT / "vendor" / "bgutil-pot-provider"
_PIN_HINT = "follow 'Updating the pin' in vendor/bgutil-pot-provider/README.md"
_DENO_DEP_RE = re.compile(r"^deno\s*>=\s*(\d+)\.(\d+)\.(\d+)")


def _vendored_version() -> str:
    package_json = json.loads((_VENDOR / "server" / "package.json").read_text(encoding="utf-8"))
    return str(package_json["version"])


def _project_dependencies() -> list[str]:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(pyproject["project"]["dependencies"])


def test_vendored_server_matches_installed_plugin() -> None:
    from yt_dlp_plugins.extractor import getpot_bgutil

    # __version__ is the value the plugin's _check_version compares against the script.
    assert getpot_bgutil.__version__ == _vendored_version(), (
        f"bgutil plugin and vendored server differ; {_PIN_HINT}"
    )


def test_vendor_readme_records_current_pin() -> None:
    readme = (_VENDOR / "README.md").read_text(encoding="utf-8")
    match = re.search(r"\*\*Pinned tag:\*\* `([^`]+)`", readme)
    assert match is not None, "vendor README lost its 'Pinned tag' line"
    assert match.group(1) == _vendored_version(), f"README pin is stale; {_PIN_HINT}"


def test_pyproject_floor_is_vendored_version() -> None:
    expected = f"bgutil-ytdlp-pot-provider>={_vendored_version()}"
    assert expected in _project_dependencies(), (
        f"pyproject.toml floor must be {expected!r}; {_PIN_HINT}"
    )


def test_deno_min_version_mirrors_plugin() -> None:
    from yt_dlp_plugins.extractor.getpot_bgutil_script import BgUtilScriptDenoPTP

    plugin_min = BgUtilScriptDenoPTP._JSRT_MIN_VER  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert plugin_min == pot_provider._DENO_MIN_VERSION, (  # pyright: ignore[reportPrivateUsage]
        f"_DENO_MIN_VERSION drifted from the plugin's _JSRT_MIN_VER; {_PIN_HINT}"
    )


def test_pyproject_deno_floor_meets_plugin_min() -> None:
    matches = [m for dep in _project_dependencies() if (m := _DENO_DEP_RE.match(dep))]
    assert len(matches) == 1, "expected exactly one 'deno>=X.Y.Z' dependency in pyproject.toml"
    floor = tuple(int(g) for g in matches[0].groups())
    assert floor >= pot_provider._DENO_MIN_VERSION, (  # pyright: ignore[reportPrivateUsage]
        f"pyproject deno floor {floor} is below the provider minimum"
    )

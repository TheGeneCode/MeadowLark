"""Guards that private or per-developer files never become tracked again."""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GIT = shutil.which("git")


def _git(*args: str, check: bool) -> subprocess.CompletedProcess[str]:
    assert _GIT is not None, "git must be on PATH to run this test"
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, trusted git binary
        [_GIT, *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _tracked(pathspec: str) -> str:
    return _git("ls-files", "--", pathspec, check=True).stdout


def _is_ignored(path: str) -> bool:
    # check=False: returncode 1 means "not ignored", which is an answer, not an error.
    return _git("check-ignore", "-q", "--", path, check=False).returncode == 0


def test_docs_plans_not_tracked() -> None:
    assert _tracked("docs/plans") == "", (
        "docs/plans/ holds local planning notes (some with a Windows profile path) "
        "and was untracked on purpose -- do not re-add it."
    )


def test_top_level_plans_dir_ignored_and_not_tracked() -> None:
    assert _is_ignored("plans/example.md"), (
        "plans/ must stay gitignored -- scripts/release.ps1 runs `git add .`."
    )
    assert _tracked("plans") == ""


def test_vscode_settings_ignored_and_not_tracked() -> None:
    assert _is_ignored(".vscode/settings.json"), (
        ".vscode/settings.json carries personal prefs and AI-agent auto-approve "
        "rules that every cloner would inherit."
    )
    assert _tracked(".vscode/settings.json") == ""


@pytest.mark.parametrize("path", ["docs/screenshot.png", "docs/social-preview.png"])
def test_readme_images_still_tracked(path: str) -> None:
    assert _tracked(path) != "", f"{path} must stay re-included past the docs/* rule"


@pytest.mark.parametrize(
    "path",
    [
        "resources/cookies.txt",
        "resources/archive.txt",
        "resources/failed_downloads.json",
        "resources/pending_queue.json",
        ".env",
    ],
)
def test_cookies_and_user_state_ignored(path: str) -> None:
    # Check tracking first: check-ignore reports a tracked file as not ignored.
    assert _tracked(path) == "", f"{path} is per-user state and must not be committed"
    assert _is_ignored(path), f"{path} is per-user state and must stay gitignored"


def test_dependabot_watches_uv_lockfile() -> None:
    config = (_REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "uv"' in config

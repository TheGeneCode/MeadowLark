"""
Guards the project's ~1000-line module limit for shipped code.

meadowlark.pyw reached 2,538 lines before plans/split-meadowlark-main-window split it into
src/window_*.py mixins. This keeps any shipped module from drifting back past the limit.
"""

import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GIT = shutil.which("git")
_MAX_LINES = 1000
_NON_SHIPPED_DIRS = frozenset({"tests", "scripts", "vendor"})
# path (repo-relative, forward slashes) -> "YYYY-MM-DD reason". Starts empty; entries need a reason.
_ALLOWLIST: dict[str, str] = {}


def _shipped_python_files() -> list[Path]:
    assert _GIT is not None, "git must be on PATH to run this test"
    tracked = subprocess.run(  # noqa: S603 -- fixed argv, no shell, trusted git binary
        [_GIT, "ls-files", "-co", "--exclude-standard", "*.py", "*.pyw"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        _REPO_ROOT / rel
        for rel in tracked
        if rel.split("/", 1)[0] not in _NON_SHIPPED_DIRS and rel not in _ALLOWLIST
    ]


def test_shipped_modules_stay_under_line_limit() -> None:
    too_long = {
        path.relative_to(_REPO_ROOT).as_posix(): count
        for path in _shipped_python_files()
        if (count := len(path.read_text(encoding="utf-8").splitlines())) > _MAX_LINES
    }
    assert not too_long, f"modules over {_MAX_LINES} lines (split them): {too_long}"


def test_enumeration_includes_entry_point_and_src() -> None:
    names = {p.relative_to(_REPO_ROOT).as_posix() for p in _shipped_python_files()}
    assert "meadowlark.pyw" in names
    assert any(n.startswith("src/window_") for n in names)


def test_allowlist_entries_carry_a_reason() -> None:
    for path, reason in _ALLOWLIST.items():
        assert reason.strip(), f"{path} is allowlisted without a reason"

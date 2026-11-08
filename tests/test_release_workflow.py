"""
Guards the release pipeline's supply-chain properties.

.github/workflows/release.yml builds and publishes the installer users run, and
meadowlark.spec decides what goes inside it. Each property asserted here (SHA-pinned
actions, a locked dependency set, a hash-checked FFmpeg, a green-CI gate, no workflow
expressions inside `run:` scripts, a published checksum and provenance attestation,
an icons-only resource bundle) is easy to undo with an innocent-looking edit, and
nothing else would notice. The checks are text-based on purpose: PyYAML is not in
uv.lock, and the properties are all visible as plain lines.
"""

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_CI = _WORKFLOWS / "ci.yml"
_BUMP = _WORKFLOWS / "ffmpeg-bump.yml"
_PIN_FILE = _REPO_ROOT / ".github" / "ffmpeg-pin.yml"
_SPEC = _REPO_ROOT / "meadowlark.spec"

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", re.MULTILINE)
_SHA_PIN = re.compile(r"@[0-9a-f]{40}$")
_RUN = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")
_FFMPEG_SHA256 = re.compile(r"^\s*FFMPEG_SHA256:\s*([0-9a-f]{64})\s*$", re.MULTILINE)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _run_script_lines(text: str) -> list[str]:
    """Return every line belonging to a `run:` value (inline or block scalar)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _RUN.match(lines[i])
        if m is None:
            i += 1
            continue
        key_indent, value = len(m.group(1)), m.group(2).strip()
        if value and value[0] not in "|>":
            out.append(value)
            i += 1
            continue
        i += 1
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > key_indent):
            out.append(lines[i])
            i += 1
    return out


def _spec_resource_sources() -> list[str]:
    """First element of every `("resources...", "<dest>")` pair in meadowlark.spec."""
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"))
    sources: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            continue
        first, second = node.elts
        if (
            isinstance(first, ast.Constant)
            and isinstance(second, ast.Constant)
            and isinstance(first.value, str)
            and isinstance(second.value, str)
            and first.value.startswith("resources")
        ):
            sources.append(first.value)
    return sources


def test_every_workflow_action_is_sha_pinned() -> None:
    """A mutable tag lets an upstream retag run new code with this repo's tokens."""
    workflows = sorted(_WORKFLOWS.glob("*.yml"))
    assert workflows, "no workflow files found"
    offenders = [
        f"{path.name}: {ref}"
        for path in workflows
        for ref in _USES.findall(path.read_text(encoding="utf-8"))
        if not ref.startswith("./") and not _SHA_PIN.search(ref)
    ]
    assert not offenders, f"actions not pinned to a 40-hex commit SHA: {offenders}"


@pytest.mark.parametrize("workflow", [_RELEASE, _BUMP], ids=lambda path: path.name)
def test_run_scripts_never_interpolate_expressions(workflow: Path) -> None:
    """
    `${{ }}` inside `run:` is textual substitution into a shell script (injection).

    ci.yml is excluded on purpose: it interpolates matrix values into `run:` lines.
    """
    lines = _run_script_lines(workflow.read_text(encoding="utf-8"))
    assert lines, f"no run: scripts extracted from {workflow.name}; the parser went stale"
    offenders = [line for line in lines if "${{" in line]
    assert not offenders, f"workflow expression inside a run: script: {offenders}"


def test_run_script_parser_flags_inline_and_block_expressions() -> None:
    text = (
        "steps:\n"
        "  - run: echo ${{ github.ref_name }}\n"
        "  - run: |\n"
        "      echo start\n"
        "      echo ${{ x }}\n"
    )
    lines = _run_script_lines(text)
    flagged = [line for line in lines if "${{" in line]
    assert len(flagged) == 2
    assert any("github.ref_name" in line for line in flagged)
    assert any("${{ x }}" in line for line in flagged)


def test_run_script_parser_ignores_env_and_with_mappings() -> None:
    text = (
        "env:\n"
        "  TAG: ${{ github.ref_name }}\n"
        "- run: echo hi\n"
        "- uses: a/b@c\n"
        "  with:\n"
        "    x: ${{ y }}\n"
    )
    lines = _run_script_lines(text)
    assert lines == ["echo hi"]


def test_run_script_parser_stops_at_dedent() -> None:
    text = "- run: |\n    echo a\n- name: next\n  env:\n    X: ${{ z }}\n"
    assert [line.strip() for line in _run_script_lines(text)] == ["echo a"]


def test_release_uv_sync_is_locked() -> None:
    """Without --locked the build resolves fresh versions the test suite never saw."""
    syncs = [
        line for line in _RELEASE.read_text(encoding="utf-8").splitlines() if "uv sync" in line
    ]
    assert syncs, "release.yml has no `uv sync` step"
    assert all("--locked" in line for line in syncs), f"unlocked uv sync: {syncs}"


def test_release_ffmpeg_is_pinned_and_hash_checked() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    assert "/releases/download/latest/" not in text, "FFmpeg is fetched from the moving `latest`"
    assert _FFMPEG_SHA256.search(_PIN_FILE.read_text(encoding="utf-8")), (
        "FFMPEG_SHA256 is missing or not 64 lowercase hex in .github/ffmpeg-pin.yml"
    )
    assert "scripts/ffmpeg_pin.py fetch" in text, "FFmpeg download bypasses the verifying script"


def test_ffmpeg_pin_lives_outside_the_workflows_directory() -> None:
    """
    The bump bot pushes the pin with GITHUB_TOKEN, which may not modify workflow files.

    So the pin must sit in a file outside .github/workflows/, and release.yml must not
    carry its own copy (two copies would drift).
    """
    assert _PIN_FILE.is_file()
    assert _WORKFLOWS not in _PIN_FILE.parents
    release_text = _RELEASE.read_text(encoding="utf-8")
    assert not re.search(r"^\s*FFMPEG_(TAG|ASSET|SHA256):", release_text, re.MULTILINE)


def test_release_build_is_gated_on_ci() -> None:
    release = _RELEASE.read_text(encoding="utf-8")
    ci = _CI.read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "needs: ci" in release
    assert "workflow_call:" in ci, "ci.yml is not callable, so release.yml's gate cannot run"


def test_release_uv_run_never_resyncs() -> None:
    """A bare `uv run` re-syncs and can re-lock, undoing the `--locked` install above it."""
    scripts = _run_script_lines(_RELEASE.read_text(encoding="utf-8"))
    runs = [line for line in scripts if "uv run" in line]
    assert runs, "release.yml has no `uv run` step; the guard went stale"
    assert all("--no-sync" in line for line in runs), f"uv run without --no-sync: {runs}"


def test_release_checkout_fetches_full_history() -> None:
    """With a shallow clone `git describe` fails and the notes silently list one commit."""
    assert "fetch-depth: 0" in _RELEASE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("ref", "pinned"),
    [
        ("actions/checkout@" + "a" * 40, True),
        ("actions/checkout@v4", False),
        ("actions/checkout@main", False),
        ("actions/checkout@" + "a" * 39, False),
        ("actions/checkout@" + "a" * 41, False),
    ],
    ids=["full-sha", "tag", "branch", "short-sha", "overlong-sha"],
)
def test_sha_pin_pattern_accepts_only_full_lowercase_commit_sha(ref: str, pinned: bool) -> None:
    """Proves the pin guard's failing path fires (a loosened `{40}` would pass tags)."""
    (found,) = _USES.findall(f"      - uses: {ref} # comment\n")
    assert found == ref
    assert bool(_SHA_PIN.search(found)) is pinned


def test_release_job_disables_uv_cache() -> None:
    """A cache restored from another run is input the published artifact did not build from."""
    assert "enable-cache: false" in _RELEASE.read_text(encoding="utf-8")


def test_release_publishes_checksum_and_attestation() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    assert "actions/attest-build-provenance@" in text
    assert ".exe.sha256" in text
    assert "body_path: release_notes.md" in text


def test_ffmpeg_bump_pr_is_gated_on_smoke() -> None:
    """A bot PR may only open after the real-FFmpeg smoke tests and the full suite passed."""
    text = _BUMP.read_text(encoding="utf-8")
    assert "needs: [check, smoke]" in text
    assert "-m ffmpeg_smoke" in text
    assert '-m "not ffmpeg_smoke"' in text


def test_ffmpeg_bump_write_scopes_are_job_local() -> None:
    """Write tokens belong to the one job that needs them, never to the whole workflow."""
    text = _BUMP.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read$", text, re.MULTILINE)
    assert text.count("contents: write") == 1
    assert text.count("issues: write") == 1


def test_spec_bundles_only_icons_from_resources() -> None:
    """Everything else under resources/ is per-user state (cookies, archive, queues)."""
    assert _spec_resource_sources() == ["resources/icons"]


@pytest.mark.parametrize(
    "bad_source",
    ['("resources", "resources")', '("resources/cookies.txt", "resources")'],
    ids=["whole-folder", "single-private-file"],
)
def test_spec_helper_detects_broader_resource_bundles(
    bad_source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the spec guard's failing path fires, not just its passing one."""
    fake = tmp_path / "x.spec"
    fake.write_text(f"datas = [{bad_source}, ('resources/icons', 'resources/icons')]\n")
    monkeypatch.setattr(f"{__name__}._SPEC", fake)
    assert _spec_resource_sources() != ["resources/icons"]

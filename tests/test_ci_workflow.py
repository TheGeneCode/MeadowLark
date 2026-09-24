"""
Guards the link between pyproject.toml's declared Python floor and its copies.

.github/workflows/ci.yml documents (in its own comments) that the `test` job's
matrix "must cover the full `requires-python` range in pyproject.toml" -- but a
comment is not enforcement. This module reads the floor out of `requires-python`
(rather than repeating the literal "3.12", so the two cannot silently drift) and
proves the `test` job's matrix names it. Mirrors the equivalent guard in the
sibling genekit repo (`python/tests/test_packaging_metadata.py`,
`test_ci_matrix_actually_exercises_the_declared_floor`).

A second group of tests guards the other places the 2026-09 `>=3.12` -> `>=3.11`
drop touched: `uv.lock`'s own `requires-python` header, the `genekit` git-tag
pin against `uv.lock`'s resolved version, `meadowlark.pyw`'s docstring (already
stale once before -- it said "Python 3.10+" while `pyproject.toml` said
`>=3.12`), and the `tomli` dependency that `uv lock` newly restored via
`coverage[toml]`'s `python_full_version <= '3.11'` marker now that the marker
is satisfiable somewhere in the supported range.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CI_PYTHON_VERSION_LIST = re.compile(r"python-version:\s*\[([^\]]*)\]")
_CI_PYTHON_VERSION_SCALAR = re.compile(r"""python-version:\s*["'](\d+\.\d+)["']""")
_CI_QUOTED_VERSION = re.compile(r"""["'](\d+\.\d+)["']""")


def _extract_versions(text: str) -> set[str]:
    versions = set(_CI_PYTHON_VERSION_SCALAR.findall(text))
    for listed in _CI_PYTHON_VERSION_LIST.findall(text):
        versions.update(_CI_QUOTED_VERSION.findall(listed))
    return versions


def _requires_python_floor() -> str:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        requires_python = tomllib.load(handle)["project"]["requires-python"]
    floor = re.fullmatch(r">=(\d+\.\d+)", requires_python)
    assert floor is not None, f"requires-python is not a bare '>=X.Y' floor: {requires_python!r}"
    return floor.group(1)


def test_ci_matrix_actually_exercises_the_declared_floor() -> None:
    """
    A declared floor that no CI leg runs is a claim, not a fact.

    Written to fail loudly rather than emptily: a workflow reformat that broke
    the extraction would otherwise leave a green no-op, so a non-empty result
    and a plausible version count are asserted before the membership check.
    The slice matters too -- the `lint` job carries its own unrelated
    `UV_PYTHON` pin, and searching the whole file would risk that (or a future
    job) masking a `test` job matrix that had genuinely dropped the floor.
    """
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    start = re.search(r"^  test:$", workflow, re.MULTILINE)
    assert start is not None, "no '  test:' job key found in ci.yml; the slice boundary moved"
    end = re.search(r"^  lint:$", workflow, re.MULTILINE)
    assert end is not None, "no '  lint:' job key found in ci.yml; the slice boundary moved"
    assert end.start() > start.start(), "ci.yml job order changed; the test-job slice is inverted"
    test_job = workflow[start.start() : end.start()]

    versions = _extract_versions(test_job)
    assert versions, "no python-version values extracted from the test job; the regex went stale"
    assert len(versions) >= 3, (
        f"test matrix names too few interpreters to be the real one: {sorted(versions)}"
    )

    floor = _requires_python_floor()
    assert floor in versions, (
        f"declared floor {floor} is not in the CI test matrix: {sorted(versions)}"
    )


def test_ci_version_list_regex_does_not_cross_a_closing_bracket() -> None:
    r"""
    `[^\]]*` must stop at the first `]`.

    So a second bracketed matrix key on the same job (e.g. an `os: [...]`
    axis) can never bleed into the captured version list.
    """
    text = 'python-version: ["3.12", "3.13"]\nos: [windows-latest, ubuntu-latest]\n'
    assert _CI_PYTHON_VERSION_LIST.findall(text) == ['"3.12", "3.13"']
    assert _extract_versions(text) == {"3.12", "3.13"}


def test_ci_version_regexes_ignore_matrix_interpolation_syntax() -> None:
    """
    Matrix interpolation syntax must not be mistaken for a literal version pin.

    `python-version: ${{ matrix.python-version }}` (the job-level env wiring
    in the real workflow) names the matrix key, not a literal version -- if
    it were, the `test` job's own env line would trivially "satisfy" the
    extraction regardless of the matrix's actual contents.
    """
    text = "name: py${{ matrix.python-version }}\nUV_PYTHON: ${{ matrix.python-version }}\n"
    assert _extract_versions(text) == set()


def test_ci_matrix_slice_excludes_the_lint_jobs_unrelated_pin() -> None:
    """
    Regression guard for why the real test slices to the `test:` job first.

    The `lint` job pins its own interpreter via `UV_PYTHON` for unrelated
    reasons (ruff reads `target-version` from `requires-python`, not the
    running interpreter). This synthetic workflow drops "3.12" from the
    `test` job's own matrix so the slice's effect is provable independent of
    the real ci.yml's current leg shape.
    """
    synthetic = (
        "  test:\n"
        "    strategy:\n"
        "      matrix:\n"
        '        python-version: ["3.13", "3.14"]\n'
        "  lint:\n"
        '    UV_PYTHON: "3.12"\n'
    )
    start = re.search(r"^  test:$", synthetic, re.MULTILINE)
    end = re.search(r"^  lint:$", synthetic, re.MULTILINE)
    assert start is not None
    assert end is not None
    sliced = synthetic[start.start() : end.start()]

    assert _extract_versions(sliced) == {"3.13", "3.14"}
    assert "3.12" not in _extract_versions(sliced)


def test_pyproject_requires_python_floor_matches_helper() -> None:
    """
    Pin `_requires_python_floor`'s parse against the real pyproject.toml.

    A change to the `requires-python` shape (e.g. a pinned upper bound like
    ">=3.12,<3.15") is caught here with a clear message rather than as an
    opaque `AssertionError` inside the main matrix test.
    """
    assert _requires_python_floor() == "3.11"


# ---------------------------------------------------------------------------
# Coverage for the other places the 2026-09 `>=3.12` -> `>=3.11` drop touched:
# uv.lock's own header, the genekit git-tag pin, the tomli extra that `uv
# lock` newly restored, and meadowlark.pyw's docstring (which drifted silently
# once already -- it still said "Python 3.10+" under the old `>=3.12` floor).
# ---------------------------------------------------------------------------


def _uv_lock_packages() -> list[dict]:
    with (_REPO_ROOT / "uv.lock").open("rb") as handle:
        return tomllib.load(handle)["package"]


def _uv_lock_package(name: str) -> dict:
    matches = [pkg for pkg in _uv_lock_packages() if pkg["name"] == name]
    assert len(matches) == 1, (
        f"expected exactly one uv.lock [[package]] named {name!r}, found {len(matches)}"
    )
    return matches[0]


def _genekit_tag_version(tag: str) -> str:
    """Extract the `X.Y.Z` version genekit's `py-vX.Y.Z` release tag implies."""
    match = re.fullmatch(r"py-v(\d+\.\d+\.\d+)", tag)
    assert match is not None, f"genekit tag {tag!r} is not the expected 'py-vX.Y.Z' shape"
    return match.group(1)


def _genekit_pin_mismatch(tag: str, locked_version: str) -> str | None:
    """None if `tag` and `locked_version` agree; else a description of the drift."""
    expected = _genekit_tag_version(tag)
    if expected == locked_version:
        return None
    return f"tag {tag!r} implies version {expected!r} but uv.lock has {locked_version!r}"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("py-v0.2.2", "0.2.2"),
        ("py-v0.1.0", "0.1.0"),
        ("py-v10.20.30", "10.20.30"),
        ("py-v0.0.0", "0.0.0"),
    ],
    ids=["nominal", "prior-pin", "multi-digit", "all-zero"],
)
def test_genekit_tag_version_boundary_values(tag: str, expected: str) -> None:
    assert _genekit_tag_version(tag) == expected


@pytest.mark.parametrize(
    "bad_tag",
    ["v0.2.2", "py-0.2.2", "py-v0.2", "py-v0.2.2.1", "py-vX.Y.Z", "", "py-v0.2.2 "],
    ids=[
        "missing-py-prefix",
        "missing-v",
        "two-component",
        "four-component",
        "non-numeric",
        "empty",
        "trailing-space",
    ],
)
def test_genekit_tag_version_rejects_malformed_tags(bad_tag: str) -> None:
    with pytest.raises(AssertionError, match="not the expected"):
        _genekit_tag_version(bad_tag)


def test_genekit_pin_mismatch_detects_real_drift() -> None:
    """Proves `_genekit_pin_mismatch`'s failing path fires, not just its passing one."""
    assert _genekit_pin_mismatch("py-v0.2.2", "0.2.2") is None
    drift = _genekit_pin_mismatch("py-v0.2.2", "0.2.1")
    assert drift is not None
    assert "0.2.2" in drift
    assert "0.2.1" in drift


def test_genekit_lock_version_matches_pyproject_tag() -> None:
    """
    pyproject.toml pins genekit by git tag; uv.lock records the resolved version.

    `uv sync --locked` already enforces these cannot diverge in CI (a full
    dependency resolution), but this is the same guarantee at pytest speed
    with a message that names the actual drift.
    """
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    tag = pyproject["tool"]["uv"]["sources"]["genekit"]["tag"]
    mismatch = _genekit_pin_mismatch(tag, _uv_lock_package("genekit")["version"])
    assert mismatch is None, f"pyproject.toml/uv.lock genekit pin drifted: {mismatch}"


def test_uv_lock_requires_python_matches_pyproject_floor() -> None:
    """
    uv.lock's own top-level `requires-python` header must match the floor too.

    That header is separate from the per-package `python_full_version`
    markers below it. `uv lock --check` (run manually per the implementation
    handoff) already enforces this; pin it here too so a local `pytest` run
    catches a lock/pyproject drift without needing a network-touching
    `uv lock --check` first.
    """
    with (_REPO_ROOT / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    assert lock["requires-python"] == f">={_requires_python_floor()}"


def _coverage_toml_extra_tomli_marker() -> str:
    toml_extra = _uv_lock_package("coverage")["optional-dependencies"]["toml"]
    tomli_entries = [dep for dep in toml_extra if dep["name"] == "tomli"]
    assert len(tomli_entries) == 1, (
        "expected exactly one 'tomli' entry in coverage's [package.optional-dependencies] "
        f"'toml' extra, found {len(tomli_entries)} -- coverage's own extras reshaped."
    )
    marker = tomli_entries[0].get("marker")
    assert marker is not None, (
        "coverage's toml extra now pulls tomli unconditionally (no marker) -- this is the "
        "exact 'tomli appeared newly in uv.lock' change the 3.11-floor handoff flagged as "
        "needing a second look; confirm it is still meant to be marker-gated."
    )
    return marker


def test_coverage_toml_extra_still_declares_tomli_with_a_marker() -> None:
    assert _coverage_toml_extra_tomli_marker() == "python_full_version <= '3.11'"


@pytest.mark.parametrize(
    ("python_full_version", "tomli_installs"),
    [
        ("3.10.9", True),  # below this repo's floor, but the marker's own boundary
        ("3.11.0", True),  # exact floor patch: the one case where tomli DOES install
        ("3.11.1", False),  # just above 3.11.0 -- PEP 440 "3.11" == "3.11.0"
        ("3.11.13", False),  # the real interpreter version this repo's CI installs
        ("3.11.99", False),
        ("3.12.0", False),
    ],
    ids=[
        "below-floor",
        "at-floor-exact-patch-zero",
        "just-above-floor-patch",
        "nominal-3.11-patch",
        "just-below-3.12",
        "next-minor",
    ],
)
def test_coverage_toml_extra_tomli_marker_boundary(
    python_full_version: str,
    tomli_installs: bool,
) -> None:
    """
    Pin the exact boundary the implementation handoff reasoned about by hand.

    `python_full_version <= '3.11'` only matches the literal `3.11.0` patch
    (PEP 440 treats `3.11` as `3.11.0`), so real 3.11.x runners -- which are
    never bare `.0` in practice -- never actually install tomli. This proves
    that reasoning against the real marker string in uv.lock rather than
    trusting the handoff's prose, so a future `coverage`/`pytest-cov` bump
    that reshapes the marker (the handoff's own "worth a second look" note)
    fails this test instead of silently changing behavior.
    """
    marker = Marker(_coverage_toml_extra_tomli_marker())
    assert marker.evaluate({"python_full_version": python_full_version}) is tomli_installs


def test_meadowlark_pyw_docstring_floor_matches_pyproject() -> None:
    """
    Regression guard: this exact drift already happened once.

    Before this 3.11-floor change, `meadowlark.pyw`'s module docstring said
    "Python 3.10+" while `pyproject.toml` already said `>=3.12` -- stale
    prose nobody noticed because nothing checked it. Parses the floor back
    out of `requires-python` so the two cannot silently diverge again.
    """
    docstring = (_REPO_ROOT / "meadowlark.pyw").read_text(encoding="utf-8")
    match = re.search(r"^- Python (\d+\.\d+)\+$", docstring, re.MULTILINE)
    assert match is not None, "meadowlark.pyw docstring no longer has a '- Python X.Y+' line"
    assert match.group(1) == _requires_python_floor()


def test_workflows_carry_no_private_genekit_auth_step() -> None:
    """
    Guard: no workflow carries the dead private-genekit auth step (the repo is public).

    A `GENEKIT_TOKEN` PAT or a global github.com `insteadOf` rewrite has no purpose.

    The rewrite is also a trap: it applies to *every* github.com URL, so once the
    now-unused secret is deleted it would inject an empty token into every clone.
    """
    workflows = sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflow files found"
    offenders = [
        f"{path.name}: {needle}"
        for path in workflows
        for needle in ("GENEKIT_TOKEN", "insteadOf")
        if needle in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"dead private-genekit auth crept back into CI: {offenders}"

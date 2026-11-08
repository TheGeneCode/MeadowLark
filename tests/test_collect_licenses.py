"""
Tests for scripts/collect_licenses.py and the third-party notices it feeds.

Coverage map
============
project_requirements / python_closure / python_component
    - real env closure            -> seeded from pyproject.toml (virtual root, no dist
                                     metadata); transitive runtime deps in, root/dev tools out
    - pyproject parsing           -> name + dependencies read; no [project] table -> exit 1
    - inapplicable markers        -> extras-only / wrong-platform requirements never resolved
    - PEP 639 license dir         -> license files found and existing
    - bgutil override             -> GPL-3.0 + the vendored LICENSE file
npm_components
    - scoped / unscoped keys, non-package store entries, legacy license shapes, missing manifest
ffmpeg_component
    - source URL from the asset's -g<sha>- segment, fallback without it
validate / write_bundle
    - only fully undeclared components fail; bundle layout + sorted inventory
main
    - exit 2 without the Deno store, exit 0 end to end on a fake store, exit 1 for FFmpeg
      without a license file
THIRD_PARTY_NOTICES.md / vendored LICENSE
    - every direct dependency has a row; the vendored GPL text is present
"""

from __future__ import annotations

import email.message
import importlib.metadata
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import types
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "collect_licenses.py"
_ASSET = "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip"


def _load_collector() -> types.ModuleType:
    """Import the standalone script as a module (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("collect_licenses", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves string annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def collector() -> Iterator[types.ModuleType]:
    mod = _load_collector()
    yield mod
    sys.modules.pop("collect_licenses", None)


def _write_npm_package(
    root: Path, key: str, name: str, manifest: dict[str, object], *, license_text: str | None
) -> Path:
    """Create ``root/.deno/<key>/node_modules/<name>/package.json`` (and optionally a LICENSE)."""
    package_dir = root / ".deno" / key / "node_modules" / name
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    if license_text is not None:
        (package_dir / "LICENSE").write_text(license_text, encoding="utf-8")
    return package_dir


# --- python ------------------------------------------------------------------------------------


def test_python_closure_includes_transitive_runtime_deps(collector: types.ModuleType) -> None:
    # Seeded from pyproject.toml: the project itself is a uv *virtual* root with no installed
    # dist metadata, so this must pass on a clean checkout (no stray meadowlark.egg-info).
    root = collector.project_requirements(_REPO_ROOT / "pyproject.toml")
    names = {collector._normalize(d.name) for d in collector.python_closure(*root)}
    assert {"requests", "urllib3", "certifi", "pyqt6-qt6"} <= names
    assert names.isdisjoint({"meadowlark", "pytest", "ruff"})


def test_python_closure_skips_inapplicable_markers(
    collector: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked_up: list[str] = []

    def _fake_distribution(name: str) -> object:
        looked_up.append(name)
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(collector.importlib.metadata, "distribution", _fake_distribution)
    root_requires = ["x ; extra == 'cli'", "y ; sys_platform == 'nonexistent'"]
    assert collector.python_closure("fakeroot", root_requires) == []
    assert looked_up == []


def test_project_requirements_reads_name_and_dependencies(
    tmp_path: Path, collector: types.ModuleType
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2", "x ; extra == \'cli\'"]\n',
        encoding="utf-8",
    )
    assert collector.project_requirements(pyproject) == (
        "demo",
        ["requests>=2", "x ; extra == 'cli'"],
    )


def test_project_requirements_without_dependencies_key_yields_empty_list(
    tmp_path: Path, collector: types.ModuleType
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    assert collector.project_requirements(pyproject) == ("demo", [])


def test_python_closure_cycle_back_to_root_is_not_collected_or_looked_up(
    collector: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeDist:
        def __init__(self, name: str, requires: list[str] | None) -> None:
            self.name = name
            self.requires = requires

    dists = {
        "dep-a": _FakeDist("dep-a", ["Demo_Root>=1", "dep-b"]),
        "dep-b": _FakeDist("dep-b", None),
    }
    looked_up: list[str] = []

    def _fake_distribution(name: str) -> _FakeDist:
        looked_up.append(name)
        return dists[name]

    monkeypatch.setattr(collector.importlib.metadata, "distribution", _fake_distribution)
    closure = collector.python_closure("demo-root", ["demo.root", "dep-a"])
    assert [d.name for d in closure] == ["dep-a", "dep-b"]
    assert looked_up == ["dep-a", "dep-b"]


@pytest.mark.parametrize("content", [None, "[project\nname = "])
def test_main_exits_1_when_pyproject_is_missing_or_malformed(
    tmp_path: Path,
    collector: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
    content: str | None,
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)
    pyproject = tmp_path / "pyproject.toml"
    if content is not None:
        pyproject.write_text(content, encoding="utf-8")
    code = collector.main(
        ["--out", str(tmp_path / "out"), "--npm-root", str(npm_root), "--pyproject", str(pyproject)]
    )
    assert code == 1
    assert capsys.readouterr().err.startswith("error:")
    assert not (tmp_path / "out").exists()


def test_main_exits_1_when_pyproject_has_no_project_table(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.x]\n", encoding="utf-8")
    code = collector.main(
        ["--out", str(tmp_path / "out"), "--npm-root", str(npm_root), "--pyproject", str(pyproject)]
    )
    assert code == 1
    assert "[project] name" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_python_component_reads_pep639_license_dir(collector: types.ModuleType) -> None:
    component = collector.python_component(importlib.metadata.distribution("requests"))
    assert component.license_files
    assert all(path.is_file() for path in component.license_files)
    assert component.license


def test_override_supplies_bgutil_license(collector: types.ModuleType) -> None:
    component = collector.python_component(
        importlib.metadata.distribution("bgutil-ytdlp-pot-provider")
    )
    assert component.license == "GPL-3.0"
    assert component.license_files == (
        (_REPO_ROOT / "vendor" / "bgutil-pot-provider" / "LICENSE").resolve(),
    )


class _MetaDist:
    """Minimal Distribution stand-in carrying only ``metadata``."""

    def __init__(self, headers: list[tuple[str, str]]) -> None:
        self.metadata = email.message.Message()
        for key, value in headers:
            self.metadata[key] = value


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param(
            [("License-Expression", "MIT"), ("License", "BSD"), ("Classifier", "License :: X")],
            "MIT",
            id="expression-wins",
        ),
        pytest.param(
            [("License", "Apache-2.0\nfull text...")], "Apache-2.0", id="legacy-first-line"
        ),
        pytest.param(
            [("License", "UNKNOWN"), ("Classifier", "License :: OSI Approved :: BSD License")],
            "BSD License",
            id="unknown-falls-to-classifier",
        ),
        pytest.param(
            [
                ("Classifier", "Programming Language :: Python :: 3"),
                ("Classifier", "License :: OSI Approved :: MIT License"),
                ("Classifier", "License :: OSI Approved :: Apache Software License"),
            ],
            "MIT License, Apache Software License",
            id="classifiers-joined-non-license-ignored",
        ),
        pytest.param([("License", "   ")], None, id="blank-is-undeclared"),
    ],
)
def test_python_license_precedence(
    collector: types.ModuleType, headers: list[tuple[str, str]], expected: str | None
) -> None:
    assert collector._python_license(_MetaDist(headers)) == expected


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        pytest.param(("x-1.dist-info", "licenses", "LICENSE"), True, id="pep639-dir"),
        pytest.param(("x-1.dist-info", "licenses", "sub", "AUTHORS"), True, id="pep639-nested"),
        pytest.param(("x-1.dist-info", "COPYING.txt"), True, id="legacy-root-name"),
        pytest.param(("x-1.dist-info", "METADATA"), False, id="other-metadata-file"),
        pytest.param(("x", "LICENSE"), False, id="package-source-license-not-in-dist-info"),
        pytest.param(("x-1.dist-info",), False, id="dist-info-itself"),
    ],
)
def test_is_python_license_file(
    collector: types.ModuleType, parts: tuple[str, ...], expected: bool
) -> None:
    assert collector._is_python_license_file(parts) is expected


# --- npm ---------------------------------------------------------------------------------------


def test_npm_key_parsing_scoped_and_unscoped(tmp_path: Path, collector: types.ModuleType) -> None:
    _write_npm_package(
        tmp_path,
        "@acemir+cssom@0.9.31",
        "@acemir/cssom",
        {"license": "MIT", "repository": {"url": "git+https://example.test/cssom.git"}},
        license_text="cssom license",
    )
    _write_npm_package(
        tmp_path,
        "string_decoder@1.3.0",
        "string_decoder",
        {"license": "MIT", "repository": "https://example.test/sd"},
        license_text=None,
    )
    found = {(c.name, c.version): c for c in collector.npm_components(tmp_path)}
    assert set(found) == {("@acemir/cssom", "0.9.31"), ("string_decoder", "1.3.0")}
    assert found["@acemir/cssom", "0.9.31"].source_url == "git+https://example.test/cssom.git"
    assert found["string_decoder", "1.3.0"].source_url == "https://example.test/sd"
    assert len(found["@acemir/cssom", "0.9.31"].license_files) == 1
    assert found["string_decoder", "1.3.0"].license_files == ()


def test_npm_skips_non_package_entries(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".deno" / "node_modules").mkdir(parents=True)
    (tmp_path / ".deno" / "stray.txt").write_text("not a package", encoding="utf-8")
    assert collector.npm_components(tmp_path) == []
    assert capsys.readouterr().err == ""


def test_npm_legacy_license_shapes(tmp_path: Path, collector: types.ModuleType) -> None:
    _write_npm_package(
        tmp_path, "old-a@1.0.0", "old-a", {"license": {"type": "MIT"}}, license_text=None
    )
    _write_npm_package(
        tmp_path,
        "old-b@2.0.0",
        "old-b",
        {"licenses": [{"type": "MIT"}, {"type": "Apache-2.0"}]},
        license_text=None,
    )
    licenses = {c.name: c.license for c in collector.npm_components(tmp_path)}
    assert licenses == {"old-a": "MIT", "old-b": "MIT OR Apache-2.0"}


def test_npm_missing_package_json_raises(tmp_path: Path, collector: types.ModuleType) -> None:
    (tmp_path / ".deno" / "ghost@1.0.0" / "node_modules" / "ghost").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match=r"package\.json"):
        collector.npm_components(tmp_path)


def _write_left_pad(npm_root: Path) -> None:
    _write_npm_package(
        npm_root, "left-pad@1.0.0", "left-pad", {"license": "MIT"}, license_text="lp"
    )


# --- ffmpeg ------------------------------------------------------------------------------------


def test_ffmpeg_source_url_from_asset_name(tmp_path: Path, collector: types.ModuleType) -> None:
    (tmp_path / "LICENSE.txt").write_text("GPL", encoding="utf-8")
    component = collector.ffmpeg_component(tmp_path, _ASSET)
    assert component.source_url is not None
    assert component.source_url.endswith("/commit/7adfbe40dc")
    assert [p.name for p in component.license_files] == ["LICENSE.txt"]


def test_ffmpeg_source_url_fallback(tmp_path: Path, collector: types.ModuleType) -> None:
    assert (
        collector.ffmpeg_component(tmp_path, None).source_url == "https://ffmpeg.org/download.html"
    )


# --- validate / write_bundle -------------------------------------------------------------------


def _component(
    collector: types.ModuleType,
    name: str,
    *,
    ecosystem: str = "npm",
    license_id: str | None = "MIT",
    files: tuple[Path, ...] = (),
) -> object:
    return collector.Component(
        ecosystem=ecosystem,
        name=name,
        version="1.0.0",
        license=license_id,
        source_url=None,
        license_files=files,
    )


def test_validate_flags_only_fully_undeclared(tmp_path: Path, collector: types.ModuleType) -> None:
    some_file = tmp_path / "LICENSE"
    some_file.write_text("text", encoding="utf-8")
    components = [
        _component(collector, "none", license_id=None),
        _component(collector, "declared", license_id="MIT"),
        _component(collector, "file-only", license_id=None, files=(some_file,)),
    ]
    errors = collector.validate(components)
    assert len(errors) == 1
    assert "none" in errors[0]


def test_write_bundle_layout_and_inventory(
    tmp_path: Path, collector: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    (fake_repo / "LICENSE").write_text("GPL text", encoding="utf-8")
    (fake_repo / "THIRD_PARTY_NOTICES.md").write_text("# notices", encoding="utf-8")
    monkeypatch.setattr(collector, "_REPO_ROOT", fake_repo)
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "no-python-here"))

    src = tmp_path / "src"
    src.mkdir()
    cssom_license = src / "LICENSE"
    cssom_license.write_text("cssom", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("old", encoding="utf-8")

    collector.write_bundle(
        [
            _component(collector, "zeta"),
            collector.Component("npm", "@acemir/cssom", "0.9.31", "MIT", "u", (cssom_license,)),
        ],
        out,
    )

    assert not (out / "stale.txt").exists()
    assert (out / "npm" / "_acemir_cssom-0.9.31" / "LICENSE").read_text(encoding="utf-8") == "cssom"
    assert (out / "MeadowLark-LICENSE.txt").read_text(encoding="utf-8") == "GPL text"
    assert (out / "THIRD_PARTY_NOTICES.md").is_file()
    lines = (out / "INVENTORY.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == ["ecosystem", "license"][:1] + lines[0].split("\t")[1:]
    assert lines[0].startswith("ecosystem\tname\tversion\tlicense")
    assert [line.split("\t")[1] for line in lines[1:]] == ["@acemir/cssom", "zeta"]
    assert "(no license file shipped by package)" in lines[2]


def test_write_bundle_disambiguates_same_named_license_files(
    tmp_path: Path, collector: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "_REPO_ROOT", tmp_path / "no-repo")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "no-python-here"))
    first = tmp_path / "a" / "LICENSE"
    second = tmp_path / "b" / "license"
    third = tmp_path / "c" / "LICENSE"
    for path, text in ((first, "one"), (second, "two"), (third, "three")):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")

    out = tmp_path / "out"
    collector.write_bundle([_component(collector, "dup", files=(first, second, third))], out)

    copied = {p.name: p.read_text(encoding="utf-8") for p in (out / "npm" / "dup-1.0.0").iterdir()}
    assert sorted(copied.values()) == ["one", "three", "two"]
    assert len(copied) == 3


# --- main --------------------------------------------------------------------------------------


def test_main_exits_2_without_deno_store(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    code = collector.main(["--out", str(tmp_path / "out"), "--npm-root", str(tmp_path)])
    assert code == 2
    assert ".deno" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_main_end_to_end_with_fake_store_and_ffmpeg(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)
    ffmpeg_dir = tmp_path / "ffmpeg_licenses"
    ffmpeg_dir.mkdir()
    (ffmpeg_dir / "LICENSE.txt").write_text("GPL", encoding="utf-8")
    out = tmp_path / "out"

    code = collector.main(
        [
            "--out",
            str(out),
            "--npm-root",
            str(npm_root),
            "--ffmpeg-root",
            str(ffmpeg_dir),
            "--ffmpeg-asset",
            _ASSET,
        ]
    )

    assert code == 0
    assert capsys.readouterr().out.startswith("wrote ")
    assert (out / "npm" / "left-pad-1.0.0" / "LICENSE").is_file()
    assert (out / "binary" / "ffmpeg-n9.0.2-39-g7adfbe40dc" / "LICENSE.txt").is_file()
    inventory = (out / "INVENTORY.txt").read_text(encoding="utf-8")
    assert "UNDECLARED" not in inventory
    assert "\trequests\t" in inventory


def test_main_exits_1_when_an_npm_package_is_fully_undeclared(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_npm_package(npm_root, "mystery@0.1.0", "mystery", {}, license_text=None)
    code = collector.main(["--out", str(tmp_path / "out"), "--npm-root", str(npm_root)])
    assert code == 1
    assert "mystery" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_main_exits_1_when_a_required_distribution_is_not_installed(
    tmp_path: Path,
    collector: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)

    def _missing(*_args: object) -> list[object]:
        raise importlib.metadata.PackageNotFoundError("ghost-dep")

    monkeypatch.setattr(collector, "python_closure", _missing)
    code = collector.main(["--out", str(tmp_path / "out"), "--npm-root", str(npm_root)])
    assert code == 1
    assert "ghost-dep" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_main_exits_2_when_ffmpeg_root_is_not_a_directory(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)
    code = collector.main(
        [
            "--out",
            str(tmp_path / "out"),
            "--npm-root",
            str(npm_root),
            "--ffmpeg-root",
            str(tmp_path / "missing"),
        ]
    )
    assert code == 2
    assert "--ffmpeg-root" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_main_fails_when_ffmpeg_ships_no_license_file(
    tmp_path: Path, collector: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    npm_root = tmp_path / "node_modules"
    _write_left_pad(npm_root)
    empty = tmp_path / "empty"
    empty.mkdir()
    code = collector.main(
        ["--out", str(tmp_path / "out"), "--npm-root", str(npm_root), "--ffmpeg-root", str(empty)]
    )
    assert code == 1
    assert "FFmpeg" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --- notices / vendored license ----------------------------------------------------------------


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_third_party_notices_lists_every_direct_dependency() -> None:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        declared = {
            _normalize(re.split(r"[\[<>=!~;\s]", req, maxsplit=1)[0])
            for req in tomllib.load(handle)["project"]["dependencies"]
        }
    notices = (_REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    rows = [line for line in notices.splitlines() if line.startswith("|")]
    listed = {_normalize(m[1]) for line in rows if (m := re.match(r"\|\s*`([^`]+)`", line))}
    assert declared <= listed
    assert any("FFmpeg" in row for row in rows)
    assert any("Python runtime" in row for row in rows)


def test_vendored_bgutil_license_present() -> None:
    license_file = _REPO_ROOT / "vendor" / "bgutil-pot-provider" / "LICENSE"
    assert "GNU GENERAL PUBLIC LICENSE" in license_file.read_text(encoding="utf-8").splitlines()[0]

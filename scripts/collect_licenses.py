"""
Collect the license texts of every third-party component the installer bundles.

The release build runs this after PyInstaller and the FFmpeg download, so the frozen
app ships a ``licenses`` folder generated from the exact packages it contains:

    uv run python scripts/collect_licenses.py --out dist/MeadowLark/licenses \
        --ffmpeg-root ffmpeg_tmp --ffmpeg-asset ffmpeg-...-win64-gpl-....zip

Sources walked:
    python  the runtime dependency closure of ``--pyproject``'s ``[project] dependencies``
            (resolved via ``importlib.metadata``), following only requirements whose
            markers apply with no extras selected
    npm     every package in the Deno npm store (``<npm-root>/.deno/<name>@<version>``)
            that ``deno install`` produced for the vendored bgutil PO-token server
    binary  the FFmpeg build (``LICENSE*`` under ``--ffmpeg-root``) and the CPython
            runtime's own ``LICENSE.txt``

Each component's license files land in ``<out>/<ecosystem>/<safe-name>-<version>/``,
next to ``INVENTORY.txt`` (one tab-separated row per component), MeadowLark's own
``LICENSE`` and ``THIRD_PARTY_NOTICES.md``.

Fail-loud rule: a component with neither a declared license nor a license file fails
the run, unless ``_OVERRIDES`` names it. A declared license without a file is only a
warning (and is marked in the inventory). FFmpeg without any ``LICENSE*`` file is an
error: its GPL text must ship.

Exit codes:
    0  bundle written
    1  validation failed (undeclared components, FFmpeg license missing, or a
       dependency/package manifest could not be read)
    2  ``<npm-root>/.deno`` or ``--ffmpeg-root`` does not exist
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import re
import shutil
import sys
import tomllib
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.requirements import Requirement

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_NPM_ROOT = _REPO_ROOT / "vendor" / "bgutil-pot-provider" / "server" / "node_modules"

# Normalized dist name -> (license, license file), for packages whose wheel ships no license
# text. Point at real files only; never fabricate license text.
_OVERRIDES: dict[str, tuple[str, Path]] = {
    "bgutil-ytdlp-pot-provider": (
        "GPL-3.0",
        _REPO_ROOT / "vendor" / "bgutil-pot-provider" / "LICENSE",
    ),
}

_UNKNOWN_LICENSE = "UNKNOWN"
_NO_FILE_NOTE = "(no license file shipped by package)"
_INVENTORY_HEADER = "ecosystem\tname\tversion\tlicense\tsource_url"

_PY_LICENSE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
_NPM_LICENSE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE)", re.IGNORECASE)
_DENO_KEY = re.compile(r"^(?P<name>(?:@[^+@]+\+)?[^@+]+)@(?P<ver>[^@]+)$")
_LICENSE_CLASSIFIER_PREFIX = "License :: "
# "code" / "source code" cover urllib3 and charset-normalizer, which label their repo "Code".
_SOURCE_URL_LABELS = frozenset({"source", "source code", "code", "repository", "homepage"})
_FFMPEG_COMMIT = re.compile(r"-g([0-9a-f]{7,40})-")
_FFMPEG_VERSION = re.compile(r"^ffmpeg-(?P<ver>.+?)-win(?:32|64|arm64)", re.IGNORECASE)
_FFMPEG_LICENSE = "GPL-3.0-or-later"
_FFMPEG_FALLBACK_URL = "https://ffmpeg.org/download.html"
_PYTHON_RUNTIME_URL = "https://www.python.org/"


@dataclass(frozen=True)
class Component:
    ecosystem: str  # "python" | "npm" | "binary"
    name: str
    version: str
    license: str | None  # SPDX expression / declared license string, None if undeclared
    source_url: str | None  # homepage / repository / source link
    license_files: tuple[Path, ...]  # absolute paths of license texts to copy


def _normalize(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------- python


def project_requirements(pyproject: Path) -> tuple[str, list[str]]:
    """
    Return ``(name, dependencies)`` from the ``[project]`` table of ``pyproject``.

    Read from the file, not ``importlib.metadata``: uv syncs this project as a virtual
    (never-installed) root, so it has no dist metadata — and a stray ``*.egg-info`` in the
    checkout would supply a stale dependency list. Raises ``ValueError`` when the table or
    its ``name`` is missing.
    """
    with pyproject.open("rb") as fh:
        project: dict[str, Any] = tomllib.load(fh).get("project", {})
    name = project.get("name")
    if not name:
        msg = f"{pyproject} has no [project] name"
        raise ValueError(msg)
    return str(name), [str(spec) for spec in project.get("dependencies", [])]


def python_closure(
    root_name: str, root_requires: Iterable[str]
) -> list[importlib.metadata.Distribution]:
    """
    Return the installed runtime dependency closure of ``root_requires`` (root excluded).

    Breadth-first over ``Requires-Dist``. A requirement whose marker does not hold with no
    extras selected (extras-only or wrong-platform) is skipped before lookup; an applicable
    requirement that is not installed raises ``PackageNotFoundError``.
    """
    seen: set[str] = {_normalize(root_name)}
    pending: deque[Iterable[str]] = deque([root_requires])
    closure: list[importlib.metadata.Distribution] = []
    while pending:
        for spec in pending.popleft():
            req = Requirement(spec)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            key = _normalize(req.name)
            if key in seen:
                continue
            seen.add(key)
            dep = importlib.metadata.distribution(req.name)
            closure.append(dep)
            pending.append(dep.requires or [])
    return closure


def _python_license(dist: importlib.metadata.Distribution) -> str | None:
    """
    Return the declared license, or None when the metadata declares none.

    Precedence: ``License-Expression``, then the first ``License`` line (``UNKNOWN``
    ignored), then the trailing segment of each ``License ::`` trove classifier (e.g.
    idna declares its license only as ``License :: OSI Approved :: BSD License``).
    """
    metadata = dist.metadata
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    legacy = (metadata.get("License") or "").strip()
    first_line = legacy.splitlines()[0].strip() if legacy else ""
    if first_line and first_line.upper() != _UNKNOWN_LICENSE:
        return first_line
    classifiers = [
        entry.rsplit("::", 1)[-1].strip()
        for entry in metadata.get_all("Classifier") or []
        if entry.startswith(_LICENSE_CLASSIFIER_PREFIX)
    ]
    return ", ".join(classifiers) if classifiers else None


def _is_python_license_file(parts: tuple[str, ...]) -> bool:
    """Return True for a ``*.dist-info`` entry under ``licenses/`` or named like a license."""
    for index, part in enumerate(parts[:-1]):
        if part.endswith(".dist-info"):
            inside = parts[index + 1 :]
            return inside[0] == "licenses" or bool(_PY_LICENSE_NAME.match(parts[-1]))
    return False


def _python_source_url(dist: importlib.metadata.Distribution) -> str | None:
    """Return the first Source/Repository/Homepage ``Project-URL``, else ``Home-page``."""
    for entry in dist.metadata.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in _SOURCE_URL_LABELS and url.strip():
            return url.strip()
    home = (dist.metadata.get("Home-page") or "").strip()
    return home or None


def python_component(dist: importlib.metadata.Distribution) -> Component:
    """
    Describe one installed distribution, with ``_OVERRIDES`` already applied.

    The override replaces both the license and the license files, for wheels that ship no
    license text (e.g. ``bgutil-ytdlp-pot-provider``).
    """
    files: list[Path] = []
    for entry in dist.files or []:
        if _is_python_license_file(entry.parts):
            path = Path(str(dist.locate_file(entry))).resolve()
            if path.is_file():
                files.append(path)
    name = _normalize(dist.name)
    component = Component(
        ecosystem="python",
        name=name,
        version=dist.version,
        license=_python_license(dist),
        source_url=_python_source_url(dist),
        license_files=tuple(files),
    )
    override = _OVERRIDES.get(name)
    if override is None:
        return component
    license_id, license_file = override
    return replace(component, license=license_id, license_files=(license_file.resolve(),))


# --------------------------------------------------------------------------- npm


def _npm_license(manifest: dict[str, Any]) -> str | None:
    """Return the declared license from a ``package.json`` (modern or legacy shapes)."""
    declared = manifest.get("license")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    if isinstance(declared, dict):
        kind = declared.get("type")
        if isinstance(kind, str) and kind.strip():
            return kind.strip()
    legacy = manifest.get("licenses")
    if isinstance(legacy, list):
        kinds = [
            item["type"].strip()
            for item in legacy
            if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"].strip()
        ]
        if kinds:
            return " OR ".join(kinds)
    return None


def _npm_source_url(manifest: dict[str, Any]) -> str | None:
    """Return ``repository`` (string or ``.url``), else ``homepage``."""
    repository = manifest.get("repository")
    if isinstance(repository, dict):
        repository = repository.get("url")
    if isinstance(repository, str) and repository.strip():
        return repository.strip()
    homepage = manifest.get("homepage")
    if isinstance(homepage, str) and homepage.strip():
        return homepage.strip()
    return None


def _license_files_in(directory: Path, pattern: re.Pattern[str]) -> tuple[Path, ...]:
    """Return the files directly in ``directory`` whose name matches ``pattern``, sorted."""
    return tuple(
        sorted(
            child.resolve()
            for child in directory.iterdir()
            if child.is_file() and pattern.match(child.name)
        )
    )


def npm_components(npm_root: Path) -> list[Component]:
    """
    Describe every package in the Deno npm store ``npm_root/.deno``.

    Store keys are ``<name>@<version>`` with a scope's ``/`` written as ``+``. Entries that
    are not package keys are skipped; a skipped directory other than the store's own
    ``node_modules`` is reported on stderr, since it may be a package this parser missed.
    """
    store = npm_root / ".deno"
    components: dict[tuple[str, str], Component] = {}
    for key_dir in sorted(store.iterdir()):
        match = _DENO_KEY.match(key_dir.name)
        if match is None or not key_dir.is_dir():
            if key_dir.is_dir() and key_dir.name != "node_modules":
                print(f"warning: skipped unrecognized npm store entry: {key_dir}", file=sys.stderr)
            continue
        key_name = match["name"]
        name = key_name.replace("+", "/", 1) if key_name.startswith("@") else key_name
        package_dir = key_dir / "node_modules" / name
        manifest_path = package_dir / "package.json"
        if not manifest_path.is_file():
            msg = f"npm package.json not found: {manifest_path}"
            raise FileNotFoundError(msg)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = match["ver"]
        components[name, version] = Component(
            ecosystem="npm",
            name=name,
            version=version,
            license=_npm_license(manifest),
            source_url=_npm_source_url(manifest),
            license_files=_license_files_in(package_dir, _NPM_LICENSE_NAME),
        )
    return list(components.values())


# --------------------------------------------------------------------------- binary


def ffmpeg_component(root: Path, asset: str | None) -> Component:
    """
    Describe the FFmpeg build extracted under ``root`` (BtbN ``win64-gpl`` variant).

    ``asset`` is the release zip's filename; its ``-g<sha>-`` segment gives the exact
    upstream commit (the corresponding-source link) and its version segment names the
    bundle folder.
    """
    files = tuple(sorted(path.resolve() for path in root.rglob("LICENSE*") if path.is_file()))
    commit = _FFMPEG_COMMIT.search(asset) if asset else None
    source_url = (
        f"https://github.com/FFmpeg/FFmpeg/commit/{commit[1]}" if commit else _FFMPEG_FALLBACK_URL
    )
    version_match = _FFMPEG_VERSION.match(asset) if asset else None
    if version_match is not None:
        version = version_match["ver"]
    elif asset:
        version = Path(asset).stem
    else:
        version = "unknown"
    return Component(
        ecosystem="binary",
        name="ffmpeg",
        version=version,
        license=_FFMPEG_LICENSE,
        source_url=source_url,
        license_files=files,
    )


def _python_runtime_component() -> Component | None:
    """Return the CPython runtime as a component, or None without ``LICENSE.txt``."""
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if not license_file.is_file():
        return None
    return Component(
        ecosystem="binary",
        name="python",
        version=platform.python_version(),
        license="PSF-2.0",
        source_url=_PYTHON_RUNTIME_URL,
        license_files=(license_file.resolve(),),
    )


# --------------------------------------------------------------------------- output


def validate(components: Iterable[Component]) -> list[str]:
    """Return one error per component with neither a declared license nor a license file."""
    return [
        f"error: {c.ecosystem} {c.name} {c.version}: no declared license and no license file "
        "(add an _OVERRIDES entry pointing at its real license text)"
        for c in components
        if c.license is None and not c.license_files
    ]


def _safe_name(name: str) -> str:
    return name.replace("/", "_").replace("@", "_")


def _copy_license_files(component: Component, out: Path) -> None:
    """Copy a component's license files, disambiguating repeated filenames."""
    target = out / component.ecosystem / f"{_safe_name(component.name)}-{component.version}"
    target.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    for source in component.license_files:
        name = source.name
        counter = 1
        while name.lower() in used:
            counter += 1
            name = f"{source.stem}-{counter}{source.suffix}"
        used.add(name.lower())
        shutil.copyfile(source, target / name)


def _inventory_line(component: Component) -> str:
    fields = [
        component.ecosystem,
        component.name,
        component.version,
        component.license or "UNDECLARED",
        component.source_url or "-",
    ]
    if not component.license_files:
        fields.append(_NO_FILE_NOTE)
    return "\t".join(fields)


def write_bundle(components: Iterable[Component], out: Path) -> None:
    """
    Wipe ``out`` and write every component's license files plus ``INVENTORY.txt``.

    Also copies MeadowLark's ``LICENSE`` (as ``MeadowLark-LICENSE.txt``) and
    ``THIRD_PARTY_NOTICES.md`` when present, and adds the CPython runtime license as a
    ``binary`` component.
    """
    all_components = list(components)
    runtime = _python_runtime_component()
    if runtime is not None:
        all_components.append(runtime)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for source, dest_name in (
        (_REPO_ROOT / "LICENSE", "MeadowLark-LICENSE.txt"),
        (_REPO_ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    ):
        if source.is_file():
            shutil.copyfile(source, out / dest_name)

    ordered = sorted(all_components, key=lambda c: (c.ecosystem, c.name, c.version))
    for component in ordered:
        if component.license_files:
            _copy_license_files(component, out)
    lines = [_INVENTORY_HEADER, *(_inventory_line(c) for c in ordered)]
    (out / "INVENTORY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect the license texts and an inventory of every third-party component "
            "the MeadowLark installer bundles."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory (wiped first); the release passes dist/MeadowLark/licenses.",
    )
    parser.add_argument(
        "--npm-root",
        type=Path,
        default=_DEFAULT_NPM_ROOT,
        help="node_modules dir holding the Deno npm store (default: the vendored server's).",
    )
    parser.add_argument(
        "--ffmpeg-root",
        type=Path,
        default=None,
        help="Directory containing the FFmpeg build's LICENSE* file(s), searched recursively.",
    )
    parser.add_argument(
        "--ffmpeg-asset",
        default=None,
        help="FFmpeg release zip filename; its -g<sha>- segment gives the source commit link.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=_REPO_ROOT / "pyproject.toml",
        help="pyproject.toml whose [project] dependencies seed the Python closure "
        "(default: the repo's).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    store = args.npm_root / ".deno"
    if not store.is_dir():
        print(
            f"error: Deno npm store not found: {store}. "
            "Run 'uv run python scripts/setup_pot_provider.py --skip-warm' first.",
            file=sys.stderr,
        )
        return 2
    if args.ffmpeg_root is not None and not args.ffmpeg_root.is_dir():
        print(f"error: --ffmpeg-root is not a directory: {args.ffmpeg_root}", file=sys.stderr)
        return 2

    try:
        root_name, root_requires = project_requirements(args.pyproject)
        components = [python_component(d) for d in python_closure(root_name, root_requires)]
        components.extend(npm_components(args.npm_root))
    except importlib.metadata.PackageNotFoundError as exc:
        print(f"error: required distribution not installed: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = validate(components)
    if args.ffmpeg_root is not None:
        ffmpeg = ffmpeg_component(args.ffmpeg_root, args.ffmpeg_asset)
        if not ffmpeg.license_files:
            errors.append(
                f"error: no LICENSE* file under --ffmpeg-root {args.ffmpeg_root}; "
                "FFmpeg's GPL text must ship with the installer"
            )
        components.append(ffmpeg)

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    for component in components:
        if not component.license_files:
            print(
                f"warning: {component.ecosystem} {component.name} {component.version}: "
                f"declares {component.license} but ships no license file",
                file=sys.stderr,
            )

    write_bundle(components, args.out)
    count = len(components) + (1 if _python_runtime_component() is not None else 0)
    print(f"wrote {count} components to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

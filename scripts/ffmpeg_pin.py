"""
Own the FFmpeg pin in ``.github/ffmpeg-pin.yml``.

The release job bundles a Windows FFmpeg build from BtbN/FFmpeg-Builds, pinned by three
``KEY: value`` lines in that file: ``FFMPEG_TAG`` (an ``autobuild-YYYY-MM-DD-HH-MM`` release
tag), ``FFMPEG_ASSET`` (the ``ffmpeg-<version>-win64-gpl-<maj>.<min>.zip`` asset name) and
``FFMPEG_SHA256`` (that asset's hash). This script reads and rewrites those lines, decides
whether a newer build exists, and downloads, verifies and extracts the binaries, so the
download-and-verify logic exists once for the release job and the monthly bump workflow.
The file lives outside ``.github/workflows/`` on purpose: the bump workflow pushes it with
``GITHUB_TOKEN``, which GitHub forbids from modifying workflow files.

Only month-end autobuilds (tagged on the last calendar day of a month) are ever pinned: BtbN
prunes dailies after about two weeks, while month-end builds are retained long-term, so a
pinned daily would soon 404 and break every release build.

Only patch-level moves (a newer month-end build on the pinned release branch) are proposed
automatically. A newer branch, or the pinned branch disappearing, becomes a notice for a
person to act on.

Stdlib only: CI runs it with ``uv run --no-project``, which skips the app environment.

Usage::

    uv run python scripts/ffmpeg_pin.py check [--branch X.Y]  # decision as JSON
    uv run python scripts/ffmpeg_pin.py apply                 # pin from FFMPEG_* env vars
    uv run python scripts/ffmpeg_pin.py fetch --dest DIR      # download, verify, extract
    uv run python scripts/ffmpeg_pin.py pr-body               # bump-PR body from env vars

``--pin-file PATH`` (before the subcommand) points every command at another file.

Exit codes:
    0  success
    1  a pin, network or verification error (printed as ``error: ...`` on stderr)
    2  invalid command-line usage (argparse)
"""

import argparse
import calendar
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Deliberately NOT under .github/workflows/: the bump workflow pushes this file with
# GITHUB_TOKEN, and GitHub refuses token pushes that modify workflow files.
_DEFAULT_PIN_FILE = _REPO_ROOT / ".github" / "ffmpeg-pin.yml"

_BTBN = "BtbN/FFmpeg-Builds"
_BTBN_WEB = f"https://github.com/{_BTBN}"
# One page is enough, so do not add pagination: the API lists releases newest first, and the
# newest month-end build is always within the newest ~16 entries (about 14 retained dailies,
# the rolling `latest`, then the month-end).
_RELEASES_API = f"https://api.github.com/repos/{_BTBN}/releases?per_page=100"
_CHECKSUMS_ASSET = "checksums.sha256"

_HTTP_TIMEOUT_S = 60
_CHUNK = 1024 * 1024
_BINARIES = ("ffmpeg.exe", "ffprobe.exe")
_PIN_KEYS = ("FFMPEG_TAG", "FFMPEG_ASSET", "FFMPEG_SHA256")

_TAG = re.compile(r"^autobuild-(\d{4})-(\d{2})-(\d{2})-\d{2}-\d{2}\Z")
_ASSET = re.compile(r"^ffmpeg-(n\d+\.\d+(?:\.\d+)?-\S+?)-win64-gpl-(\d+)\.(\d+)\.zip\Z")
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
_PIN_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>FFMPEG_TAG|FFMPEG_ASSET|FFMPEG_SHA256):"
    r"[ \t]*(?P<value>\S+)[ \t]*\r?$",
    re.MULTILINE,
)
_CHECKSUM_LINE = re.compile(r"^(?P<hex>[0-9a-fA-F]{64}) [ *](?P<name>.+)$")
_BRANCH_ARG = re.compile(r"^(\d+)\.(\d+)\Z")


class PinError(Exception):
    """A pin, network or verification failure, reported as ``error: ...`` by the CLI."""


@dataclass(frozen=True)
class Pin:
    """
    One pinned BtbN build.

    Attributes:
        tag: Release tag, ``autobuild-YYYY-MM-DD-HH-MM``.
        asset: Zip name, e.g. ``ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip``.
        sha256: The zip's SHA256, 64 lowercase hex digits.
    """

    tag: str
    asset: str
    sha256: str


@dataclass(frozen=True)
class Notice:
    """Something a person must act on; the bump workflow files it as a deduplicated issue."""

    title: str
    body: str


@dataclass(frozen=True)
class Decision:
    """
    Outcome of :func:`decide`.

    Attributes:
        status: ``"patch"`` when ``candidate`` should replace the pin, else ``"current"``.
        candidate: The pin to apply; ``None`` unless ``status`` is ``"patch"``.
        notices: Branch-level findings that need a person, independent of ``status``.
    """

    status: Literal["current", "patch"]
    candidate: Pin | None
    notices: list[Notice] = field(default_factory=list[Notice])


# --- Pure core ------------------------------------------------------------------------------


def _validate(pin: Pin) -> Pin:
    """Return ``pin`` unchanged, or raise :class:`PinError` naming the first malformed value."""
    if not _TAG.match(pin.tag):
        msg = f"FFMPEG_TAG {pin.tag!r} is not an autobuild-YYYY-MM-DD-HH-MM tag"
        raise PinError(msg)
    if not _ASSET.match(pin.asset):
        msg = f"FFMPEG_ASSET {pin.asset!r} is not a ffmpeg-*-win64-gpl-X.Y.zip asset"
        raise PinError(msg)
    if not _SHA256.match(pin.sha256):
        msg = f"FFMPEG_SHA256 {pin.sha256!r} is not 64 lowercase hex digits"
        raise PinError(msg)
    return pin


def _pin_matches(pin_text: str) -> dict[str, re.Match[str]]:
    """Map each pin key to its single line match; raise :class:`PinError` otherwise."""
    found: dict[str, list[re.Match[str]]] = {key: [] for key in _PIN_KEYS}
    for match in _PIN_LINE.finditer(pin_text):
        found[match["key"]].append(match)
    for key, matches in found.items():
        if len(matches) != 1:
            msg = f"the pin file must define {key} exactly once (found {len(matches)})"
            raise PinError(msg)
    return {key: matches[0] for key, matches in found.items()}


def parse_pin(pin_text: str) -> Pin:
    r"""
    Read the pin from the text of the pin file.

    Example:
        >>> parse_pin(
        ...     "      FFMPEG_TAG: autobuild-2026-10-31-12-59\n"
        ...     "      FFMPEG_ASSET: ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip\n"
        ...     "      FFMPEG_SHA256: " + "0" * 64 + "\n"
        ... ).tag
        'autobuild-2026-10-31-12-59'

    Raises:
        PinError: A key is missing or repeated, or a value is malformed.
    """
    matches = _pin_matches(pin_text)
    return _validate(
        Pin(
            tag=matches["FFMPEG_TAG"]["value"],
            asset=matches["FFMPEG_ASSET"]["value"],
            sha256=matches["FFMPEG_SHA256"]["value"],
        )
    )


def render_pin(pin_text: str, pin: Pin) -> str:
    """
    Return ``pin_text`` with only the three pin values replaced by ``pin``'s.

    Indentation, line endings and every other byte are kept, so
    ``render_pin(text, parse_pin(text)) == text``.

    Raises:
        PinError: The text has no well-formed pin lines, or ``pin`` is malformed.
    """
    _validate(pin)
    values = {"FFMPEG_TAG": pin.tag, "FFMPEG_ASSET": pin.asset, "FFMPEG_SHA256": pin.sha256}
    spans = sorted(
        (match.span("value"), values[key]) for key, match in _pin_matches(pin_text).items()
    )
    out: list[str] = []
    cursor = 0
    for (start, end), value in spans:
        out.extend((pin_text[cursor:start], value))
        cursor = end
    out.append(pin_text[cursor:])
    return "".join(out)


def _tag_date(tag: str) -> date | None:
    """Return the build date in an autobuild tag, or ``None`` for any other or invalid tag."""
    match = _TAG.match(tag)
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def month_end_date(tag: str) -> date | None:
    """
    Return the date of a month-end autobuild tag, else ``None``.

    Example:
        >>> month_end_date("autobuild-2024-02-29-12-59")
        datetime.date(2024, 2, 29)
        >>> month_end_date("autobuild-2026-10-30-12-59") is None  # a daily
        True
        >>> month_end_date("latest") is None
        True
    """
    built = _tag_date(tag)
    if built is None or built.day != calendar.monthrange(built.year, built.month)[1]:
        return None
    return built


def _asset_match(asset: str) -> re.Match[str]:
    match = _ASSET.match(asset)
    if match is None:
        msg = f"{asset!r} is not a ffmpeg-*-win64-gpl-X.Y.zip asset"
        raise PinError(msg)
    return match


def _branch(asset: str) -> tuple[int, int]:
    match = _asset_match(asset)
    return int(match[2]), int(match[3])


def _branch_text(branch: tuple[int, int]) -> str:
    return f"{branch[0]}.{branch[1]}"


def branch_assets(names: Iterable[str]) -> dict[tuple[int, int], str]:
    """
    Map each release branch to its ``win64-gpl-<maj>.<min>.zip`` asset name.

    Master (``win64-gpl.zip``), shared, LGPL and non-Windows builds are skipped. Keys are int
    tuples, so they order numerically; the first asset seen for a branch wins.

    Example:
        >>> branch_assets([
        ...     "ffmpeg-master-latest-win64-gpl.zip",
        ...     "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip",
        ...     "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-shared-9.0.zip",
        ... ])
        {(9, 0): 'ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip'}
    """
    out: dict[tuple[int, int], str] = {}
    for name in names:
        if _ASSET.match(name):
            out.setdefault(_branch(name), name)
    return out


def parse_checksums(text: str) -> dict[str, str]:
    r"""
    Parse ``sha256sum`` output (``<hex>  <name>`` or ``<hex> *<name>``) into name -> hex.

    Hex is lowercased; lines in any other shape are ignored.

    Example:
        >>> parse_checksums("AB" * 32 + " *a.zip\n")
        {'a.zip': 'abababababababababababababababababababababababababababababababab'}
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = _CHECKSUM_LINE.match(line.strip())
        if match:
            out[match["name"]] = match["hex"].lower()
    return out


def version_label(asset: str) -> str:
    """
    Return the FFmpeg version embedded in an asset name.

    Example:
        >>> version_label("ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip")
        'n9.0.2-39-g7adfbe40dc'

    Raises:
        PinError: ``asset`` is not a ``win64-gpl-X.Y`` zip name.
    """
    return _asset_match(asset)[1]


def _release_url(tag: str) -> str:
    return f"{_BTBN_WEB}/releases/tag/{tag}"


def _new_branch_notice(tag: str, branch: tuple[int, int], pinned: tuple[int, int]) -> Notice:
    new, old = _branch_text(branch), _branch_text(pinned)
    return Notice(
        title=f"FFmpeg: release branch {new} is available (pinned: {old})",
        body=(
            f"BtbN's newest month-end build [{tag}]({_release_url(tag)}) includes release "
            f"branch {new}; MeadowLark pins {old}. Branch moves are never applied "
            "automatically. To move manually:\n\n"
            f"    uv run python scripts/ffmpeg_pin.py check --branch {new}\n\n"
            "then set FFMPEG_TAG / FFMPEG_ASSET / FFMPEG_SHA256 from its candidate and run\n\n"
            "    uv run python scripts/ffmpeg_pin.py apply\n"
        ),
    )


def _branch_dropped_notice(tag: str, pinned: tuple[int, int]) -> Notice:
    old = _branch_text(pinned)
    return Notice(
        title=f"FFmpeg: BtbN no longer builds pinned branch {old}",
        body=(
            f"BtbN's newest month-end build [{tag}]({_release_url(tag)}) has no "
            f"win64-gpl-{old} asset, so the pin cannot be bumped automatically. Choose a "
            "branch and move manually with `uv run python scripts/ffmpeg_pin.py check "
            "--branch X.Y`, then `apply`.\n"
        ),
    )


def decide(
    pin: Pin,
    releases: list[dict[str, Any]],
    checksums: Callable[[dict[str, Any]], str],
    branch: tuple[int, int] | None = None,
) -> Decision:
    """
    Decide whether the newest month-end build should replace ``pin``.

    Args:
        pin: The current pin.
        releases: GitHub API release objects (``tag_name``, ``assets[].name``, ...).
        checksums: Returns the ``checksums.sha256`` text of a release; called only for a patch.
        branch: Target release branch; defaults to the pin's own branch.

    Returns:
        ``patch`` with a candidate when the newest month-end build is newer than the pin, or
        when ``branch`` differs from the pin's branch; otherwise ``current``. Notices report a
        newer branch, or the pinned branch no longer being built.

    Raises:
        PinError: No month-end release, an explicitly requested branch is absent, the pin is
            malformed, or the candidate asset is missing from the checksums.
    """
    month_ends = [r for r in releases if month_end_date(str(r.get("tag_name", ""))) is not None]
    if not month_ends:
        raise PinError("no month-end autobuild among the newest BtbN releases")
    # Tags are zero-padded timestamps, so the lexicographic max is the newest build.
    newest = max(month_ends, key=lambda r: str(r["tag_name"]))
    newest_tag = str(newest["tag_name"])
    newest_date = month_end_date(newest_tag)
    pin_date = _tag_date(pin.tag)
    if newest_date is None or pin_date is None:
        msg = f"pinned tag {pin.tag!r} is not an autobuild-YYYY-MM-DD-HH-MM tag"
        raise PinError(msg)

    pinned = _branch(pin.asset)
    target = branch or pinned
    assets = branch_assets(str(a.get("name", "")) for a in newest.get("assets", []))

    notices: list[Notice] = []
    if assets and max(assets) > max(pinned, target):
        notices.append(_new_branch_notice(newest_tag, max(assets), pinned))
    if target not in assets:
        if branch is not None:
            available = ", ".join(_branch_text(b) for b in sorted(assets)) or "none"
            msg = (
                f"{newest_tag} has no win64-gpl-{_branch_text(target)} asset "
                f"(available: {available})"
            )
            raise PinError(msg)
        notices.append(_branch_dropped_notice(newest_tag, pinned))
        return Decision("current", None, notices)

    if newest_date > pin_date or target != pinned:
        asset = assets[target]
        sha256 = parse_checksums(checksums(newest)).get(asset)
        if sha256 is None:
            msg = f"{asset} is missing from {newest_tag}'s {_CHECKSUMS_ASSET}"
            raise PinError(msg)
        return Decision("patch", Pin(newest_tag, asset, sha256), notices)
    return Decision("current", None, notices)


# --- I/O shell ------------------------------------------------------------------------------


def _get(url: str) -> bytes:
    """
    GET ``url`` and return the body; every network access in this module goes through here.

    Sends ``Authorization: Bearer $GITHUB_TOKEN`` only when that variable is set, and as an
    unredirected header, so a redirect to the asset CDN never receives it.

    Raises:
        PinError: Transport or HTTP failure (never chained, so the token cannot leak).
    """
    if not url.startswith("https://"):
        msg = f"refusing non-HTTPS URL {url!r}"
        raise PinError(msg)
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MeadowLark-ffmpeg-pin"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310
            body: bytes = response.read()
    except urllib.error.HTTPError as exc:
        exc.close()  # the error owns the response body; release it deterministically
        msg = f"GET {url} failed: HTTP {exc.code} {exc.reason}"
        raise PinError(msg) from None
    except (urllib.error.URLError, OSError) as exc:
        msg = f"GET {url} failed: {type(exc).__name__}: {exc}"
        raise PinError(msg) from None
    return body


def _download_url(pin: Pin) -> str:
    return f"{_BTBN_WEB}/releases/download/{pin.tag}/{pin.asset}"


def _binary_members(zf: zipfile.ZipFile, asset: str) -> dict[str, zipfile.ZipInfo]:
    """Return the single ``bin/ffmpeg.exe`` and ``bin/ffprobe.exe`` members, keyed by basename."""
    found: dict[str, list[zipfile.ZipInfo]] = {name: [] for name in _BINARIES}
    for info in zf.infolist():
        parts = info.filename.replace("\\", "/").split("/")
        if not info.is_dir() and len(parts) >= 2 and parts[-2] == "bin" and parts[-1] in found:
            found[parts[-1]].append(info)
    for name, members in found.items():
        if len(members) != 1:
            msg = f"{asset} must contain exactly one bin/{name} (found {len(members)})"
            raise PinError(msg)
    return {name: members[0] for name, members in found.items()}


def fetch(pin: Pin, dest: Path) -> None:
    """
    Download ``pin``'s zip, verify its SHA256 and extract only ``ffmpeg.exe``/``ffprobe.exe``.

    Members are written by basename via ``ZipFile.open`` (never ``extractall``), so a crafted
    member path cannot escape ``dest``. Both binaries are staged in a temp directory first, so
    ``dest`` (created if missing) gains nothing on any :class:`PinError`.

    Raises:
        PinError: Download failure, hash mismatch, or not exactly one of each binary.
    """
    data = _get(_download_url(pin))
    with tempfile.TemporaryDirectory(prefix="ffmpeg_pin_") as tmp:
        staging = Path(tmp)
        archive = staging / "ffmpeg.zip"
        digest = hashlib.sha256()
        view = memoryview(data)
        with archive.open("wb") as out:
            for start in range(0, len(view), _CHUNK):
                chunk = view[start : start + _CHUNK]
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if actual != pin.sha256.lower():
            msg = f"FFmpeg SHA256 mismatch: expected {pin.sha256}, got {actual}"
            raise PinError(msg)

        try:
            with zipfile.ZipFile(archive) as zf:
                for name, info in _binary_members(zf, pin.asset).items():
                    with zf.open(info) as src, (staging / name).open("wb") as dst:
                        shutil.copyfileobj(src, dst, _CHUNK)
        except zipfile.BadZipFile as exc:
            msg = f"{pin.asset} is not a valid zip: {exc}"
            raise PinError(msg) from None

        dest.mkdir(parents=True, exist_ok=True)
        for name in _BINARIES:
            shutil.move(staging / name, dest / name)


def _read_text(path: Path) -> str:
    # newline="" keeps CRLF/LF exactly as on disk, so render_pin round-trips byte for byte.
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        msg = f"cannot read {path}: {exc.strerror}"
        raise PinError(msg) from None


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        msg = f"environment variable {name} is not set"
        raise PinError(msg)
    return value


def _pin_from_env() -> Pin:
    return _validate(Pin(_env("FFMPEG_TAG"), _env("FFMPEG_ASSET"), _env("FFMPEG_SHA256")))


def _release_checksums(release: dict[str, Any]) -> str:
    """Download the ``checksums.sha256`` asset of a GitHub API release object."""
    for asset in release.get("assets", []):
        if asset.get("name") == _CHECKSUMS_ASSET:
            return _get(str(asset["browser_download_url"])).decode("utf-8")
    msg = f"{release.get('tag_name')} has no {_CHECKSUMS_ASSET} asset"
    raise PinError(msg)


def _fetch_releases() -> list[dict[str, Any]]:
    try:
        releases = json.loads(_get(_RELEASES_API))
    except ValueError as exc:
        msg = f"releases API returned invalid JSON: {exc}"
        raise PinError(msg) from None
    if not isinstance(releases, list):
        raise PinError("releases API did not return a list")
    return [r for r in releases if isinstance(r, dict)]


def _write_github_output(path: str, values: dict[str, str]) -> None:
    with Path(path).open("a", encoding="utf-8", newline="") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in values.items())


def _cmd_check(pin_file: Path, branch: tuple[int, int] | None) -> None:
    pin = parse_pin(_read_text(pin_file))
    decision = decide(pin, _fetch_releases(), _release_checksums, branch)
    candidate = decision.candidate if decision.status == "patch" else None
    notices = [asdict(n) for n in decision.notices]
    print(
        json.dumps(
            {
                "status": decision.status,
                "current": {**asdict(pin), "version": version_label(pin.asset)},
                "candidate": (
                    {**asdict(candidate), "version": version_label(candidate.asset)}
                    if candidate
                    else None
                ),
                "notices": notices,
            },
            indent=2,
        )
    )
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        _write_github_output(
            github_output,
            {
                "status": decision.status,
                "tag": candidate.tag if candidate else "",
                "asset": candidate.asset if candidate else "",
                "sha256": candidate.sha256 if candidate else "",
                "version": version_label(candidate.asset) if candidate else "",
                "current_version": version_label(pin.asset),
                "notices": json.dumps(notices, separators=(",", ":")),
            },
        )


def _cmd_apply(pin_file: Path) -> None:
    pin = _pin_from_env()
    _write_text(pin_file, render_pin(_read_text(pin_file), pin))
    print(f"{pin_file}: pinned FFmpeg {version_label(pin.asset)} ({pin.tag})")


def _cmd_fetch(pin_file: Path, dest: Path) -> None:
    pin = parse_pin(_read_text(pin_file))
    fetch(pin, dest)
    print(f"FFmpeg {version_label(pin.asset)} verified; {', '.join(_BINARIES)} -> {dest}")


def _cmd_pr_body() -> None:
    pin = _pin_from_env()
    current = _env("FFMPEG_CURRENT_VERSION")
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    run_id = _env("GITHUB_RUN_ID")
    run_url = f"{server}/{_env('GITHUB_REPOSITORY')}/actions/runs/{run_id}"
    print(
        f"Bumps the bundled FFmpeg from `{current}` to `{version_label(pin.asset)}`.\n\n"
        f"- Build: [{pin.tag}]({_release_url(pin.tag)}), asset `{pin.asset}`\n"
        f"- SHA256: `{pin.sha256}`\n"
        f"- Verified by [workflow run {run_id}]({run_url}): the candidate was downloaded and "
        "hash-checked, smoke-tested through yt-dlp's postprocessors, and the full test suite "
        "passed on the bumped tree.\n\n"
        "Note: `ci.yml` does not run on PRs opened with `GITHUB_TOKEN`, so the run above is "
        "this PR's gate. CI runs again on `main` after merge."
    )


def _branch_arg(text: str) -> tuple[int, int]:
    match = _BRANCH_ARG.match(text)
    if match is None:
        msg = f"expected a release branch like 9.0, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return int(match[1]), int(match[2])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check, apply and fetch the FFmpeg pin.")
    parser.add_argument(
        "--pin-file",
        type=Path,
        default=_DEFAULT_PIN_FILE,
        help="workflow file holding the FFMPEG_* pin (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="print whether a newer month-end build exists")
    check.add_argument("--branch", type=_branch_arg, help="target release branch X.Y")
    commands.add_parser("apply", help="rewrite the pin from FFMPEG_TAG/ASSET/SHA256 env vars")
    fetch_cmd = commands.add_parser("fetch", help="download, verify and extract the pinned build")
    fetch_cmd.add_argument("--dest", type=Path, required=True, help="directory for the binaries")
    commands.add_parser("pr-body", help="print the bump-PR body from env vars")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return 0, or print ``error: ...`` to stderr and return 1 on PinError."""
    args = _parser().parse_args(argv)
    pin_file: Path = args.pin_file
    try:
        if args.command == "check":
            _cmd_check(pin_file, args.branch)
        elif args.command == "apply":
            _cmd_apply(pin_file)
        elif args.command == "fetch":
            _cmd_fetch(pin_file, args.dest)
        else:
            _cmd_pr_body()
    except PinError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

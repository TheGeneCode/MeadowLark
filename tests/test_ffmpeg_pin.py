"""
Tests for scripts/ffmpeg_pin.py, the owner of the FFmpeg pin file.

The pure core (parse/render/decide) is exercised with inline release dicts; the I/O shell
is exercised with `_get` monkeypatched, so nothing here touches the network. Real
FFmpeg behaviour is covered separately by tests/test_ffmpeg_smoke.py.
"""

import difflib
import hashlib
import io
import json
import re
import urllib.error
import zipfile
from collections.abc import Callable
from datetime import date
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any, Self

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_PIN_FILE = _REPO_ROOT / ".github" / "ffmpeg-pin.yml"

_TAG_PIN = "autobuild-2026-10-31-12-59"
_ASSET_9 = "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip"
_ASSET_10 = "ffmpeg-n10.0.1-5-gabcdef1234-win64-gpl-10.0.zip"
_SHA_A = "a" * 64
_SHA_B = "b" * 64


@pytest.fixture
def mod(import_script: Callable[[str], ModuleType]) -> ModuleType:
    return import_script("ffmpeg_pin.py")


def _release(tag: str, *assets: str) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "html_url": f"https://example.invalid/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.invalid/{tag}/{name}"}
            for name in assets
        ],
    }


def _checksums_for(*pairs: tuple[str, str]) -> Callable[[dict[str, Any]], str]:
    text = "".join(f"{sha}  {name}\n" for name, sha in pairs)
    return lambda _release: text


def _pin(mod: ModuleType) -> Any:
    return mod.Pin(_TAG_PIN, _ASSET_9, _SHA_A)


def _real_text() -> str:
    return _REAL_PIN_FILE.read_text(encoding="utf-8")


# --- pin file ---------------------------------------------------------------------------------


def test_parse_pin_reads_the_real_pin_file(mod: ModuleType) -> None:
    pin = mod.parse_pin(_real_text())
    assert mod._TAG.match(pin.tag)
    assert mod._ASSET.match(pin.asset)
    assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)


def test_parse_pin_rejects_missing_or_duplicate_key(mod: ModuleType) -> None:
    text = _real_text()
    without_sha = re.sub(r"^.*FFMPEG_SHA256:.*\n", "", text, flags=re.MULTILINE)
    with pytest.raises(mod.PinError, match="FFMPEG_SHA256"):
        mod.parse_pin(without_sha)
    with pytest.raises(mod.PinError, match="FFMPEG_TAG"):
        mod.parse_pin(text + "      FFMPEG_TAG: autobuild-2026-09-30-12-59\n")


def test_render_pin_round_trip_is_identity(mod: ModuleType) -> None:
    text = _real_text()
    assert mod.render_pin(text, mod.parse_pin(text)) == text


def test_render_pin_round_trip_preserves_crlf(mod: ModuleType) -> None:
    text = _real_text().replace("\n", "\r\n")
    assert mod.render_pin(text, mod.parse_pin(text)) == text


def test_render_pin_changes_only_the_three_values(mod: ModuleType) -> None:
    text = _real_text()
    new = mod.Pin("autobuild-2026-11-30-12-59", _ASSET_10, _SHA_B)
    rendered = mod.render_pin(text, new)
    diff = list(difflib.unified_diff(text.splitlines(), rendered.splitlines(), lineterm=""))
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    assert len(removed) == len(added) == 3
    for old, fresh in zip(removed, added, strict=True):
        assert old[: len(old) - len(old.lstrip())] == fresh[: len(fresh) - len(fresh.lstrip())]
    assert mod.parse_pin(rendered) == new


def test_render_pin_keeps_odd_whitespace_and_ignores_lookalike_lines(mod: ModuleType) -> None:
    def build(tag: str, asset: str, sha: str) -> str:
        return (
            "# FFMPEG_TAG: autobuild-2020-01-31-00-00\r\n"
            f"\t FFMPEG_TAG:   {tag} \t\r\n"
            f"  FFMPEG_ASSET: {asset}\n"
            f"  FFMPEG_SHA256:\t{sha}   \n"
            "  OTHER: FFMPEG_TAG: autobuild-2020-01-31-00-00\n"
        )

    old = build(_TAG_PIN, _ASSET_9, _SHA_A)
    assert mod.parse_pin(old) == mod.Pin(_TAG_PIN, _ASSET_9, _SHA_A)
    new = mod.Pin("autobuild-2026-11-30-12-59", _ASSET_10, _SHA_B)
    assert mod.render_pin(old, new) == build(new.tag, new.asset, new.sha256)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("autobuild-2026-10-31-12-59", date(2026, 10, 31)),
        ("autobuild-2026-10-30-12-59", None),
        ("autobuild-2024-02-29-12-59", date(2024, 2, 29)),
        ("autobuild-2025-02-28-12-59", date(2025, 2, 28)),
        ("autobuild-2025-02-30-12-59", None),
        ("latest", None),
        ("autobuild-2026-10-31", None),
    ],
    ids=["oct-end", "oct-30", "leap-end", "non-leap-end", "impossible-date", "latest", "no-time"],
)
def test_month_end_date(mod: ModuleType, tag: str, expected: date | None) -> None:
    assert mod.month_end_date(tag) == expected


def test_branch_assets_filters_and_orders_numerically(mod: ModuleType) -> None:
    names = [
        "ffmpeg-master-latest-win64-gpl.zip",
        "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-shared-9.0.zip",
        "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-9.0.zip",
        "ffmpeg-n9.0.2-39-g7adfbe40dc-linux64-gpl-9.0.zip",
        _ASSET_9,
        _ASSET_10,
    ]
    found = mod.branch_assets(names)
    assert set(found) == {(9, 0), (10, 0)}
    assert max(found) == (10, 0)


def test_parse_checksums_formats(mod: ModuleType) -> None:
    text = f"{'AB' * 32}  one.zip\n{'cd' * 32} *two.zip\nnot a checksum line\n\n"
    assert mod.parse_checksums(text) == {"one.zip": "ab" * 32, "two.zip": "cd" * 32}


# --- decide -----------------------------------------------------------------------------------


def test_decide_up_to_date(mod: ModuleType) -> None:
    decision = mod.decide(_pin(mod), [_release(_TAG_PIN, _ASSET_9)], _checksums_for())
    assert decision.status == "current"
    assert decision.candidate is None
    assert decision.notices == []


def test_decide_newer_month_end_same_branch(mod: ModuleType) -> None:
    newer = "ffmpeg-n9.0.3-1-g1234567890-win64-gpl-9.0.zip"
    releases = [_release("autobuild-2026-11-30-12-59", newer)]
    decision = mod.decide(_pin(mod), releases, _checksums_for((newer, _SHA_B)))
    assert decision.status == "patch"
    assert decision.candidate == mod.Pin("autobuild-2026-11-30-12-59", newer, _SHA_B)


def test_decide_picks_newest_month_end_regardless_of_list_order(mod: ModuleType) -> None:
    oldest = "ffmpeg-n9.0.1-1-g1111111111-win64-gpl-9.0.zip"
    newest = "ffmpeg-n9.0.3-1-g3333333333-win64-gpl-9.0.zip"
    releases = [
        _release("autobuild-2026-10-31-12-59", _ASSET_9),
        _release("autobuild-2026-12-31-12-59", newest),
        _release("autobuild-2026-09-30-12-59", oldest),
    ]
    decision = mod.decide(_pin(mod), releases, _checksums_for((newest, _SHA_B)))
    assert decision.candidate.tag == "autobuild-2026-12-31-12-59"


def test_decide_ignores_newer_dailies(mod: ModuleType) -> None:
    releases = [
        _release("latest", "ffmpeg-master-latest-win64-gpl.zip"),
        _release("autobuild-2026-11-05-12-59", _ASSET_9),
        _release(_TAG_PIN, _ASSET_9),
    ]
    assert mod.decide(_pin(mod), releases, _checksums_for()).status == "current"


def test_decide_new_branch_notice_alongside_patch(mod: ModuleType) -> None:
    newer = "ffmpeg-n9.0.3-1-g1234567890-win64-gpl-9.0.zip"
    releases = [_release("autobuild-2026-11-30-12-59", newer, _ASSET_10)]
    decision = mod.decide(_pin(mod), releases, _checksums_for((newer, _SHA_B)))
    assert decision.status == "patch"
    assert decision.candidate.asset == newer
    assert [n.title for n in decision.notices] == [
        "FFmpeg: release branch 10.0 is available (pinned: 9.0)",
    ]


def test_decide_pinned_branch_missing(mod: ModuleType) -> None:
    releases = [_release("autobuild-2026-11-30-12-59", _ASSET_10)]
    decision = mod.decide(_pin(mod), releases, _checksums_for())
    assert decision.status == "current"
    assert "FFmpeg: BtbN no longer builds pinned branch 9.0" in [n.title for n in decision.notices]


def test_decide_branch_override(mod: ModuleType) -> None:
    releases = [_release(_TAG_PIN, _ASSET_9, _ASSET_10)]
    decision = mod.decide(_pin(mod), releases, _checksums_for((_ASSET_10, _SHA_B)), (10, 0))
    assert decision.status == "patch"
    assert decision.candidate.asset == _ASSET_10


def test_decide_branch_override_absent_branch_is_an_error(mod: ModuleType) -> None:
    releases = [_release(_TAG_PIN, _ASSET_9)]
    with pytest.raises(mod.PinError, match=r"win64-gpl-10\.0"):
        mod.decide(_pin(mod), releases, _checksums_for(), (10, 0))


def test_decide_asset_missing_from_checksums(mod: ModuleType) -> None:
    newer = "ffmpeg-n9.0.3-1-g1234567890-win64-gpl-9.0.zip"
    releases = [_release("autobuild-2026-11-30-12-59", newer)]
    with pytest.raises(mod.PinError, match="missing"):
        mod.decide(_pin(mod), releases, _checksums_for(("other.zip", _SHA_B)))


def test_decide_no_month_end_release(mod: ModuleType) -> None:
    releases = [
        _release("latest", "ffmpeg-master-latest-win64-gpl.zip"),
        _release("autobuild-2026-11-05-12-59", _ASSET_9),
    ]
    with pytest.raises(mod.PinError, match="month-end"):
        mod.decide(_pin(mod), releases, _checksums_for())


# --- fetch ------------------------------------------------------------------------------------


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _serve(monkeypatch: pytest.MonkeyPatch, mod: ModuleType, data: bytes) -> Any:
    monkeypatch.setattr(mod, "_get", lambda _url: data)
    return mod.Pin(_TAG_PIN, _ASSET_9, hashlib.sha256(data).hexdigest())


def test_fetch_rejects_hash_mismatch(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _zip_bytes({"x/bin/ffmpeg.exe": b"a", "x/bin/ffprobe.exe": b"b"})
    monkeypatch.setattr(mod, "_get", lambda _url: data)
    actual = hashlib.sha256(data).hexdigest()
    dest = tmp_path / "dest"
    with pytest.raises(mod.PinError) as caught:
        mod.fetch(mod.Pin(_TAG_PIN, _ASSET_9, _SHA_B), dest)
    assert _SHA_B in str(caught.value)
    assert actual in str(caught.value)
    assert not dest.exists() or not list(dest.iterdir())


def test_fetch_extracts_only_the_two_binaries(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(
        monkeypatch,
        mod,
        _zip_bytes(
            {"x/bin/ffmpeg.exe": b"FF", "x/bin/ffprobe.exe": b"PR", "x/doc/readme.txt": b"hi"},
        ),
    )
    mod.fetch(pin, tmp_path / "dest")
    found = {p.name: p.read_bytes() for p in (tmp_path / "dest").iterdir()}
    assert found == {"ffmpeg.exe": b"FF", "ffprobe.exe": b"PR"}


def test_fetch_member_path_cannot_escape_dest(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(
        monkeypatch,
        mod,
        _zip_bytes({"../../evil/bin/ffmpeg.exe": b"EVIL", "x/bin/ffprobe.exe": b"PR"}),
    )
    dest = tmp_path / "a" / "b" / "dest"
    mod.fetch(pin, dest)
    assert (dest / "ffmpeg.exe").read_bytes() == b"EVIL"
    written = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {
        Path("a/b/dest/ffmpeg.exe"),
        Path("a/b/dest/ffprobe.exe"),
    }


@pytest.mark.parametrize(
    "members",
    [
        {"x/bin/ffmpeg.exe": b"a"},
        {"x/bin/ffmpeg.exe": b"a", "y/bin/ffmpeg.exe": b"b", "x/bin/ffprobe.exe": b"c"},
    ],
    ids=["missing-ffprobe", "two-ffmpeg"],
)
def test_fetch_requires_exactly_one_of_each(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, members: dict[str, bytes]
) -> None:
    pin = _serve(monkeypatch, mod, _zip_bytes(members))
    dest = tmp_path / "dest"
    with pytest.raises(mod.PinError, match="exactly one"):
        mod.fetch(pin, dest)
    assert not dest.exists()


def test_fetch_rejects_a_non_zip_body(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(monkeypatch, mod, b"this is not a zip")
    with pytest.raises(mod.PinError, match="not a valid zip"):
        mod.fetch(pin, tmp_path / "dest")


def test_fetch_replaces_stale_binaries_but_a_failed_fetch_leaves_them(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (dest / name).write_bytes(b"OLD")
    data = _zip_bytes({"x/bin/ffmpeg.exe": b"NEW-FF", "x/bin/ffprobe.exe": b"NEW-PR"})
    pin = _serve(monkeypatch, mod, data)

    with pytest.raises(mod.PinError, match="mismatch"):
        mod.fetch(mod.Pin(pin.tag, pin.asset, _SHA_B), dest)
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == {
        "ffmpeg.exe": b"OLD",
        "ffprobe.exe": b"OLD",
    }

    mod.fetch(pin, dest)
    assert (dest / "ffmpeg.exe").read_bytes() == b"NEW-FF"
    assert (dest / "ffprobe.exe").read_bytes() == b"NEW-PR"


_BINARY_MEMBERS = {"x/bin/ffmpeg.exe": b"FF", "x/bin/ffprobe.exe": b"PR"}


def test_fetch_license_dest_receives_only_the_top_level_license(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(
        monkeypatch,
        mod,
        _zip_bytes({**_BINARY_MEMBERS, "x/LICENSE.txt": b"GPL", "x/doc/LICENSE.txt": b"nested"}),
    )
    mod.fetch(pin, tmp_path / "dest", tmp_path / "lic")
    assert {p.name: p.read_bytes() for p in (tmp_path / "lic").iterdir()} == {"LICENSE.txt": b"GPL"}
    assert {p.name for p in (tmp_path / "dest").iterdir()} == {"ffmpeg.exe", "ffprobe.exe"}


def test_fetch_without_license_dest_ignores_a_missing_license(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(monkeypatch, mod, _zip_bytes(_BINARY_MEMBERS))
    mod.fetch(pin, tmp_path / "dest")
    assert {p.name for p in (tmp_path / "dest").iterdir()} == {"ffmpeg.exe", "ffprobe.exe"}


def test_fetch_license_dest_requires_exactly_one_license_and_stages_nothing(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(monkeypatch, mod, _zip_bytes(_BINARY_MEMBERS))
    with pytest.raises(mod.PinError, match=r"top-level LICENSE\.txt"):
        mod.fetch(pin, tmp_path / "dest", tmp_path / "lic")
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "lic").exists()


def test_fetch_license_dest_rejects_two_top_level_licenses(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pin = _serve(
        monkeypatch,
        mod,
        _zip_bytes({**_BINARY_MEMBERS, "x/LICENSE.txt": b"one", "y/LICENSE.txt": b"two"}),
    )
    with pytest.raises(mod.PinError, match=r"exactly one top-level LICENSE\.txt \(found 2\)"):
        mod.fetch(pin, tmp_path / "dest", tmp_path / "lic")
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "lic").exists()


def test_fetch_command_license_dest_flag_reaches_fetch(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _zip_bytes({**_BINARY_MEMBERS, "x/LICENSE.txt": b"GPL"})
    monkeypatch.setattr(mod, "_get", lambda _url: data)
    pin_file = tmp_path / "ffmpeg-pin.yml"
    pin = mod.Pin(_TAG_PIN, _ASSET_9, hashlib.sha256(data).hexdigest())
    pin_file.write_text(mod.render_pin(_real_text(), pin), encoding="utf-8", newline="")
    argv = ["--pin-file", str(pin_file), "fetch", "--dest", str(tmp_path / "d")]
    assert mod.main([*argv, "--license-dest", str(tmp_path / "lic")]) == 0
    assert (tmp_path / "lic" / "LICENSE.txt").read_bytes() == b"GPL"


# --- _get -------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_get_refuses_non_https(mod: ModuleType) -> None:
    with pytest.raises(mod.PinError, match="non-HTTPS"):
        mod._get("http://example.invalid/x")


def test_get_sends_token_only_when_set(mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        seen.append(request)
        return _FakeResponse(b"ok")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert mod._get("https://example.invalid/a") == b"ok"
    monkeypatch.setenv("GITHUB_TOKEN", "s3cret-token")
    mod._get("https://example.invalid/b")
    assert seen[0].get_header("Authorization") is None
    assert seen[1].get_header("Authorization") == "Bearer s3cret-token"


def test_get_errors_never_leak_the_token(mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "s3cret-token")

    def boom(request: Any, timeout: float) -> None:
        # An explicit body avoids HTTPError allocating a temp file that warns at teardown.
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "rate limited",
            Message(),
            io.BytesIO(b""),
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(mod.PinError, match="HTTP 403") as caught:
        mod._get("https://example.invalid/a")
    assert "s3cret-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("unreachable"), TimeoutError("timed out")],
    ids=["urlerror", "timeout"],
)
def test_get_transport_failures_become_pin_errors(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "s3cret-token")

    def boom(_request: Any, timeout: float) -> None:
        raise failure

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with pytest.raises(mod.PinError, match="failed") as caught:
        mod._get("https://example.invalid/a")
    assert "s3cret-token" not in str(caught.value)
    assert caught.value.__cause__ is None


# --- CLI --------------------------------------------------------------------------------------


def _decision(mod: ModuleType, *, patch: bool, notices: list[Any]) -> Any:
    candidate = mod.Pin("autobuild-2026-11-30-12-59", _ASSET_10, _SHA_B) if patch else None
    return mod.Decision("patch" if patch else "current", candidate, notices)


def _read_outputs(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_check_writes_github_output(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(mod, "_get", lambda _url: b"[]")
    notice = mod.Notice("a title", "a body")
    monkeypatch.setattr(
        mod, "decide", lambda *_a, **_k: _decision(mod, patch=True, notices=[notice])
    )

    assert mod.main(["check"]) == 0

    values = _read_outputs(out)
    assert set(values) == {
        "status",
        "tag",
        "asset",
        "sha256",
        "version",
        "current_version",
        "notices",
    }
    assert values["status"] == "patch"
    assert values["tag"] == "autobuild-2026-11-30-12-59"
    assert values["version"] == "n10.0.1-5-gabcdef1234"
    assert json.loads(values["notices"]) == [{"title": "a title", "body": "a body"}]


def test_check_writes_empty_notices_and_candidate_when_current(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(mod, "_get", lambda _url: b"[]")
    monkeypatch.setattr(mod, "decide", lambda *_a, **_k: _decision(mod, patch=False, notices=[]))

    assert mod.main(["check"]) == 0

    values = _read_outputs(out)
    assert values["notices"] == "[]"
    assert (values["tag"], values["asset"], values["sha256"], values["version"]) == ("",) * 4


def test_apply_reads_env_and_validates(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "ffmpeg-pin.yml"
    target.write_text(_real_text(), encoding="utf-8", newline="")
    monkeypatch.setenv("FFMPEG_TAG", "autobuild-2026-11-30-12-59")
    monkeypatch.setenv("FFMPEG_ASSET", _ASSET_10)
    monkeypatch.setenv("FFMPEG_SHA256", _SHA_B)

    assert mod.main(["--pin-file", str(target), "apply"]) == 0
    assert mod.parse_pin(target.read_text(encoding="utf-8")) == mod.Pin(
        "autobuild-2026-11-30-12-59", _ASSET_10, _SHA_B
    )

    before = target.read_text(encoding="utf-8")
    monkeypatch.setenv("FFMPEG_SHA256", "b" * 63)
    capsys.readouterr()
    assert mod.main(["--pin-file", str(target), "apply"]) == 1
    assert "error:" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>not json", "invalid JSON"),
        (b"\xff\xfe\x00", "invalid JSON"),
        (b'{"message": "API rate limit exceeded"}', "did not return a list"),
    ],
    ids=["text", "bad-utf8", "object"],
)
def test_fetch_releases_rejects_a_bad_payload(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, body: bytes, message: str
) -> None:
    monkeypatch.setattr(mod, "_get", lambda _url: body)
    with pytest.raises(mod.PinError, match=message):
        mod._fetch_releases()


def test_fetch_releases_drops_non_object_entries(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release("autobuild-2026-10-31-12-59", _ASSET_9)
    monkeypatch.setattr(mod, "_get", lambda _url: json.dumps([1, "x", None, release]).encode())
    assert mod._fetch_releases() == [release]


def test_release_checksums_downloads_the_checksums_asset(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release("autobuild-2026-10-31-12-59", "notes.txt", "checksums.sha256")
    urls: list[str] = []

    def fake_get(url: str) -> bytes:
        urls.append(url)
        return b"abc  file.zip\n"

    monkeypatch.setattr(mod, "_get", fake_get)
    assert mod._release_checksums(release) == "abc  file.zip\n"
    assert urls == ["https://example.invalid/autobuild-2026-10-31-12-59/checksums.sha256"]


def test_release_checksums_missing_asset_is_an_error(mod: ModuleType) -> None:
    with pytest.raises(mod.PinError, match=r"checksums\.sha256"):
        mod._release_checksums(_release("autobuild-2026-10-31-12-59", _ASSET_9))


@pytest.mark.parametrize(
    ("argv", "expected"),
    [(["check"], None), (["check", "--branch", "10.0"], (10, 0))],
    ids=["default", "explicit"],
)
def test_check_passes_the_parsed_branch_to_decide(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected: tuple[int, int] | None,
) -> None:
    seen: list[Any] = []

    def fake_decide(_pin: Any, _releases: Any, checksums: Any, branch: Any = None) -> Any:
        seen.append((checksums, branch))
        return _decision(mod, patch=False, notices=[])

    monkeypatch.setattr(mod, "_get", lambda _url: b"[]")
    monkeypatch.setattr(mod, "decide", fake_decide)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert mod.main(argv) == 0
    assert seen == [(mod._release_checksums, expected)]


@pytest.mark.parametrize("branch", ["9", "9.0.1", "x.y", "9.", "-1.0"])
def test_check_rejects_a_malformed_branch_with_usage_exit(mod: ModuleType, branch: str) -> None:
    with pytest.raises(SystemExit) as caught:
        mod.main(["check", "--branch", branch])
    assert caught.value.code == 2


def test_missing_pin_file_is_a_reported_error(
    mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mod.main(["--pin-file", str(tmp_path / "nope.yml"), "check"]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_fetch_command_downloads_the_pinned_url_and_reports_failure(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = _zip_bytes({"x/bin/ffmpeg.exe": b"FF", "x/bin/ffprobe.exe": b"PR"})
    urls: list[str] = []

    def fake_get(url: str) -> bytes:
        urls.append(url)
        return data

    monkeypatch.setattr(mod, "_get", fake_get)
    pin_file = tmp_path / "ffmpeg-pin.yml"
    good = mod.Pin(_TAG_PIN, _ASSET_9, hashlib.sha256(data).hexdigest())
    pin_file.write_text(mod.render_pin(_real_text(), good), encoding="utf-8", newline="")
    dest = tmp_path / "dest"

    assert mod.main(["--pin-file", str(pin_file), "fetch", "--dest", str(dest)]) == 0
    assert urls == [
        f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{_TAG_PIN}/{_ASSET_9}"
    ]
    assert (dest / "ffmpeg.exe").read_bytes() == b"FF"

    pin_file.write_text(
        mod.render_pin(_real_text(), mod.Pin(_TAG_PIN, _ASSET_9, _SHA_B)),
        encoding="utf-8",
        newline="",
    )
    capsys.readouterr()
    bad_dest = tmp_path / "bad_dest"
    assert mod.main(["--pin-file", str(pin_file), "fetch", "--dest", str(bad_dest)]) == 1
    assert "error: FFmpeg SHA256 mismatch" in capsys.readouterr().err
    assert not bad_dest.exists()


def test_apply_without_env_is_an_error(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("FFMPEG_TAG", "FFMPEG_ASSET", "FFMPEG_SHA256"):
        monkeypatch.delenv(key, raising=False)
    assert mod.main(["apply"]) == 1
    assert "FFMPEG_TAG" in capsys.readouterr().err


def test_pr_body_names_versions_and_links_the_run(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FFMPEG_TAG", "autobuild-2026-11-30-12-59")
    monkeypatch.setenv("FFMPEG_ASSET", _ASSET_10)
    monkeypatch.setenv("FFMPEG_SHA256", _SHA_B)
    monkeypatch.setenv("FFMPEG_CURRENT_VERSION", "n9.0.2-39-g7adfbe40dc")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")

    assert mod.main(["pr-body"]) == 0

    body = capsys.readouterr().out
    assert "n9.0.2-39-g7adfbe40dc" in body
    assert "n10.0.1-5-gabcdef1234" in body
    assert "https://github.com/owner/repo/actions/runs/12345" in body
    assert "ci.yml" in body


# --- pin file location and strict validation --------------------------------------------------


def test_default_pin_file_is_outside_the_workflows_directory(mod: ModuleType) -> None:
    """The bump bot pushes this file with GITHUB_TOKEN, which may not touch workflow files."""
    assert Path(mod._DEFAULT_PIN_FILE) == _REAL_PIN_FILE
    assert _REPO_ROOT / ".github" / "workflows" not in Path(mod._DEFAULT_PIN_FILE).parents


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag", _TAG_PIN + "\n"),
        ("asset", _ASSET_9 + "\n"),
        ("sha256", _SHA_A + "\n"),
    ],
    ids=["tag", "asset", "sha256"],
)
def test_render_pin_rejects_values_with_a_trailing_newline(
    mod: ModuleType, field: str, value: str
) -> None:
    """`$` would accept a trailing newline and inject a blank line into the pin file."""
    values = {"tag": _TAG_PIN, "asset": _ASSET_9, "sha256": _SHA_A, field: value}
    with pytest.raises(mod.PinError):
        mod.render_pin(_real_text(), mod.Pin(**values))


def test_decide_branch_override_does_not_announce_the_chosen_branch(mod: ModuleType) -> None:
    """Targeting 10.0 explicitly must not also file "10.0 is available"; 11.0 still would."""
    asset_11 = "ffmpeg-n11.0.0-1-g1111111111-win64-gpl-11.0.zip"
    releases = [_release(_TAG_PIN, _ASSET_9, _ASSET_10)]
    chosen = mod.decide(_pin(mod), releases, _checksums_for((_ASSET_10, _SHA_B)), (10, 0))
    assert chosen.notices == []
    releases = [_release(_TAG_PIN, _ASSET_9, _ASSET_10, asset_11)]
    beyond = mod.decide(_pin(mod), releases, _checksums_for((_ASSET_10, _SHA_B)), (10, 0))
    assert [n.title for n in beyond.notices] == [
        "FFmpeg: release branch 11.0 is available (pinned: 9.0)",
    ]

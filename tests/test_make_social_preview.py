"""
Tests for scripts/make_social_preview.py.

scripts/ is not an importable package (see the ``import_script`` fixture in
conftest.py), so every test here loads the module fresh from its file path.
Font loading is monkeypatched via the shared ``fake_truetype_factory`` fixture
(tests/conftest.py) so the suite stays deterministic across machines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PIL import Image, ImageFont, UnidentifiedImageError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType


@pytest.fixture
def social_preview(import_script: Callable[[str], ModuleType]) -> ModuleType:
    """Load and return the make_social_preview module, freshly."""
    return import_script("make_social_preview.py")


@pytest.fixture(autouse=True)
def _default_font_branch(
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_truetype_factory: Callable[..., Callable[..., ImageFont.FreeTypeFont]],
) -> None:
    """
    Force the load_default branch for every test in this module by default.

    ``_load_font``'s own fallback chain gets its own explicit tests below, each
    overriding this with its own ``monkeypatch.setattr`` call; every other test here is
    exercising ``render_social_preview``'s layout/IO behaviour, orthogonal to which font
    branch wins, so pinning one branch keeps them deterministic across machines.
    """
    fake = fake_truetype_factory(
        blocked=frozenset(
            {"consola.ttf", "consolab.ttf", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"}
        )
    )
    monkeypatch.setattr(social_preview.ImageFont, "truetype", fake)


def _make_icon(path: Path, size: tuple[int, int]) -> Path:
    """Write a solid-color RGBA PNG of the given (width, height) and return its path."""
    Image.new("RGBA", size, (127, 166, 80, 255)).save(path)
    return path


# ---------------------------------------------------------------------------
# _load_font -- font-candidate fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bold", [False, True])
def test_load_font_uses_first_available_candidate(
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_truetype_factory: Callable[..., Callable[..., ImageFont.FreeTypeFont]],
    bold: bool,
) -> None:
    """Test the happy path: the first candidate family's matching face loads directly."""
    calls: list[str] = []
    real_fake = fake_truetype_factory()

    def _tracking(name: str, size: int, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_fake(name, size, *args, **kwargs)

    monkeypatch.setattr(social_preview.ImageFont, "truetype", _tracking)

    font = social_preview._load_font(30, bold=bold)

    assert isinstance(font, ImageFont.FreeTypeFont)
    assert calls == (["consolab.ttf"] if bold else ["consola.ttf"])


def test_load_font_bold_skips_straight_to_next_family_when_bold_face_missing(
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_truetype_factory: Callable[..., Callable[..., ImageFont.FreeTypeFont]],
) -> None:
    """
    Document a real behavioural boundary: no per-family bold-to-regular fallback.

    ``_load_font`` tries only the bold name for each candidate family in turn, so a
    missing ``consolab.ttf`` skips the entire consola family (even though ``consola.ttf``
    itself would load fine) and moves straight to ``DejaVuSansMono-Bold.ttf``.
    """
    calls: list[str] = []
    real_fake = fake_truetype_factory(blocked=frozenset({"consolab.ttf"}))

    def _tracking(name: str, size: int, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_fake(name, size, *args, **kwargs)

    monkeypatch.setattr(social_preview.ImageFont, "truetype", _tracking)

    font = social_preview._load_font(30, bold=True)

    assert isinstance(font, ImageFont.FreeTypeFont)
    assert calls == ["consolab.ttf", "DejaVuSansMono-Bold.ttf"]
    assert "consola.ttf" not in calls


def test_load_font_falls_back_to_second_candidate_family(
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_truetype_factory: Callable[..., Callable[..., ImageFont.FreeTypeFont]],
) -> None:
    """Test that when consola is entirely unavailable, DejaVuSansMono is tried and used."""
    calls: list[str] = []
    real_fake = fake_truetype_factory(blocked=frozenset({"consola.ttf"}))

    def _tracking(name: str, size: int, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return real_fake(name, size, *args, **kwargs)

    monkeypatch.setattr(social_preview.ImageFont, "truetype", _tracking)

    font = social_preview._load_font(30, bold=False)

    assert isinstance(font, ImageFont.FreeTypeFont)
    assert calls == ["consola.ttf", "DejaVuSansMono.ttf"]


def test_load_font_falls_back_to_load_default_when_every_candidate_missing(
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_truetype_factory: Callable[..., Callable[..., ImageFont.FreeTypeFont]],
) -> None:
    """Test the final fallback: every candidate family missing lands on load_default."""
    fake = fake_truetype_factory(
        blocked=frozenset(
            {"consola.ttf", "consolab.ttf", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"}
        )
    )
    monkeypatch.setattr(social_preview.ImageFont, "truetype", fake)

    font = social_preview._load_font(30, bold=False)

    assert font is not None


@pytest.mark.parametrize("size", [0, -1, -10])
def test_load_font_non_positive_size_raises_valueerror(
    social_preview: ModuleType, size: int
) -> None:
    """Document a boundary defect matching the sibling loader's: size<=0 is never validated."""
    with pytest.raises(ValueError, match="font size must be greater than 0"):
        social_preview._load_font(size, bold=False)


# ---------------------------------------------------------------------------
# render_social_preview -- output shape is always exactly 1280x640
# ---------------------------------------------------------------------------


def test_render_social_preview_uses_the_real_repo_icon_by_default(
    tmp_path: Path, social_preview: ModuleType
) -> None:
    """Test the default (no icon_source override) path against the actual tracked icon."""
    output_path = tmp_path / "preview.png"

    result = social_preview.render_social_preview(output_path)

    assert result == output_path
    with Image.open(result) as img:
        img.load()
        assert img.mode == "RGB"
        assert img.size == (social_preview.WIDTH, social_preview.HEIGHT)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        pytest.param(1024, 1024, id="square"),
        pytest.param(2000, 100, id="wide_rectangle"),
        pytest.param(100, 2000, id="tall_rectangle"),
        pytest.param(1, 1, id="tiny_1x1"),
        pytest.param(3000, 3000, id="larger_than_icon_size"),
    ],
)
def test_render_social_preview_canvas_is_always_1280x640_regardless_of_icon_shape(
    tmp_path: Path, social_preview: ModuleType, width: int, height: int
) -> None:
    """
    Test the layout boundary: any icon aspect ratio, same output canvas.

    ``render_social_preview`` force-resizes the icon to a fixed ICON_SIZE square before
    pasting, so the final canvas size must never depend on the source icon's shape --
    including degenerate 1x1 icons and icons already larger than the composited target.
    """
    icon_path = _make_icon(tmp_path / "icon.png", (width, height))
    output_path = tmp_path / "preview.png"

    result = social_preview.render_social_preview(output_path, icon_source=icon_path)

    with Image.open(result) as img:
        img.load()
        assert img.mode == "RGB"
        assert img.size == (social_preview.WIDTH, social_preview.HEIGHT)


def test_render_social_preview_missing_icon_raises_file_not_found(
    tmp_path: Path, social_preview: ModuleType
) -> None:
    """Test the environment boundary: a deleted/never-generated icon source fails loudly."""
    missing = tmp_path / "does-not-exist.png"

    with pytest.raises(FileNotFoundError):
        social_preview.render_social_preview(tmp_path / "preview.png", icon_source=missing)


def test_render_social_preview_corrupt_icon_raises(
    tmp_path: Path, social_preview: ModuleType
) -> None:
    """Test the malformed-data boundary: a non-image file at icon_source fails loudly."""
    bad_icon = tmp_path / "not-an-image.png"
    bad_icon.write_bytes(b"this is not png data")

    with pytest.raises(UnidentifiedImageError):
        social_preview.render_social_preview(tmp_path / "preview.png", icon_source=bad_icon)


def test_render_social_preview_creates_missing_output_parent_directories(
    tmp_path: Path, social_preview: ModuleType
) -> None:
    """Test that a nested, not-yet-existing output directory is created."""
    icon_path = _make_icon(tmp_path / "icon.png", (1024, 1024))
    output_path = tmp_path / "nested" / "dir" / "out.png"

    result = social_preview.render_social_preview(output_path, icon_source=icon_path)

    assert result.is_file()


def test_render_social_preview_overwrites_an_existing_output_file(
    tmp_path: Path, social_preview: ModuleType
) -> None:
    """Test the repeated-call boundary: re-running against the same output path succeeds."""
    icon_path = _make_icon(tmp_path / "icon.png", (1024, 1024))
    output_path = tmp_path / "preview.png"
    output_path.write_bytes(b"stale placeholder content")

    result = social_preview.render_social_preview(output_path, icon_source=icon_path)

    with Image.open(result) as img:
        img.load()
        assert img.size == (social_preview.WIDTH, social_preview.HEIGHT)


# ---------------------------------------------------------------------------
# main -- CLI wiring
# ---------------------------------------------------------------------------


def test_main_writes_to_the_explicit_output_argument(
    tmp_path: Path,
    social_preview: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Test that --output is honored end to end and printed, never the real docs/ default.

    Deliberately never invokes main() without --output: the argparse default writes into
    this repo's own docs/ directory, which this suite must not touch.
    """
    output_path = tmp_path / "cli-preview.png"
    monkeypatch.setattr("sys.argv", ["make_social_preview.py", "--output", str(output_path)])

    social_preview.main()

    captured = capsys.readouterr()
    assert str(output_path) in captured.out
    assert "1280x640" in captured.out
    with Image.open(output_path) as img:
        img.load()
        assert img.size == (1280, 640)

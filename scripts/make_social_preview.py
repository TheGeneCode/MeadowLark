"""
Generate docs/social-preview.png: GitHub's "Social preview" card image.

GitHub requires exactly 1280x640 and has no API to upload it -- it must be attached by
hand at https://github.com/TheGeneCode/MeadowLark/settings under Social preview.
docs/screenshot.png is the wrong aspect ratio and would letterbox badly there, so this
composites the app icon and wordmark onto a dedicated 1280x640 canvas instead of reusing it.
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

WIDTH: Final = 1280
HEIGHT: Final = 640
ICON_SIZE: Final = 320
TITLE_FONT_SIZE: Final = 96
TAGLINE_FONT_SIZE: Final = 32

BACKGROUND: Final = "#1B1F17"
FOREGROUND: Final = "#EDE6D6"
ACCENT: Final = "#7FA650"
DIM: Final = "#8A9280"

TITLE: Final = "meadowlark"
TAGLINE: Final = "Drag a URL, save the file"

_ROOT: Final = Path(__file__).resolve().parent.parent
_ICON_SOURCE: Final = _ROOT / "resources" / "icons" / "meadowlark.png"

_FONT_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("consola.ttf", "consolab.ttf"),
    ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
)


def _load_font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont:
    for regular_name, bold_name in _FONT_CANDIDATES:
        name = bold_name if bold else regular_name
        with contextlib.suppress(OSError):
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def render_social_preview(output_path: Path, *, icon_source: Path = _ICON_SOURCE) -> Path:
    """Render the 1280x640 social preview card and save it to output_path."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)

    icon = Image.open(icon_source).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    icon_x = 140
    icon_y = (HEIGHT - ICON_SIZE) // 2
    image.paste(icon, (icon_x, icon_y), icon)

    draw = ImageDraw.Draw(image)
    text_x = icon_x + ICON_SIZE + 70
    title_font = _load_font(TITLE_FONT_SIZE, bold=True)
    tagline_font = _load_font(TAGLINE_FONT_SIZE, bold=False)

    title_bbox = draw.textbbox((0, 0), TITLE, font=title_font)
    tagline_bbox = draw.textbbox((0, 0), TAGLINE, font=tagline_font)
    gap = 24
    block_height = (title_bbox[3] - title_bbox[1]) + gap + (tagline_bbox[3] - tagline_bbox[1])
    title_y = (HEIGHT - block_height) // 2 - title_bbox[1]
    tagline_y = title_y + (title_bbox[3] - title_bbox[1]) + gap - tagline_bbox[1]

    draw.text((text_x, title_y), TITLE, font=title_font, fill=FOREGROUND)
    draw.text((text_x, tagline_y), TAGLINE, font=tagline_font, fill=DIM)
    underline_y = title_y + title_bbox[3] + 12
    draw.rectangle((text_x, underline_y, text_x + 80, underline_y + 6), fill=ACCENT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/social-preview.png"),
        help="Where to write the rendered PNG (default: docs/social-preview.png)",
    )
    args = parser.parse_args()

    output_path = render_social_preview(args.output)
    with Image.open(output_path) as img:
        size = img.size
    print(f"{output_path} ({output_path.stat().st_size:,} bytes, {size[0]}x{size[1]})")


if __name__ == "__main__":
    main()

"""
Registry of the video resolution rungs the app can download and route.

The heights here are rendition-ladder rungs, not arbitrary numbers. YouTube (and
every other supported site) encodes each video into a fixed ladder of renditions
- 2160, 1440, 1080, 720, 480, 360 - and the format selector built by
``_build_video_format_selector`` in ``src/ydl_options.py`` matches ``height<=N``.
A value that falls between two rungs therefore resolves silently to the rung
below it: asking for 900 downloads 720 and reports nothing unusual. That is why
the app offers this fixed set of presets rather than a free-form number field.

Every per-resolution name the app needs - settings keys, playlist filenames,
drop-target routing keys, tile colors - is derived from this one module, so
adding a rung is a single tuple entry. Stdlib imports only: ``src/config.py``
imports this module, so importing anything from ``src/`` here would create a
circular import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class ResolutionPreset:
    """One rung of the rendition ladder and everything the app derives from it."""

    height: int
    label: str
    description: str
    color: str
    text_color: str
    playlist_filename: str


# Invariants - all four are load-bearing, do not "tidy" any of them:
#
# 1. Ordered strictly descending by height. That order is relied on three times:
#    the UI tile order, the settings row order, and the retry-ladder descent
#    order (see lower_heights below).
# 2. 1080 keeps playlist_filename="playlists.txt" and 720 keeps
#    "720playlists.txt". Those are the filenames already sitting on disk in
#    resources/playlists/ and in users' AppData. Regularizing them to
#    "1080playlists.txt" would point existing users at a fresh empty template.
# 3. The color values for 1080 and 720 are the exact hex already shipping in
#    meadowlark.pyw (the DropLabel constructions near lines 360 and 363).
#    Do not adjust them.
# 4. text_color is white for the four dark rungs and #1A1B2E for 480/360, whose
#    backgrounds are too light to carry white 32pt text.
RESOLUTION_PRESETS: Final[tuple[ResolutionPreset, ...]] = (
    ResolutionPreset(2160, "2160", "4K UHD - 3840x2160", "#23273D", "#FFFFFF", "2160playlists.txt"),
    ResolutionPreset(
        1440,
        "1440",
        'QHD - 2560x1440, often marketed as "2K"',
        "#333756",
        "#FFFFFF",
        "1440playlists.txt",
    ),
    ResolutionPreset(1080, "1080", "Full HD - 1920x1080", "#424769", "#FFFFFF", "playlists.txt"),
    ResolutionPreset(720, "720", "HD - 1280x720", "#7077A1", "#FFFFFF", "720playlists.txt"),
    ResolutionPreset(480, "480", "SD - 854x480", "#A0A6C7", "#1A1B2E", "480playlists.txt"),
    ResolutionPreset(360, "360", "Data saver - 640x360", "#CBCEE2", "#1A1B2E", "360playlists.txt"),
)

DEFAULT_ENABLED_HEIGHTS: Final[tuple[int, ...]] = (1080, 720)

_BY_HEIGHT: Final[dict[int, ResolutionPreset]] = {p.height: p for p in RESOLUTION_PRESETS}

# 1080 and 720 predate the registry. Their settings keys already exist in users'
# AppData .env and point at populated playlist files; renaming them would silently
# resolve to a fresh empty template and look like the app erased their playlists.
_LEGACY_PLAYLIST_FILE_KEYS: Final[dict[int, str]] = {
    1080: "VID_DL_PLAYLISTS_FILE",
    720: "VID_DL_PLAYLISTS_720_FILE",
}
_LEGACY_BUTTON_LABEL_KEYS: Final[dict[int, str]] = {
    1080: "VID_DL_LABEL_BTN_PLAYLISTS",
    720: "VID_DL_LABEL_BTN_720",
}

MAX_LADDER_DESCENT: Final[int] = 3

_PLAYLIST_SUFFIX: Final[str] = "playlists"


def all_heights() -> tuple[int, ...]:
    """
    List every registered rung, highest first.

    Returns:
        Registered heights in descending order.
    """
    return tuple(p.height for p in RESOLUTION_PRESETS)


def get_preset(height: int) -> ResolutionPreset | None:
    """
    Look up the preset for a height.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The matching ResolutionPreset, or None if the height is not registered.
    """
    return _BY_HEIGHT.get(height)


def source_key(height: int) -> str:
    """
    Build the drop-target routing key for a rung.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The bare-height source key, e.g. "1080".
    """
    return str(height)


def playlist_source_key(height: int) -> str:
    """
    Build the playlist routing key for a rung.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The playlist source key, e.g. "1080playlists".
    """
    return f"{height}{_PLAYLIST_SUFFIX}"


def height_from_source(source: str) -> int | None:
    """
    Recover the rung height from either form of source key.

    Handles the bare drop-target key ("1080") and the playlist key
    ("1080playlists"). Non-resolution sources such as "audio",
    "audio_playlists" and "Update" return None, and so do heights that are not
    registered here - an unregistered height has no preset, no playlist
    filename and no settings keys, so accepting it would only push the failure
    further downstream.

    Args:
        source: Source identifier to parse.

    Returns:
        The registered height, or None if source does not name one.
    """
    digits = source.removesuffix(_PLAYLIST_SUFFIX)
    try:
        height = int(digits)
    except ValueError:
        return None
    return height if height in _BY_HEIGHT else None


def drop_label_key(height: int) -> str:
    """
    Build the settings key holding a rung's drop-target caption.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The settings key, e.g. "VID_DL_LABEL_DROP_1080".
    """
    # No alias map needed here: the generated names already coincide with the
    # VID_DL_LABEL_DROP_1080 / VID_DL_LABEL_DROP_720 keys the app ships today.
    return f"VID_DL_LABEL_DROP_{height}"


def button_label_key(height: int) -> str:
    """
    Build the settings key holding a rung's playlist-button caption.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The settings key - the legacy name for 1080/720, otherwise
        "VID_DL_LABEL_BTN_<height>".
    """
    return _LEGACY_BUTTON_LABEL_KEYS.get(height, f"VID_DL_LABEL_BTN_{height}")


def playlist_file_key(height: int) -> str:
    """
    Build the settings key holding a rung's playlist file path.

    Args:
        height: Rendition-ladder height, e.g. 1080.

    Returns:
        The settings key - the legacy name for 1080/720, otherwise
        "VID_DL_PLAYLISTS_<height>_FILE".
    """
    return _LEGACY_PLAYLIST_FILE_KEYS.get(height, f"VID_DL_PLAYLISTS_{height}_FILE")


def parse_enabled_heights(raw: str | None) -> tuple[int, ...]:
    """
    Parse the comma-separated enabled-resolutions setting.

    Malformed and unregistered entries are dropped silently; a hand-edited .env
    should degrade rather than crash the app at import time.

    Args:
        raw: Raw setting value, e.g. "1080,720", or None when unset.

    Returns:
        De-duplicated registered heights in descending order, or
        DEFAULT_ENABLED_HEIGHTS when nothing usable was parsed.
    """
    heights: set[int] = set()
    for token in (raw or "").split(","):
        try:
            height = int(token.strip())
        except ValueError:
            continue
        if height in _BY_HEIGHT:
            heights.add(height)
    # Fall back rather than returning () - an app with zero drop targets has no
    # UI left to fix itself with, so the user could not recover from the empty
    # state without hand-editing the .env again.
    if not heights:
        return DEFAULT_ENABLED_HEIGHTS
    return tuple(sorted(heights, reverse=True))


def format_enabled_heights(heights: Iterable[int]) -> str:
    """
    Render heights back into the comma-separated setting value.

    Args:
        heights: Heights to serialize, in any order and possibly duplicated.

    Returns:
        Comma-separated heights in descending order, e.g. "1080,720".
    """
    return ",".join(str(h) for h in sorted(set(heights), reverse=True))


def lower_heights(height: int, enabled: Iterable[int] | None = None) -> tuple[int, ...]:
    """
    List the rungs below a height, for retry-ladder descent.

    Args:
        height: Rendition-ladder height to descend from.
        enabled: Optional restriction to the rungs the user has turned on.

    Returns:
        Registered heights strictly below height, in descending order.
    """
    below = tuple(h for h in all_heights() if h < height)
    if enabled is None:
        return below
    enabled_set = set(enabled)
    restricted = tuple(h for h in below if h in enabled_set)
    # A user who enabled only 2160 still deserves a working retry ladder, so an
    # empty restriction falls back to every registered rung below the height
    # rather than leaving the descent with nowhere to go.
    return restricted or below

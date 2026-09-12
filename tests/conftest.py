"""Shared fixtures for the MeadowLark test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

# Mirrors QYT.py: load .env before any test module imports src.config (or anything
# that imports it) at module scope. Without this, whichever test file happens to be
# collected first freezes config's os.getenv-backed constants (e.g. VIDEO_STORAGE_DIR)
# to their hardcoded defaults instead of the .env overrides, since those constants are
# computed once at first import and .env loading used to only happen incidentally,
# whenever some test's import chain first pulled in QYT. That made a handful of
# TestRuntimeDirectoryOverrides-style tests in test_format_settings.py pass or fail
# depending on which other test files existed and in what order pytest collected them.
_user_env = Path.home() / "AppData" / "Roaming" / "MeadowLark" / ".env"
load_dotenv(dotenv_path=_user_env if _user_env.exists() else None)
load_dotenv()

import pytest  # noqa: E402  (imported after load_dotenv so env is set at import time)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from PIL import ImageFont

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture
def import_script() -> Callable[[str], ModuleType]:
    """
    Load a module from scripts/ by filename.

    scripts/ is deliberately not a package (see the ``INP001`` per-file-ignore in
    pyproject.toml) so its maintenance scripts stay independent of the installed
    ``meadowlark`` package. That means they can't be imported with a normal
    ``import`` statement in tests; load them from their file path instead.
    """

    def _load(filename: str) -> ModuleType:
        import importlib.util

        path = _SCRIPTS_DIR / filename
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture
def fake_truetype_factory() -> Callable[..., Callable[..., ImageFont.FreeTypeFont]]:
    """
    Build a fake ``ImageFont.truetype`` that blocks chosen filenames, else substitutes a real font.

    ``blocked`` filenames raise ``OSError`` (simulating "not installed on this machine").
    Every other named font resolves to Pillow's own bundled default font, resized via
    ``font_variant`` -- a genuine ``FreeTypeFont``, but never dependent on which font
    families happen to exist on the runner (a Windows dev box may have ``consola.ttf``;
    Linux CI may have neither that nor ``DejaVuSansMono.ttf``). A file-like object (the
    ``BytesIO`` ``ImageFont.load_default`` resolves internally) passes through to the real
    loader untouched, so patching this at the module level doesn't also break the
    default-font fallback itself. ``base_font`` is loaded here, before any patching, so
    resizing it later never re-enters ``truetype`` and risks recursing into the fake --
    confirmed against Pillow's own source: ``FreeTypeFont.font_variant()`` reconstructs
    from ``self.font_bytes`` via a fresh ``BytesIO``, never by calling
    ``ImageFont.truetype()`` again.
    """
    from PIL import ImageFont as _ImageFont

    real_truetype = _ImageFont.truetype
    base_font = _ImageFont.load_default(size=10)

    def _factory(
        blocked: frozenset[str] = frozenset(),
    ) -> Callable[..., ImageFont.FreeTypeFont]:
        def _fake(name: object, size: int, *args: object, **kwargs: object) -> ImageFont.FreeTypeFont:
            if isinstance(name, str):
                if name in blocked:
                    msg = f"simulated missing font: {name}"
                    raise OSError(msg)
                return base_font.font_variant(size=size)
            return real_truetype(name, size, *args, **kwargs)

        return _fake

    return _factory

"""Shared fixtures for the MeadowLark test suite."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Redirect the app's log files before anything imports src.config, whose paths are
# import-time Final constants. The defaults are CWD-relative -- the repo root under
# pytest, i.e. the developer's real error_log.txt / history_log.txt -- and the code
# under test writes them directly (HistoryLogger, the yt-dlp debug capture), so an
# unredirected run appends fake entries to the logs the user diagnoses real failures
# from. Assigned (not setdefault) before src.config loads .env (src/app_env.py), which
# never overrides, so neither the shell nor a .env can point the suite back at a real
# log. One fixed dir, wiped per session (best effort: a concurrent run may hold files
# open), so it stays bounded and the last run's logs can still be read after a failure.
# Pinned by tests/test_log_isolation.py.
_TEST_LOG_DIR = Path(tempfile.gettempdir()) / "meadowlark-pytest-logs"
shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)
_TEST_LOG_DIR.mkdir(exist_ok=True)
os.environ["VID_DL_ERROR_LOG"] = str(_TEST_LOG_DIR / "error_log.txt")
os.environ["VID_DL_HISTORY_LOG"] = str(_TEST_LOG_DIR / "history_log.txt")
os.environ["VID_DL_YTDLP_DEBUG_LOG"] = str(_TEST_LOG_DIR / "ytdlp_debug.log")

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
        def _fake(
            name: object, size: int, *args: object, **kwargs: object
        ) -> ImageFont.FreeTypeFont:
            if isinstance(name, str):
                if name in blocked:
                    msg = f"simulated missing font: {name}"
                    raise OSError(msg)
                return base_font.font_variant(size=size)
            return real_truetype(name, size, *args, **kwargs)

        return _fake

    return _factory

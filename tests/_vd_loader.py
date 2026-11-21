"""
Shared meadowlark.pyw module-loader shim.

Used by tests needing a real ``MyWindow`` bound to a stubbed ``yt_dlp``.
"""

import importlib.util
import sys
import types
from pathlib import Path


def _restore_module(name: str, mod: types.ModuleType | None) -> None:
    """
    Restore (or remove) a saved module and its parent-package attribute.

    exec_module re-imports popped submodules under the fake yt_dlp and rebinds
    them on their parent package (e.g. src.ydl_utils on src).  Restoring only
    sys.modules leaves a stale fake-bound attribute that breaks mock.patch
    targets resolved via getattr-walk like "src.ydl_utils.yt_dlp.YoutubeDL".
    """
    if mod is None:
        sys.modules.pop(name, None)
        return
    sys.modules[name] = mod
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, mod)


# ``src.window_*`` modules with no yt_dlp tie: tests patch them directly via ``sys.modules``,
# so re-importing them per load would leave ``vd`` bound to a different module object.
_WINDOW_NON_MIXINS = frozenset({"src.window_base", "src.window_geometry"})


def _window_mods() -> dict[str, types.ModuleType]:
    """Snapshot loaded ``src.window_*`` mixin modules (those that may bind the fake yt_dlp)."""
    return {
        n: m
        for n, m in sys.modules.items()
        if n.startswith("src.window_") and n not in _WINDOW_NON_MIXINS
    }


def import_vid_module():
    # src.playlist_entry needs the real yt_dlp.extractor tree, which the fake below lacks;
    # cache it under the real package so meadowlark.pyw's import of it reuses that.
    importlib.import_module("src.playlist_entry")
    fake = types.ModuleType("yt_dlp")

    class _Dummy:
        def __init__(self, opts: dict) -> None:
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = False) -> dict:
            raise RuntimeError("unpatched DummyYDL invoked")

    fake.YoutubeDL = _Dummy
    utils_mod = types.ModuleType("yt_dlp.utils")

    class DownloadError(Exception):
        pass

    class ExtractorError(Exception):
        pass

    class MaxDownloadsReached(Exception):
        pass

    utils_mod.DownloadError = DownloadError
    utils_mod.ExtractorError = ExtractorError
    utils_mod.MaxDownloadsReached = MaxDownloadsReached

    old_yt_dlp = sys.modules.get("yt_dlp")
    old_yt_dlp_utils = sys.modules.get("yt_dlp.utils")
    old_src_download_executor = sys.modules.get("src.download_executor")
    old_src_ydl_utils = sys.modules.get("src.ydl_utils")
    # src.window_* mixins must be re-imported under the fake yt_dlp too (window_base has no
    # yt_dlp dependency, so it is shared).
    old_window_mods = _window_mods()
    sys.modules["yt_dlp"] = fake
    sys.modules["yt_dlp.utils"] = utils_mod
    sys.modules.pop("src.download_executor", None)
    sys.modules.pop("src.ydl_utils", None)
    for n in old_window_mods:
        sys.modules.pop(n, None)

    path = str(Path(__file__).parent.parent / "meadowlark.pyw")
    repo_root = str(Path(path).parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    spec = importlib.util.spec_from_file_location("vd", path)
    vd = importlib.util.module_from_spec(spec)
    sys.modules["vd"] = vd
    try:
        spec.loader.exec_module(vd)
        # Capture the mixin modules vd was built against before sys.modules is restored, so
        # tests can patch the globals moved methods actually read (see owner_module).
        vars(vd)["_window_modules"] = _window_mods()
    finally:
        _restore_module("yt_dlp", old_yt_dlp)
        _restore_module("yt_dlp.utils", old_yt_dlp_utils)
        _restore_module("src.download_executor", old_src_download_executor)
        _restore_module("src.ydl_utils", old_src_ydl_utils)
        new_window_names = [n for n in _window_mods() if n not in old_window_mods]
        src_pkg = sys.modules.get("src")
        for n in new_window_names:
            _restore_module(n, None)
            if src_pkg is not None:
                child = n.rpartition(".")[2]
                if child in vars(src_pkg):
                    delattr(src_pkg, child)
        for n, m in old_window_mods.items():
            _restore_module(n, m)
    return vd


def owner_module(vd: types.ModuleType, method: str) -> types.ModuleType:
    """
    Return the module whose globals ``vd.MyWindow.<method>`` resolves names in.

    Patch through this instead of ``vd`` so a test keeps targeting the right module when a
    method moves into a ``src.window_*`` mixin.
    """
    func = getattr(vd.MyWindow, method)
    name = func.__module__
    if name == vd.__name__:
        return vd
    return vd._window_modules[name]


def window_module(vd: types.ModuleType, name: str) -> types.ModuleType:
    """Return the ``src.window_<name>`` module ``vd`` was loaded against."""
    return vd._window_modules[f"src.window_{name}"]


def _make_dummy_win(vd: types.ModuleType, *, cache: dict | None = None):
    """Return a DummyWin instance wired to real MyWindow helpers."""

    class DummyWin:
        _podcast_latest_url_cache: dict = {}
        CACHE_TTL_SECONDS = vd.MyWindow.CACHE_TTL_SECONDS

        def _check_sponsorblock_for_video_id(self, vid: str) -> bool:
            return False

        def _cache_put(
            self,
            url: str,
            latest_url: str | None,
            latest_ts: int | None,
        ) -> None:
            pass  # no-op; individual tests override or inspect the real impl

        _episode_already_archived = vd.MyWindow._episode_already_archived
        _skip_if_update_episode = vd.MyWindow._skip_if_update_episode
        _skip_if_short_duration = vd.MyWindow._skip_if_short_duration
        _classify_episode_by_age = vd.MyWindow._classify_episode_by_age

    win = DummyWin()
    win._podcast_latest_url_cache = cache if cache is not None else {}
    return win

"""Shared meadowlark.pyw module-loader shim used by tests needing a real ``MyWindow`` bound to a stubbed ``yt_dlp``."""

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


def import_vid_module():
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
    sys.modules["yt_dlp"] = fake
    sys.modules["yt_dlp.utils"] = utils_mod
    sys.modules.pop("src.download_executor", None)
    sys.modules.pop("src.ydl_utils", None)

    path = str(Path(__file__).parent.parent / "meadowlark.pyw")
    repo_root = str(Path(path).parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    spec = importlib.util.spec_from_file_location("vd", path)
    vd = importlib.util.module_from_spec(spec)
    sys.modules["vd"] = vd
    try:
        spec.loader.exec_module(vd)
    finally:
        _restore_module("yt_dlp", old_yt_dlp)
        _restore_module("yt_dlp.utils", old_yt_dlp_utils)
        _restore_module("src.download_executor", old_src_download_executor)
        _restore_module("src.ydl_utils", old_src_ydl_utils)
    return vd


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

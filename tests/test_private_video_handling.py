import importlib.util
import queue
import sys

# helper function to import the main module even though its filename contains a space
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from src.exceptions import PodcastResolutionError
from src.podcast_helpers import MAX_LOOKAHEAD


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
    # before loading the target module, provide a minimal fake yt_dlp so the
    # import statement inside the file succeeds.  Individual tests will
    # monkeypatch ``YoutubeDL`` on the imported module as needed.
    fake = types.ModuleType("yt_dlp")

    # a dummy context manager; methods will be replaced by tests via monkeypatch
    class _Dummy:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("unpatched DummyYDL invoked")

    fake.YoutubeDL = _Dummy
    # also provide a minimal ``yt_dlp.utils`` namespace so imports in QYT.py succeed
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
    # Ensure the repo root is on sys.path so imports like `import QYT` succeed
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


def test_fetch_latest_accessible_entry_skips_private(monkeypatch):
    vd = import_vid_module()

    class DummyYDL:
        call_count = 0

        def __init__(self, opts):
            DummyYDL.call_count += 1
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            # first invocation simulates private-latest behaviour
            if self.opts.get("playlist_items") == "1":
                raise Exception("Private video is not available")
            # fallback returns a normal entry
            return {
                "entries": [
                    {
                        "title": "Accessible episode",
                        "id": "abc123",
                        "webpage_url": "http://example.com/accessible",
                    },
                ],
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL)
    entries, skipped, info = vd.fetch_latest_accessible_entry("http://fake-playlist")
    assert skipped is True
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["webpage_url"] == "http://example.com/accessible"
    # info should be the original dictionary returned by the dummy
    assert isinstance(info, dict)
    # because the first attempt failed, we should have made two calls
    assert DummyYDL.call_count == 2


def test_fetch_latest_accessible_entry_two_private_then_good(monkeypatch):
    vd = import_vid_module()

    class DummyYDL2:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            n = int(self.opts.get("playlist_items", 0))
            if n in (1, 2):
                # pretend the last entry is private
                return {"entries": [{"title": "Private video foo"}]}
            return {
                "entries": [
                    {
                        "title": "Working",
                        "id": "good",
                        "webpage_url": "http://example.com/good3",
                    },
                ],
                "title": "Playlist",
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL2)
    entries, skipped, info = vd.fetch_latest_accessible_entry("http://fake3")
    assert skipped is True
    assert entries[0]["webpage_url"] == "http://example.com/good3"
    assert info.get("title") == "Playlist"


def test_fetch_latest_accessible_entry_private_by_title(monkeypatch):
    vd = import_vid_module()

    class DummyYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            if self.opts.get("playlist_items") == "1":
                # return an entry whose title begins with the magic phrase
                return {"entries": [{"title": "Private video #456"}]}
            return {
                "entries": [
                    {
                        "title": "Other",
                        "id": "xyz",
                        "webpage_url": "http://example.com/other",
                    },
                ],
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL)
    entries, skipped, info = vd.fetch_latest_accessible_entry("http://fake2")
    assert skipped is True
    assert entries[0]["webpage_url"] == "http://example.com/other"


def test_filter_audio_playlist_urls_with_private(monkeypatch):
    # we don't need a real GUI instance; just supply an object with the
    # attributes that ``_filter_audio_playlist_urls`` touches.
    vd = import_vid_module()

    class DummyYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            if self.opts.get("playlist_items") == "1":
                raise Exception("Private video is not available")
            return {
                "entries": [
                    {
                        "title": "Good episode",
                        "id": "id123",
                        "webpage_url": "http://example.com/good",
                    },
                ],
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL)

    # use dummy window-like object
    class DummyWin:
        def _check_sponsorblock_for_video_id(self, vid):
            return False

        def _cache_put(self, url, latest_url, ts, *, video_id=None):
            pass

        _episode_already_archived = vd.MyWindow._episode_already_archived
        _skip_if_update_episode = vd.MyWindow._skip_if_update_episode
        _skip_if_short_duration = vd.MyWindow._skip_if_short_duration
        _classify_episode_by_age = vd.MyWindow._classify_episode_by_age

    win = DummyWin()
    to_download, pending, had_error, messages, statuses = vd.MyWindow._filter_audio_playlist_urls(
        win,
        ["http://fake-playlist"],
        {},
    )
    assert had_error is False
    assert any("private" in m.lower() for m in messages)
    assert statuses[0]["latest_url"] == "http://example.com/good"


def test_fetch_latest_accessible_entry_no_accessible(monkeypatch):
    vd = import_vid_module()

    class DummyYDL2:
        call_count = 0

        def __init__(self, opts):
            DummyYDL2.call_count += 1
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            # always claim private
            if self.opts.get("playlist_items") == "1":
                raise Exception("Private video")
            return {
                "entries": [{"title": "Private video 1"}, {"title": "Private video 2"}],
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL2)
    with pytest.raises(PodcastResolutionError):
        vd.fetch_latest_accessible_entry("http://nothing")
    # should have retried up to the lookup limit
    # the dummy is called once per lookahead attempt
    assert DummyYDL2.call_count == MAX_LOOKAHEAD


def test_filter_audio_playlist_urls_skips_update(monkeypatch, tmp_path):
    """Entries whose title contains '(Update)' should be skipped and archived."""
    vd = import_vid_module()
    archive_file = tmp_path / "tfarchive.txt"
    archive_file.write_text("", encoding="utf-8")

    class DummyYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            return {
                "entries": [
                    {
                        "title": "Episode (Update) special",
                        "id": "upd123",
                        "webpage_url": "http://example.com/update",
                        "timestamp": 1234567890,
                    },
                ],
            }

    monkeypatch.setattr("src.podcast_helpers.yt_dlp.YoutubeDL", DummyYDL)

    class DummyWin:
        def _check_sponsorblock_for_video_id(self, vid):
            return False

        def _cache_put(self, url, latest_url, ts, *, video_id=None):
            pass

        _episode_already_archived = vd.MyWindow._episode_already_archived
        _skip_if_update_episode = vd.MyWindow._skip_if_update_episode
        _skip_if_short_duration = vd.MyWindow._skip_if_short_duration
        _classify_episode_by_age = vd.MyWindow._classify_episode_by_age

    win = DummyWin()
    with patch("QYT.HistoryLogger.log_skip"):
        to_download, pending, had_error, messages, statuses = (
            vd.MyWindow._filter_audio_playlist_urls(
                win,
                ["http://fake-playlist"],
                {"download_archive": str(archive_file)},
            )
        )
    assert had_error is False
    assert to_download == []
    assert pending == []
    assert any("Update exception" in m for m in messages)
    # archive should now contain the video id
    content = archive_file.read_text(encoding="utf-8")
    assert "youtube upd123" in content


def test_download_retries_without_sponsorblock(monkeypatch):
    """Download should retry once without SponsorBlock if SponsorBlock API is down."""
    vd = import_vid_module()
    # Get DownloadError from the fake yt_dlp.utils that was set up by import_vid_module
    from yt_dlp.utils import DownloadError

    class DummyYDL:
        inst_opts = []

        def __init__(self, opts):
            # record the options passed in so we can assert on retry behavior
            DummyYDL.inst_opts.append(dict(opts))
            self.opts = opts
            self.cache = types.SimpleNamespace(remove=lambda: None)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def extract_info(self, url, download=False):
            return {"title": "Test Video"}

        def download(self, urls):
            # First call should raise a SponsorBlock API failure; second call should succeed
            if len(DummyYDL.inst_opts) == 1:
                raise DownloadError(
                    "Postprocessing: Unable to communicate with SponsorBlock API: "
                    "HTTP Error 503: Service Unavailable",
                )

    # Patch YoutubeDL in download_executor module (where it's actually used)
    import src.download_executor

    monkeypatch.setattr(src.download_executor, "YoutubeDL", DummyYDL)

    download_queue = queue.Queue()
    q = vd.QYT.QYTQueue(download_queue)
    q.download(
        ["http://example.com/video"],
        {
            "postprocessors": [{"key": "SponsorBlock"}],
            "qmeta": {"site": "youtube", "type": "1080"},
        },
    )

    # We expect three instantiations:
    # 1) main download attempt
    # 2) title extraction attempt after failure
    # 3) retry without SponsorBlock
    assert len(DummyYDL.inst_opts) == 3

    # Verify retry options were marked and SponsorBlock was removed
    retry_opts = DummyYDL.inst_opts[-1]
    assert retry_opts.get("_tried_without_sponsorblock") is True
    assert all(pp.get("key") != "SponsorBlock" for pp in retry_opts.get("postprocessors", []))

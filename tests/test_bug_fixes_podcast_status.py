"""
Boundary tests for the two Podcast-Status-window bug fixes.

BUG 1 — _label_from_comments helper (offline label resolution).
BUG 2 — _resolve_latest_via_ytdlp hardening + _guarded_status_action guard.

Module-load pattern mirrors tests/_vd_loader.py (importlib shim that
stubs out yt_dlp so meadowlark.pyw loads without a real Qt display).
"""

import sys
import types
from collections.abc import Callable
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Module loader (reuses the exact shim from tests/_vd_loader.py)
# ---------------------------------------------------------------------------


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


def _import_vd() -> types.ModuleType:
    """Load meadowlark.pyw as module 'vd' with a stubbed yt_dlp."""
    fake = types.ModuleType("yt_dlp")

    class _DummyYDL:
        def __init__(self, opts: dict) -> None:
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = False) -> dict:
            raise RuntimeError("unpatched DummyYDL invoked")

    fake.YoutubeDL = _DummyYDL
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

    saved: dict = {}
    for key in ("yt_dlp", "yt_dlp.utils", "src.download_executor", "src.ydl_utils"):
        saved[key] = sys.modules.get(key)

    sys.modules["yt_dlp"] = fake
    sys.modules["yt_dlp.utils"] = utils_mod
    sys.modules.pop("src.download_executor", None)
    sys.modules.pop("src.ydl_utils", None)

    import importlib.util as _ilu
    from pathlib import Path

    repo_root = str(Path(__file__).parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    path = str(Path(__file__).parent.parent / "meadowlark.pyw")
    spec = _ilu.spec_from_file_location("vd_bug_fix", path)
    vd = _ilu.module_from_spec(spec)
    sys.modules["vd_bug_fix"] = vd
    try:
        spec.loader.exec_module(vd)
    finally:
        for key, mod in saved.items():
            _restore_module(key, mod)
    return vd


# ---------------------------------------------------------------------------
# Lightweight stub window for methods that need self.logEdit / self._podcast_last_statuses
# ---------------------------------------------------------------------------


def _make_stub_win(vd: types.ModuleType, *, statuses: list[dict] | None = None):
    """Return a minimal stub window wired to real MyWindow helper methods."""
    logged_lines: list[str] = []

    class _LogEdit:
        def appendPlainText(self, text: str) -> None:
            logged_lines.append(text)

    class StubWin:
        logEdit = _LogEdit()
        _podcast_last_statuses: list[dict] = statuses if statuses is not None else []
        _podcast_latest_url_cache: dict = {}
        CACHE_TTL_SECONDS = vd.MyWindow.CACHE_TTL_SECONDS

        _cache_get_fresh = vd.MyWindow._cache_get_fresh
        _cache_put = vd.MyWindow._cache_put

    win = StubWin()
    win._logged_lines = logged_lines
    return win


# ===========================================================================
# SECTION 1 — _label_from_comments unit tests
# ===========================================================================


class TestLabelFromComments:
    """Boundary tests for the module-level _label_from_comments helper."""

    def setup_method(self) -> None:
        self.vd = _import_vd()

    # --- Nominal: pl_id present in comments ---

    def test_label_returned_when_pl_id_in_comments(self, monkeypatch) -> None:
        """Return sanitize_for_path(label) when pl_id found in comments."""
        import utils as u

        monkeypatch.setattr(u, "sanitize_for_path", lambda s: s.strip())
        url = "https://www.youtube.com/playlist?list=PLabc123"
        result = self.vd._label_from_comments(url, {"PLabc123": "My Podcast"})
        assert result == "My Podcast"

    def test_label_passes_through_sanitize_for_path(self, monkeypatch) -> None:
        """Apply sanitize_for_path transformation to the retrieved label."""
        import utils as u

        monkeypatch.setattr(u, "sanitize_for_path", lambda s: s.replace(" ", "_"))
        url = "https://www.youtube.com/playlist?list=PLsanitize"
        result = self.vd._label_from_comments(url, {"PLsanitize": "My Cool Show"})
        assert result == "My_Cool_Show"

    # --- pl_id absent from comments ---

    def test_none_returned_when_pl_id_not_in_comments(self) -> None:
        """Return None when pl_id is extracted but absent from comments dict."""
        url = "https://www.youtube.com/playlist?list=PLnothere"
        result = self.vd._label_from_comments(url, {"PLsomethingelse": "X"})
        assert result is None

    def test_none_returned_when_comments_empty(self) -> None:
        """Return None for any URL when comments dict is empty."""
        url = "https://www.youtube.com/playlist?list=PLanything"
        result = self.vd._label_from_comments(url, {})
        assert result is None

    # --- URL has no extractable playlist id ---

    def test_none_returned_when_url_has_no_list_param(self) -> None:
        """Return None when URL has no ?list= parameter."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        result = self.vd._label_from_comments(url, {"PLx": "label"})
        assert result is None

    def test_none_returned_when_url_is_empty_string(self) -> None:
        """Return None for an empty string URL."""
        result = self.vd._label_from_comments("", {"PLx": "label"})
        assert result is None

    def test_none_returned_when_url_is_plain_string_no_query(self) -> None:
        """Return None for a non-URL string with no query parameters."""
        result = self.vd._label_from_comments("not-a-url", {"not-a-url": "label"})
        assert result is None

    def test_none_returned_when_url_has_empty_list_param(self) -> None:
        """Return None when URL has list= but empty value (pl_id is falsy)."""
        url = "https://www.youtube.com/playlist?list="
        result = self.vd._label_from_comments(url, {"": "should_not_match"})
        # extract_playlist_id returns "" for an empty list param; "" is falsy
        assert result is None

    # --- Empty string label in comments (latent: sanitize_for_path("") returns "") ---

    def test_empty_string_label_in_comments_returns_falsy(self, monkeypatch) -> None:
        """
        Return a falsy value when pl_id maps to an empty label.

        sanitize_for_path("") returns ""; callers use ``or fallback`` so the
        fallback activates — that is the correct behaviour.  This test documents
        the contract so the interaction is not silently changed.
        """
        import utils as u

        monkeypatch.setattr(u, "sanitize_for_path", lambda s: s)
        url = "https://www.youtube.com/playlist?list=PLempty"
        result = self.vd._label_from_comments(url, {"PLempty": ""})
        assert not result  # "" is falsy — callers' `or fallback` will engage


# ===========================================================================
# SECTION 2 — _resolve_latest_via_ytdlp boundary tests
# ===========================================================================


class TestResolveLatestViaYtdlp:
    """
    Tests for MyWindow._resolve_latest_via_ytdlp.

    meadowlark.pyw imports ``extract_playlist_info`` from src.ydl_utils into its
    own module namespace, so tests patch that bound name (``vd.extract_playlist_info``)
    rather than the source module.
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def _call(self, win, playlist_url: str, info_return_value: object) -> dict | None:
        """Invoke _resolve_latest_via_ytdlp with a mocked extract_playlist_info."""
        with patch.object(
            self.vd, "extract_playlist_info", return_value=info_return_value
        ):
            return self.vd.MyWindow._resolve_latest_via_ytdlp(win, playlist_url)

    def _make_win(self):
        return _make_stub_win(self.vd)

    # --- info is None ---

    def test_returns_none_when_info_is_none(self) -> None:
        """Return None without raising when extract_playlist_info returns None."""
        result = self._call(self._make_win(), "http://pl/none", None)
        assert result is None

    # --- info is {} (no "entries" key) ---

    def test_returns_none_when_info_is_empty_dict(self) -> None:
        """
        Return None when info is an empty dict.

        info={} → entries falls back to [info] i.e. [{}].
        {} has no webpage_url / url → returns None.
        """
        result = self._call(self._make_win(), "http://pl/empty", {})
        assert result is None

    # --- entries == [] ---

    def test_returns_none_when_entries_list_is_empty(self) -> None:
        """Return None when entries list is empty (next yields nothing)."""
        result = self._call(self._make_win(), "http://pl/empty_entries", {"entries": []})
        assert result is None

    # --- entries == [None] ---

    def test_returns_none_when_sole_entry_is_none(self) -> None:
        """Return None for [None] entries without raising AttributeError."""
        result = self._call(self._make_win(), "http://pl/upcoming", {"entries": [None]})
        assert result is None

    # --- entries == [None, valid_entry] ---

    def test_returns_valid_entry_when_first_is_none_second_is_valid(self) -> None:
        """Skip None sentinel and return dict from first non-None entry."""
        valid = {
            "webpage_url": "https://www.youtube.com/watch?v=abc",
            "timestamp": 1700000000,
        }
        result = self._call(
            self._make_win(), "http://pl/mixed", {"entries": [None, valid]}
        )
        assert result is not None
        assert result["url"] == "https://www.youtube.com/watch?v=abc"
        assert result["ts"] == 1700000000

    # --- entries == [valid_entry] ---

    def test_returns_dict_with_url_and_ts_for_valid_entry(self) -> None:
        """Return {"url": webpage_url, "ts": ts} for a single valid entry."""
        valid = {
            "webpage_url": "https://www.youtube.com/watch?v=xyz",
            "timestamp": 1710000000,
        }
        result = self._call(self._make_win(), "http://pl/ok", {"entries": [valid]})
        assert result == {"url": "https://www.youtube.com/watch?v=xyz", "ts": 1710000000}

    # --- entry uses "url" fallback instead of "webpage_url" ---

    def test_returns_url_key_when_no_webpage_url(self) -> None:
        """Return result using url key when webpage_url is absent."""
        valid = {"url": "https://example.com/video", "timestamp": 9999}
        result = self._call(self._make_win(), "http://pl/urlkey", {"entries": [valid]})
        assert result is not None
        assert result["url"] == "https://example.com/video"

    # --- entry has neither webpage_url nor url ---

    def test_returns_none_when_entry_has_no_url_fields(self) -> None:
        """Return None when entry has neither webpage_url nor url."""
        invalid = {"id": "abc123", "timestamp": 100}
        result = self._call(
            self._make_win(), "http://pl/nourl", {"entries": [invalid]}
        )
        assert result is None

    # --- timestamp may be None ---

    def test_returns_dict_with_none_ts_when_no_timestamp(self) -> None:
        """Return dict with ts=None when entry has no timestamp key."""
        valid = {"webpage_url": "https://www.youtube.com/watch?v=nots"}
        result = self._call(self._make_win(), "http://pl/nots", {"entries": [valid]})
        assert result is not None
        assert result["ts"] is None

    # --- REGRESSION GUARD: the correct extract_playlist_info is wired up ---

    def test_module_binds_extract_playlist_info_from_ydl_utils(self) -> None:
        """
        meadowlark.pyw must bind the real extract_playlist_info.

        Regression guard for the silent-crash bug: the call used to be
        ``utils.extract_playlist_info`` which does not exist and raised
        AttributeError inside a Qt slot, aborting the app.  The module must
        expose the function imported from src.ydl_utils.

        (_import_vd reloads src.ydl_utils in isolation, so we assert the
        function's origin rather than object identity.)
        """
        fn = getattr(self.vd, "extract_playlist_info", None)
        assert callable(fn), "meadowlark must bind a callable extract_playlist_info"
        assert fn.__module__ == "src.ydl_utils"
        assert fn.__name__ == "extract_playlist_info"

    # --- YDL_COMMON_ERRORS caught and silenced ---

    def test_returns_none_on_ydl_common_error(self) -> None:
        """Return None without propagating when extract_playlist_info raises a YDL error."""
        vd = self.vd
        win = self._make_win()
        ydl_error_class = (
            vd.YDL_COMMON_ERRORS[0]
            if isinstance(vd.YDL_COMMON_ERRORS, tuple)
            else vd.YDL_COMMON_ERRORS
        )
        import utils as u

        with (
            patch.object(
                vd, "extract_playlist_info", side_effect=ydl_error_class("err")
            ),
            patch.object(u, "log_exception"),
        ):
            result = vd.MyWindow._resolve_latest_via_ytdlp(win, "http://pl/err")
        assert result is None

    # --- Non-YDL errors are NOT swallowed ---

    def test_non_ydl_error_escapes(self) -> None:
        """
        Propagate ValueError so _guarded_status_action can log it.

        A ValueError is not in YDL_COMMON_ERRORS; it must reach the caller.
        """
        import pytest

        vd = self.vd
        win = self._make_win()

        with (
            patch.object(vd, "extract_playlist_info", side_effect=ValueError("bad")),
            pytest.raises(ValueError, match="bad"),
        ):
            vd.MyWindow._resolve_latest_via_ytdlp(win, "http://pl/valuerr")

    # --- Multiple None entries followed by valid ---

    def test_returns_first_non_none_when_multiple_nones_precede(self) -> None:
        """Skip all leading None entries and return first non-None result."""
        valid = {"webpage_url": "https://www.youtube.com/watch?v=multi", "timestamp": 1}
        result = self._call(
            self._make_win(),
            "http://pl/multinone",
            {"entries": [None, None, valid]},
        )
        assert result is not None
        assert result["url"] == "https://www.youtube.com/watch?v=multi"

    # --- info dict used as single entry when no "entries" key ---

    def test_direct_info_without_entries_key_uses_info_as_entry(self) -> None:
        """
        Use info itself as a single-item entry list when no 'entries' key present.

        When info has no "entries" key the code does
        ``entries = info.get("entries", [info])``.  If info itself carries
        webpage_url the function should return a valid result dict.
        """
        info = {"webpage_url": "https://www.youtube.com/watch?v=direct", "timestamp": 42}
        result = self._call(self._make_win(), "http://pl/direct", info)
        assert result is not None
        assert result["url"] == "https://www.youtube.com/watch?v=direct"
        assert result["ts"] == 42

    # --- Premiere error string still names the video → recover watch URL ---

    def test_resolve_recovers_video_id_from_premiere_error(self) -> None:
        """Recover the watch URL from a premiere error that yields no entry."""
        vd = self.vd
        win = self._make_win()
        download_error = vd.YDL_COMMON_ERRORS[0]
        err = "ERROR: [youtube] dQw4w9WgXcQ: This live event will begin in 2 hours."
        import utils as u

        with (
            patch.object(vd, "extract_playlist_info", side_effect=download_error(err)),
            patch.object(u, "log_exception"),
        ):
            result = vd.MyWindow._resolve_latest_via_ytdlp(win, "http://pl/premiere")
        assert result is not None
        assert result["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert isinstance(result["ts"], float)

    # --- Error string has no recoverable video ID → preserve None behaviour ---

    def test_resolve_returns_none_when_error_has_no_video_id(self) -> None:
        """Return None (regression guard) when the error names no video ID."""
        vd = self.vd
        win = self._make_win()
        download_error = vd.YDL_COMMON_ERRORS[0]
        import utils as u

        with (
            patch.object(
                vd, "extract_playlist_info", side_effect=download_error("network down")
            ),
            patch.object(u, "log_exception"),
        ):
            result = vd.MyWindow._resolve_latest_via_ytdlp(win, "http://pl/nodverr")
        assert result is None


# ===========================================================================
# SECTION 3 — _guarded_status_action boundary tests
# ===========================================================================


def _raise(exc: Exception) -> Callable[[int], None]:
    """Return a single-argument callable that raises exc when called."""

    def _action(_row: int) -> None:
        raise exc

    return _action


class TestGuardedStatusAction:
    """
    Tests for MyWindow._guarded_status_action.

    The guard must:
    1. Swallow any Exception subclass.
    2. Call logEdit.appendPlainText with an error message.
    3. Call utils.log_exception.
    4. NOT propagate the exception.
    5. NOT log anything on a successful call.
    6. NOT swallow BaseException subclasses outside Exception (e.g. KeyboardInterrupt).
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def _call_guard(
        self,
        win,
        action: Callable[[int], None],
        row: int,
        error_label: str = "test action",
    ) -> None:
        self.vd.MyWindow._guarded_status_action(
            win, action, row, error_label=error_label
        )

    # --- action raises RuntimeError (generic Exception) ---

    def test_runtime_error_does_not_propagate(self) -> None:
        """Catch RuntimeError inside action; method must return normally."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        with patch.object(u, "log_exception"):
            self._call_guard(win, _raise(RuntimeError("oops")), 0)
        # reaching here means no exception escaped

    def test_runtime_error_is_logged_to_log_edit(self) -> None:
        """Append an error message to logEdit when action raises RuntimeError."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        with patch.object(u, "log_exception"):
            self._call_guard(
                win,
                _raise(RuntimeError("boom")),
                0,
                error_label="open latest video",
            )
        assert any("open latest video" in line for line in win._logged_lines), (
            "Expected error_label in log output"
        )

    def test_runtime_error_calls_utils_log_exception(self) -> None:
        """Forward RuntimeError to utils.log_exception exactly once."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        with patch.object(u, "log_exception") as mock_log_exc:
            self._call_guard(win, _raise(RuntimeError("detail")), 0)
        mock_log_exc.assert_called_once()

    # --- action raises AttributeError (the original bug trigger) ---

    def test_attribute_error_does_not_propagate(self) -> None:
        """Catch AttributeError (original BUG 2 crash scenario) silently."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        with patch.object(u, "log_exception"):
            self._call_guard(
                win,
                _raise(AttributeError("NoneType has no .get")),
                0,
                error_label="open latest video",
            )
        assert any("open latest video" in line for line in win._logged_lines)

    # --- action succeeds — no log entry written ---

    def test_no_log_entry_when_action_succeeds(self) -> None:
        """Leave logEdit empty when action completes without error."""
        vd = self.vd
        win = _make_stub_win(vd)
        call_count = [0]

        def _ok(row: int) -> None:
            call_count[0] += 1

        self._call_guard(win, _ok, 7)
        assert call_count[0] == 1
        assert win._logged_lines == []

    # --- negative row (action raises IndexError internally) ---

    def test_action_receiving_negative_row_raises_caught(self) -> None:
        """Catch IndexError raised inside action for a negative row value."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        def _action_with_index(row: int) -> None:
            data: list[int] = []
            _ = data[row]  # raises IndexError for row=-1

        with patch.object(u, "log_exception"):
            self._call_guard(win, _action_with_index, -1, error_label="bad index")
        assert any("bad index" in line for line in win._logged_lines)

    # --- KeyboardInterrupt is NOT an Exception subclass — must escape ---

    def test_keyboard_interrupt_propagates(self) -> None:
        """
        Propagate KeyboardInterrupt because it is a BaseException, not Exception.

        The guard uses ``except Exception`` so it must NOT swallow KI.
        """
        import pytest

        vd = self.vd
        win = _make_stub_win(vd)

        def _raise_ki(_row: int) -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            self._call_guard(win, _raise_ki, 0)

    # --- error_label appears in both the log message and log_exception call ---

    def test_error_label_forwarded_to_log_exception(self) -> None:
        """Include error_label string in the utils.log_exception call arguments."""
        vd = self.vd
        win = _make_stub_win(vd)
        import utils as u

        logged_msgs: list[str] = []

        def _capture_log_exc(exc: object, msg: str) -> None:
            logged_msgs.append(msg)

        with patch.object(u, "log_exception", side_effect=_capture_log_exc):
            self._call_guard(
                win,
                _raise(ValueError("x")),
                0,
                error_label="start download now",
            )
        assert any("start download now" in m for m in logged_msgs), (
            "error_label must appear in utils.log_exception message"
        )

    # --- Verify both menu action paths feed through the guard ---

    def test_open_latest_for_row_attribute_error_caught_by_guard(self) -> None:
        """
        Prevent crash when _open_latest_for_row raises AttributeError.

        Simulates BUG 2 pre-fix: _resolve_latest_via_ytdlp raises AttributeError
        because yt-dlp returned None info.  The guard must prevent propagation.
        """
        vd = self.vd
        # Build a status entry with no latest_url (forces the ytdlp fallback path)
        statuses = [{"url": "http://pl/upcoming", "podcast": "My Show"}]
        win = _make_stub_win(vd, statuses=statuses)
        import utils as u

        with (
            patch.object(
                vd.MyWindow,
                "_resolve_latest_via_ytdlp",
                side_effect=AttributeError("'NoneType' object has no attribute 'get'"),
            ),
            patch.object(u, "log_exception"),
            patch.object(vd.MyWindow, "_cache_get_fresh", return_value=None),
        ):
            self._call_guard(
                win,
                lambda row: vd.MyWindow._open_latest_for_row(win, row),
                0,
                error_label="open latest video",
            )
        assert any("open latest video" in line for line in win._logged_lines)

    # --- SystemExit (BaseException, not Exception) must propagate ---

    def test_system_exit_propagates_through_guard(self) -> None:
        """SystemExit must not be swallowed — it is BaseException, not Exception."""
        import pytest

        vd = self.vd
        win = _make_stub_win(vd)

        def _raise_sys_exit(_row: int) -> None:
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            self._call_guard(win, _raise_sys_exit, 0)


# ===========================================================================
# SECTION 4 — _open_latest_for_row boundary tests
# ===========================================================================


class TestOpenLatestForRow:
    """
    Boundary tests for MyWindow._open_latest_for_row.

    Focuses on edge cases not exercised by the _guarded_status_action tests:
    - out-of-range rows (negative, past-end, empty list)
    - status entry has no "url" key (playlist_url is None)
    - happy path: latest_url already in status entry (no fallback needed)
    - cache hit: cache returns value, no yt-dlp call
    - None resolved from yt-dlp (logs instead of opening)
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def _make_win(self, statuses: list[dict] | None = None):
        return _make_stub_win(self.vd, statuses=statuses)

    def _call(self, win, row: int) -> None:
        self.vd.MyWindow._open_latest_for_row(win, row)

    # --- out-of-range: negative row ---

    def test_negative_row_returns_silently(self) -> None:
        """
        Negative row index must return without any action or error.

        The guard `if not (0 <= row < len(statuses))` fires before any
        attribute access, so no stubs are needed for _open_url_in_browser etc.
        """
        win = self._make_win(statuses=[{"url": "http://pl/x", "podcast": "X"}])
        # If the guard is absent, _open_url_in_browser / _resolve_latest_via_ytdlp
        # would be looked up on StubWin and raise AttributeError — so any
        # AttributeError here means the guard is missing.
        self._call(win, -1)
        assert win._logged_lines == []

    # --- out-of-range: row == len(statuses) ---

    def test_row_equal_to_len_returns_silently(self) -> None:
        """Row equal to len(statuses) is out of range — must return silently."""
        statuses = [{"url": "http://pl/x", "podcast": "X"}]
        win = self._make_win(statuses=statuses)
        self._call(win, 1)  # len == 1, so index 1 is out of range
        assert win._logged_lines == []

    # --- out-of-range: empty statuses list ---

    def test_row_zero_with_empty_statuses_returns_silently(self) -> None:
        """Row=0 on an empty statuses list is out of range — must return silently."""
        win = self._make_win(statuses=[])
        self._call(win, 0)
        assert win._logged_lines == []

    # --- happy path: latest_url already in status entry ---

    def test_opens_latest_url_from_status_entry_directly(self) -> None:
        """When status entry already has latest_url, open it without any fallback."""
        statuses = [
            {
                "url": "http://pl/x",
                "podcast": "My Podcast",
                "latest_url": "https://youtube.com/watch?v=direct",
            }
        ]
        win = self._make_win(statuses=statuses)
        opened: list[str] = []
        resolve_called: list[bool] = []

        # StubWin does not inherit MyWindow; bind the methods directly on the instance.
        win._open_url_in_browser = lambda url, _label: opened.append(url)
        win._resolve_latest_via_ytdlp = lambda _url: resolve_called.append(True) or None

        self._call(win, 0)
        assert opened == ["https://youtube.com/watch?v=direct"]
        assert resolve_called == [], "resolve must not be called when latest_url is in status"

    # --- cache hit: cache returns URL, skips yt-dlp ---

    def test_opens_url_from_cache_when_no_latest_url_in_status(self) -> None:
        """When status has no latest_url but cache returns one, use cache without yt-dlp."""
        statuses = [{"url": "http://pl/cache", "podcast": "Cached Show"}]
        win = self._make_win(statuses=statuses)
        opened: list[str] = []
        resolve_called: list[bool] = []

        win._cache_get_fresh = lambda _url: "https://youtube.com/watch?v=cached"
        win._open_url_in_browser = lambda url, _label: opened.append(url)
        win._resolve_latest_via_ytdlp = lambda _url: resolve_called.append(True) or None

        self._call(win, 0)
        assert opened == ["https://youtube.com/watch?v=cached"]
        assert resolve_called == [], "resolve must not be called when cache has a fresh URL"

    # --- playlist_url is None (status entry lacks "url" key) ---

    def test_no_url_key_skips_resolve_and_logs(self) -> None:
        """
        When a status entry has no 'url' key, resolve is skipped (not called with None).

        Guards the `if playlist_url else None` short-circuit in _open_latest_for_row,
        so we never invoke yt-dlp (and never reach extract_playlist_info(None, ...)).
        Instead the could-not-resolve message is logged.
        """
        # Status entry deliberately missing the "url" key
        statuses = [{"podcast": "No URL Show"}]
        win = self._make_win(statuses=statuses)
        resolve_calls: list = []

        # StubWin does not inherit MyWindow; bind directly on the instance.
        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda url: resolve_calls.append(url) or None

        self._call(win, 0)

        assert resolve_calls == [], (
            "resolve must NOT be called when the status entry has no 'url' key"
        )
        assert any("No URL Show" in line for line in win._logged_lines), (
            "Expected could-not-resolve log message referencing the podcast label"
        )

    # --- yt-dlp returns None: logs informational message ---

    def test_logs_message_when_resolve_returns_none(self) -> None:
        """When _resolve_latest_via_ytdlp returns None, append a message to logEdit."""
        statuses = [{"url": "http://pl/upcoming", "podcast": "Upcoming Show"}]
        win = self._make_win(statuses=statuses)

        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda _url: None
        # A playlist URL is present, so the final fallback opens it; bind a no-op.
        win._open_url_in_browser = lambda _url, _label: None

        self._call(win, 0)
        assert any("Upcoming Show" in line for line in win._logged_lines), (
            "Expected podcast label in could-not-resolve log message"
        )

    # --- unresolvable: still open the playlist page as a final fallback ---

    def test_open_latest_opens_playlist_url_when_unresolvable(self) -> None:
        """
        When resolution fails entirely, keep the note AND open the playlist page.

        Guards the final-fallback branch: the action must never be a dead end —
        the existing could-not-resolve note is preserved and the podcast/playlist
        URL is opened via _open_url_in_browser.
        """
        statuses = [
            {
                "podcast": "Show",
                "url": "https://youtube.com/playlist?list=PLx",
                "status": "Upcoming",
            }
        ]
        win = self._make_win(statuses=statuses)
        opened: list[tuple] = []

        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda _url: None
        win._open_url_in_browser = lambda url, label: opened.append((url, label))

        self._call(win, 0)

        assert any(
            "Could not resolve latest episode for Show" in line
            for line in win._logged_lines
        ), "Expected the existing could-not-resolve note to be preserved"
        assert opened == [("https://youtube.com/playlist?list=PLx", "Show")], (
            "Expected the playlist URL to be opened exactly once as a fallback"
        )


# ===========================================================================
# SECTION 5 — _label_from_comments additional boundary tests
# ===========================================================================


class TestLabelFromCommentsAdditional:
    """Additional boundary cases not covered in TestLabelFromComments."""

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def test_whitespace_only_comment_returns_misc_not_url_fallback(self) -> None:
        """
        Whitespace-only comment sanitizes to 'misc' via sanitize_for_path.

        sanitize_for_path("  ") strips to "" then returns "misc", which is truthy.
        Callers' `or url` fallback does NOT activate — the podcast column shows
        "misc" instead of the raw URL.  This documents the latent behaviour so
        any future strip-before-store fix is guarded by a regression test.
        """
        url = "https://www.youtube.com/playlist?list=PLws"
        result = self.vd._label_from_comments(url, {"PLws": "  "})
        # "  " → sanitize_for_path → "" → "misc"
        assert result == "misc"

    def test_label_with_windows_invalid_chars_is_sanitized(self, monkeypatch) -> None:
        """Labels with Windows-invalid characters pass through sanitize_for_path."""
        import utils as u

        monkeypatch.setattr(u, "sanitize_for_path", lambda s: s.replace(":", "_"))
        url = "https://www.youtube.com/playlist?list=PLcolon"
        result = self.vd._label_from_comments(url, {"PLcolon": "Show: The Podcast"})
        assert result == "Show_ The Podcast"

    def test_url_with_extra_query_params_still_extracts_list(self) -> None:
        """Extract pl_id correctly when URL has multiple query parameters."""
        url = "https://www.youtube.com/playlist?si=abc&list=PLmulti&index=1"
        result = self.vd._label_from_comments(url, {"PLmulti": "Multi Param Show"})
        assert result == "Multi Param Show"


# ===========================================================================
# SECTION 6 — scheduled-premiere status carries a recovered latest_url
# ===========================================================================


class TestScheduledPremiereStatus:
    """
    Integration test for the scheduled-error branch of _filter_audio_playlist_urls.

    A premiere/scheduled live event fails extraction before yielding any entry,
    but yt-dlp's error string still names the video.  The status row must carry
    a recovered ``latest_url`` so "Open Latest Video" works with zero extra work.
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def test_scheduled_premiere_status_carries_latest_url(self, monkeypatch) -> None:
        """Recover latest_url for a scheduled premiere that errors before yielding."""
        vd = self.vd
        import utils as u

        monkeypatch.setattr(u, "load_playlist_comments_for_source", lambda _: {})
        monkeypatch.setattr(u, "sanitize_for_path", lambda s: s)

        from tests._vd_loader import _make_dummy_win

        win = _make_dummy_win(vd)
        url = "https://www.youtube.com/playlist?list=PLx"
        download_error = vd.YDL_EXTRACTION_ERRORS[0]
        err = "ERROR: [youtube] dQw4w9WgXcQ: This live event will begin in 5 hours."

        # ydl_opts={} → archive_path is None → load_downloaded_video_ids(None)
        # returns an empty set, so the cache branch is skipped and the premiere
        # error propagates straight into the scheduled-error handler.
        with patch.object(
            vd, "fetch_latest_accessible_entry", side_effect=download_error(err)
        ):
            _, _, _, _, statuses = vd.MyWindow._filter_audio_playlist_urls(
                win, [url], {}
            )

        assert len(statuses) == 1
        st = statuses[0]
        assert st["status"] == "Upcoming"
        assert st["latest_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert st["latest_date"] == "(scheduled)"
        assert st.get("recheck_ts") is not None


# ===========================================================================
# SECTION 7 — _filter_audio_playlist_urls: extraction error edge cases
# ===========================================================================


class TestFilterAudioPlaylistUrlsEdgeCases:
    """
    Additional boundary tests for _filter_audio_playlist_urls.

    Case A: extraction error whose error string has a scheduled timestamp but
            NO recoverable video ID → status must be Upcoming WITHOUT latest_url.
    Case B: extraction error whose error string has NEITHER a scheduled timestamp
            NOR a video ID → status must be Error (not Upcoming).
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def _run(self, url: str, error: Exception, ydl_opts: dict | None = None):
        """Run _filter_audio_playlist_urls with a fetch that always raises ``error``."""
        import utils as u
        from tests._vd_loader import _make_dummy_win

        vd = self.vd
        with patch.object(
            u,
            "load_playlist_comments_for_source",
            return_value={},
        ), patch.object(u, "sanitize_for_path", lambda s: s):
            win = _make_dummy_win(vd)
            with patch.object(
                vd, "fetch_latest_accessible_entry", side_effect=error
            ):
                return vd.MyWindow._filter_audio_playlist_urls(
                    win, [url], ydl_opts or {}
                )

    def test_scheduled_premiere_without_video_id_still_upcoming(self) -> None:
        """
        When the error has a scheduled timestamp but NO video ID, status is Upcoming.

        This covers the code path where ``scheduled_ts`` is truthy but
        ``parse_video_id_from_error`` returns None (so the ``if vid:`` block
        is skipped and ``extra`` only contains ``recheck_ts``).
        """
        vd = self.vd
        download_error = vd.YDL_EXTRACTION_ERRORS[0]
        # Error has a schedule time but uses a 34-char playlist ID,
        # which parse_video_id_from_error must ignore.
        err = (
            "ERROR: [youtube:tab] PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx: "
            "This live event will begin in 1 hours."
        )
        url = "https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

        _, _, _, _, statuses = self._run(url, download_error(err))

        assert len(statuses) == 1
        st = statuses[0]
        assert st["status"] == "Upcoming", "must be Upcoming even without a video ID"
        assert "latest_url" not in st, "latest_url must be absent when no video ID recovered"
        assert st.get("recheck_ts") is not None

    def test_extraction_error_without_schedule_produces_error_status(self) -> None:
        """
        Status is 'Error: ...' when error string has no timestamp or video ID.

        had_error must be True in this case.
        """
        vd = self.vd
        download_error = vd.YDL_EXTRACTION_ERRORS[0]
        err = "Network unreachable"
        url = "https://www.youtube.com/playlist?list=PLerror"

        _, _, had_error, messages, statuses = self._run(url, download_error(err))

        assert had_error is True
        assert len(statuses) == 1
        st = statuses[0]
        assert st["status"].startswith("Error:"), (
            f"expected Error: prefix, got {st['status']!r}"
        )
        assert st["latest_date"] == "(error)"
        assert "latest_url" not in st


# ===========================================================================
# SECTION 8 — _open_latest_for_row: empty-string playlist_url is falsy
# ===========================================================================


class TestOpenLatestForRowFalsyUrl:
    """
    Guard the ``if playlist_url else None`` short-circuit for an empty-string URL.

    An empty-string "url" value is falsy — it must behave identically to a
    missing key: no yt-dlp call, and the could-not-resolve note references the
    label (or empty string) rather than crashing.
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def test_empty_string_url_skips_resolve_and_logs(self) -> None:
        """Empty-string 'url' is falsy; _resolve_latest_via_ytdlp must not be called."""
        vd = self.vd
        statuses = [{"url": "", "podcast": "Empty URL Show"}]
        win = _make_stub_win(vd, statuses=statuses)
        resolve_calls: list = []

        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda url: resolve_calls.append(url) or None

        vd.MyWindow._open_latest_for_row(win, 0)

        assert resolve_calls == [], "resolve must not be called for empty-string URL"
        # No browser call either because playlist_url is falsy; just a log message.
        assert any("Empty URL Show" in line for line in win._logged_lines), (
            "Expected could-not-resolve log referencing the podcast label"
        )

    def test_empty_string_url_does_not_open_playlist_fallback(self) -> None:
        """Empty-string URL must not trigger the playlist-fallback browser call."""
        vd = self.vd
        statuses = [{"url": "", "podcast": "Empty URL Show"}]
        win = _make_stub_win(vd, statuses=statuses)
        opened: list = []

        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda _url: None
        win._open_url_in_browser = lambda url, label: opened.append((url, label))

        vd.MyWindow._open_latest_for_row(win, 0)

        assert opened == [], (
            "_open_url_in_browser must not be called when playlist_url is empty string"
        )


# ===========================================================================
# SECTION 9 — _resolve_latest_via_ytdlp: error-recovery ts=None path
# ===========================================================================


class TestResolveLatestViaYtdlpTsNone:
    """
    Error recovery when no schedulable timestamp is present.

    When an error names a video ID but has NO schedulable timestamp, the recovered
    dict must still be returned with ts=None — not suppressed entirely.
    """

    def setup_method(self) -> None:
        self.vd = _import_vd()

    def test_recover_video_id_with_ts_none_when_no_schedule_phrase(self) -> None:
        """Return {url: ..., ts: None} when error names a video but no schedule info."""
        import utils as u

        vd = self.vd
        win = _make_stub_win(vd)
        download_error = vd.YDL_COMMON_ERRORS[0]
        # Names a valid 11-char ID but has no "will begin in" or "scheduled to begin"
        err = "ERROR: [youtube] dQw4w9WgXcQ: Members-only content unavailable."

        with (
            patch.object(vd, "extract_playlist_info", side_effect=download_error(err)),
            patch.object(u, "log_exception"),
        ):
            result = vd.MyWindow._resolve_latest_via_ytdlp(win, "http://pl/members")

        assert result is not None, "a video ID was found — must return a dict, not None"
        assert result["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert result["ts"] is None, "ts must be None when no schedule phrase in error"

    def test_open_latest_caches_recovered_url_even_when_ts_is_none(self) -> None:
        """
        _open_latest_for_row must cache and open the recovered URL even when ts=None.

        Guards the ``_cache_put`` call with a ts=None argument — the cache must
        accept it without raising.
        """
        vd = self.vd
        statuses = [{"url": "http://pl/premiere", "podcast": "My Show"}]
        win = _make_stub_win(vd, statuses=statuses)
        opened: list[str] = []
        cached: list[tuple] = []

        win._cache_get_fresh = lambda _url: None
        win._resolve_latest_via_ytdlp = lambda _url: {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "ts": None,
        }
        win._cache_put = lambda url, latest, ts, **_kw: cached.append((url, latest, ts))
        win._open_url_in_browser = lambda url, _label: opened.append(url)

        vd.MyWindow._open_latest_for_row(win, 0)

        assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
        assert len(cached) == 1
        assert cached[0][2] is None, "cache_put must be called with ts=None"

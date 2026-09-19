"""
Resolve and health-check the bgutil PO-token provider (script-deno mode).

YouTube gates 1080p+ media behind a per-video GVS PO token (yt-dlp #12482). That
token is minted by the bgutil-ytdlp-pot-provider plugin running in "script" mode
via the bundled Deno runtime. If the plugin, the Deno runtime, or the generate
script/deps are missing, every 1080p download 403s at the media stage. This module
reports whether the provider is fully wired so startup can warn instead of failing
silently. It mirrors the requirements of
yt_dlp_plugins.extractor.getpot_bgutil_script.BgUtilScriptDenoPTP.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import POT_PROVIDER_SERVER_HOME, VENV_SCRIPTS_DIR

# Mirror BgUtilScriptDenoPTP: script at {server_home}/src/generate_once.ts, deps
# under {server_home}/node_modules, Deno >= 2.0.0.
_SCRIPT_RELPATH: tuple[str, ...] = ("src", "generate_once.ts")
_NODE_MODULES = "node_modules"
_DENO_MIN_VERSION = (2, 0, 0)
_DENO_VERSION_TIMEOUT_S = 5.0
_DENO_VERSION_RE = re.compile(r"deno\s+(\d+)\.(\d+)\.(\d+)")

logger = logging.getLogger(__name__)

# The plugin gives its script-version probe a hard 15s budget
# (BgUtilScriptPTPBase._GET_SCRIPT_VSN_TIMEOUT). A cold DENO_DIR blows straight
# through it -- measured 26.3s cold vs 1.5s warm -- so is_available() goes False
# and every 1080p download 403s for want of a PO token. Warming the cache ahead of
# time is the only lever we have; the timeout itself lives in the plugin.
_PROBE_BUDGET_S = 15.0
# Cold fill pulls ~63 MB of npm deps over the network; be generous, this never
# runs on the UI thread.
_DENO_WARM_TIMEOUT_S = 300.0
# Windows GUI build (console=False): keep deno.exe from flashing a console window.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_CACHE_DIRNAME = "bgutil-ytdlp-pot-provider"


@dataclass(frozen=True)
class PotProviderStatus:
    """Per-component availability of the script-deno PO-token provider."""

    plugin_installed: bool
    deno_ok: bool
    script_found: bool
    node_modules_found: bool

    @property
    def ok(self) -> bool:
        return (
            self.plugin_installed and self.deno_ok and self.script_found and self.node_modules_found
        )

    def summary(self) -> str:
        """Comma-joined names of missing pieces; empty string when ``ok``."""
        if self.ok:
            return ""
        missing: list[str] = []
        if not self.plugin_installed:
            missing.append("provider plugin (bgutil-ytdlp-pot-provider)")
        if not self.deno_ok:
            missing.append("Deno runtime (>= 2.0)")
        if not self.script_found:
            missing.append("generate_once.ts script")
        if not self.node_modules_found:
            missing.append("script dependencies (node_modules)")
        return ", ".join(missing)


@dataclass(frozen=True)
class DenoWarmResult:
    """Outcome of a DENO_DIR warm-up run."""

    ok: bool
    elapsed_s: float
    detail: str


def _plugin_importable() -> bool:
    """Return True if the bgutil script provider plugin imports (i.e. not pruned)."""
    try:
        return (
            importlib.util.find_spec(
                "yt_dlp_plugins.extractor.getpot_bgutil_script",
            )
            is not None
        )
    except (ImportError, ValueError):
        return False


def _deno_ok(scripts_dir: Path) -> bool:
    """Return True if deno.exe exists in ``scripts_dir`` and reports version >= 2.0.0."""
    deno = scripts_dir / "deno.exe"
    if not deno.is_file():
        return False
    try:
        proc = subprocess.run(  # noqa: S603
            [str(deno), "--version"],
            capture_output=True,
            text=True,
            timeout=_DENO_VERSION_TIMEOUT_S,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    m = _DENO_VERSION_RE.search(proc.stdout or "")
    if not m:
        return False
    return tuple(int(g) for g in m.groups()) >= _DENO_MIN_VERSION


def check_pot_provider(
    server_home: Path | None = None,
    scripts_dir: Path | None = None,
) -> PotProviderStatus:
    """
    Probe every component the script-deno provider needs.

    Args:
        server_home: Provider server root; defaults to ``POT_PROVIDER_SERVER_HOME``.
        scripts_dir: Dir holding ``deno.exe``; defaults to ``VENV_SCRIPTS_DIR``.

    Returns:
        A ``PotProviderStatus`` snapshot.
    """
    home = Path(server_home) if server_home is not None else POT_PROVIDER_SERVER_HOME
    scripts = Path(scripts_dir) if scripts_dir is not None else VENV_SCRIPTS_DIR
    return PotProviderStatus(
        plugin_installed=_plugin_importable(),
        deno_ok=_deno_ok(scripts),
        script_found=home.joinpath(*_SCRIPT_RELPATH).is_file(),
        node_modules_found=(home / _NODE_MODULES).is_dir(),
    )


def _escpath(*paths: Path) -> str:
    """Mirror BgUtilScriptDenoPTP._jsrt_args.escpath: comma-join, doubling literal commas."""
    return ",".join(str(p).replace(",", ",,") for p in paths)


def _script_cache_dir(server_home: Path) -> Path:
    """
    Mirror BgUtilScriptPTPBase._script_cache_dir.

    The provider grants Deno --allow-write/--allow-read on this dir only, so the
    warm-up must pass the same path or the script errors instead of caching.
    """
    xdg = os.getenv("XDG_CACHE_HOME")
    if xdg is not None:
        return Path(xdg).absolute() / _CACHE_DIRNAME
    home = os.getenv("HOME") or os.getenv("USERPROFILE")
    if home:
        return Path(home).absolute() / ".cache" / _CACHE_DIRNAME
    return server_home


def build_warm_cmd(deno: str | Path, server_home: Path) -> list[str]:
    """Return the exact argv BgUtilScriptDenoPTP uses for its version probe."""
    node_mods = server_home / _NODE_MODULES
    cache = _script_cache_dir(server_home)
    return [
        str(deno),
        "run",
        "--allow-env",
        "--allow-net",
        f"--allow-ffi={_escpath(node_mods)}",
        f"--allow-write={_escpath(cache)}",
        f"--allow-read={_escpath(cache, node_mods)}",
        str(server_home.joinpath(*_SCRIPT_RELPATH)),
        "--version",
    ]


def build_warm_env() -> dict[str, str]:
    """Return the env BgUtilScriptDenoPTP._jsrt_envs builds (os.environ + Deno flags)."""
    env = os.environ.copy()
    env["DENO_NO_PROMPT"] = "1"
    env["DENO_NO_UPDATE_CHECK"] = "1"
    env["FORCE_COLOR"] = "false"
    return env


def warm_deno_cache(
    server_home: Path | None = None,
    scripts_dir: Path | None = None,
    timeout: float = _DENO_WARM_TIMEOUT_S,
) -> DenoWarmResult:
    r"""
    Run the provider's own version probe once to populate Deno's module cache.

    Deno caches npm deps and transpiled TS under DENO_DIR (default
    %LOCALAPPDATA%\deno). On a cold cache the plugin's probe overruns its hard 15s
    budget and 1080p downloads 403. Running the identical command here fills that
    cache while nothing is waiting on it. Safe to call on every launch: ~1.5s once warm.

    Args:
        server_home: Provider server root; defaults to ``POT_PROVIDER_SERVER_HOME``.
        scripts_dir: Dir holding ``deno.exe``; defaults to ``VENV_SCRIPTS_DIR``.
        timeout: Hard cap on the warm-up subprocess.

    Returns:
        A ``DenoWarmResult``; never raises.
    """
    home = Path(server_home) if server_home is not None else POT_PROVIDER_SERVER_HOME
    scripts = Path(scripts_dir) if scripts_dir is not None else VENV_SCRIPTS_DIR
    deno = scripts / "deno.exe"

    if not deno.is_file():
        return DenoWarmResult(False, 0.0, f"deno.exe not found at {deno}")
    if not home.joinpath(*_SCRIPT_RELPATH).is_file():
        return DenoWarmResult(False, 0.0, f"generate_once.ts not found under {home}")
    if not (home / _NODE_MODULES).is_dir():
        return DenoWarmResult(
            False,
            0.0,
            "node_modules missing; run scripts/setup_pot_provider.py",
        )

    cmd = build_warm_cmd(deno, home)
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=home,
            env=build_warm_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        logger.warning("Deno cache warm-up timed out after %.1fs", elapsed)
        return DenoWarmResult(False, elapsed, f"timed out after {timeout:.0f}s")
    except OSError as e:
        elapsed = time.monotonic() - start
        logger.warning("Deno cache warm-up could not start: %s", e)
        return DenoWarmResult(False, elapsed, str(e))

    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        logger.warning(
            "Deno cache warm-up failed (rc=%d, %.1fs): %s",
            proc.returncode,
            elapsed,
            detail,
        )
        return DenoWarmResult(False, elapsed, detail or f"returncode {proc.returncode}")

    if elapsed > _PROBE_BUDGET_S:
        logger.info(
            "Deno cache warmed in %.1fs (a cold probe would have blown the plugin's "
            "%.0fs budget; subsequent probes will be fast)",
            elapsed,
            _PROBE_BUDGET_S,
        )
    else:
        logger.debug("Deno cache already warm (%.1fs)", elapsed)
    return DenoWarmResult(True, elapsed, (proc.stdout or "").strip())


class _WarmupState:
    """
    Progress and outcome of the startup DENO_DIR warm-up.

    Warming happens off the UI thread, but a download that starts before it
    finishes races the plugin's cold script probe and loses -- the probe raises
    subprocess.TimeoutExpired straight out of ydl.download() and the whole item
    fails. Downloads wait on ``finished`` rather than hoping the warm-up won.

    Written by exactly one warm-up thread; ``lock`` guards only the start-once
    decision, and ``finished`` publishes ``result`` to readers.
    """

    def __init__(self) -> None:
        self.started = False
        self.finished = threading.Event()
        self.result: DenoWarmResult | None = None
        self.lock = threading.Lock()


_warm = _WarmupState()


def start_deno_warmup(
    server_home: Path | None = None,
    scripts_dir: Path | None = None,
) -> threading.Thread | None:
    """
    Warm DENO_DIR on a daemon thread, recording the outcome for ``wait_for_deno_warm``.

    Args:
        server_home: Provider server root; defaults to ``POT_PROVIDER_SERVER_HOME``.
        scripts_dir: Dir holding ``deno.exe``; defaults to ``VENV_SCRIPTS_DIR``.

    Returns:
        The started thread, or None if a warm-up was already started.
    """
    with _warm.lock:
        if _warm.started:
            return None
        _warm.started = True

    def _run() -> None:
        try:
            _warm.result = warm_deno_cache(
                server_home=server_home,
                scripts_dir=scripts_dir,
            )
        except Exception:
            # warm_deno_cache is documented not to raise, but this runs on a bare
            # thread: anything escaping here would go to threading.excepthook and
            # then to a stderr that pythonw discards, losing the diagnosis.
            logger.exception("Deno cache warm-up thread failed")
        finally:
            # Unconditional: a waiter blocked on an unset event would hang every
            # download for the full budget.
            _warm.finished.set()

    thread = threading.Thread(target=_run, name="deno-cache-warm", daemon=True)
    thread.start()
    return thread


def deno_warmup_pending() -> bool:
    """Return True if a warm-up is running, i.e. a waiter would actually block."""
    return _warm.started and not _warm.finished.is_set()


def wait_for_deno_warm(timeout: float = _DENO_WARM_TIMEOUT_S) -> DenoWarmResult | None:
    """
    Block until the startup warm-up finishes.

    Returns immediately when no warm-up was started (provider incomplete, or a
    context that never called ``start_deno_warmup`` such as the test suite), so
    this can be called unconditionally before a download.

    Args:
        timeout: Seconds to wait before giving up and letting the download proceed.

    Returns:
        The warm-up's ``DenoWarmResult``, or None when none ran or it timed out.
    """
    if not _warm.started:
        return None
    if not _warm.finished.wait(timeout):
        logger.warning(
            "Gave up after %.0fs waiting for the Deno cache warm-up; proceeding",
            timeout,
        )
        return None
    return _warm.result

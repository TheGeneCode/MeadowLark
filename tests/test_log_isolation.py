"""
The test session must never write to the user's real log files.

``error_log.txt`` / ``history_log.txt`` default to CWD-relative paths, which under
pytest is the repo root -- the same files the app writes in dev. Two layers keep
the suite off them, and these tests pin both:

- Only the entrypoint (``meadowlark.pyw`` -> ``init_app_logging``) attaches the
  root error-log handler; importing app modules never does.
- ``tests/conftest.py`` redirects every log path to a temp dir before
  ``src.config`` is first imported, for code that writes its files directly.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pytest

from src import logging_utils, qyt

_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("ERROR_LOG_PATH", logging_utils.ERROR_LOG_PATH),
        ("HISTORY_LOG_PATH", qyt.HistoryLogger.HISTORY_PATH),
        ("YTDLP_DEBUG_LOG_PATH", logging_utils.YTDLP_DEBUG_LOG_PATH),
    ],
)
def test_app_log_paths_resolve_under_temp_dir(name: str, path: Path) -> None:
    """The log paths the app modules bound at import point into the temp dir."""
    assert path.resolve().is_relative_to(_TEMP_ROOT), f"{name} = {path.resolve()}"


def test_importing_app_modules_attaches_no_root_file_handler() -> None:
    """Importing src.qyt (and its import graph) leaves root logging unconfigured."""
    # pytest's LoggingPlugin attaches its own FileHandler on os.devnull when no
    # --log-file is given; the null device is not a log anyone reads.
    null_device = Path(os.devnull).resolve()
    file_targets = [
        Path(h.baseFilename)
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() != null_device
    ]
    assert not file_targets, f"an import configured root logging: {file_targets}"

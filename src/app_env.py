"""
Where the user's ``.env`` lives, and loading it into the process environment once.

``src.config`` turns env vars into import-time ``Final`` constants, so the ``.env``
files must be loaded before config's body runs; config calls :func:`load_app_env`
first thing. This lives in its own module, not in config, because tests
``importlib.reload(config)`` after popping env vars: a ``load_dotenv`` inside
config would re-add the popped keys from the developer's ``.env`` on every reload.
"""

import functools
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

APPDATA_DIR: Final[Path] = Path.home() / "AppData" / "Roaming" / "MeadowLark"
USER_ENV_PATH: Final[Path] = APPDATA_DIR / ".env"


@functools.cache
def load_app_env() -> None:
    """
    Load the user's AppData ``.env``, then the project ``.env`` (dev), once per process.

    Neither call overrides a var already set, so the shell (and ``tests/conftest.py``)
    win over both files, and the AppData file wins over the project one.
    """
    load_dotenv(dotenv_path=USER_ENV_PATH if USER_ENV_PATH.exists() else None)
    load_dotenv()

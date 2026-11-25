"""Tests for src.app_env.load_app_env."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src import app_env


@pytest.fixture
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Give the test an unloaded load_app_env; afterwards leave it loaded again.

    Teardown must not leave the cache empty: the next importlib.reload(config) in
    another test would then re-run load_dotenv and re-add env vars that test popped
    (from the developer's .env), making its default-value assertions order-dependent.
    So re-prime the cache with load_dotenv stubbed out -- this runs before
    monkeypatch's own teardown restores the real load_dotenv.
    """
    app_env.load_app_env.cache_clear()
    yield
    app_env.load_app_env.cache_clear()
    monkeypatch.setattr(app_env, "load_dotenv", lambda *_a, **_k: False)
    app_env.load_app_env()


@pytest.mark.usefixtures("_fresh_cache")
class TestLoadAppEnv:
    def test_existing_var_not_overridden_and_missing_var_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "QA_APP_ENV_SET=from_file\nQA_APP_ENV_NEW=from_file\n", encoding="utf-8"
        )
        monkeypatch.setattr(app_env, "USER_ENV_PATH", env_file)
        monkeypatch.setenv("QA_APP_ENV_SET", "from_shell")
        monkeypatch.delenv("QA_APP_ENV_NEW", raising=False)
        try:
            app_env.load_app_env()
            assert os.environ["QA_APP_ENV_SET"] == "from_shell"
            assert os.environ["QA_APP_ENV_NEW"] == "from_file"
        finally:
            monkeypatch.delenv("QA_APP_ENV_NEW", raising=False)

    def test_second_call_does_not_readd_popped_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("QA_APP_ENV_POP=from_file\n", encoding="utf-8")
        monkeypatch.setattr(app_env, "USER_ENV_PATH", env_file)
        monkeypatch.delenv("QA_APP_ENV_POP", raising=False)
        try:
            app_env.load_app_env()
            assert os.environ["QA_APP_ENV_POP"] == "from_file"
            os.environ.pop("QA_APP_ENV_POP")
            app_env.load_app_env()
            assert "QA_APP_ENV_POP" not in os.environ
        finally:
            os.environ.pop("QA_APP_ENV_POP", None)

"""Unit tests for the typed runtime-setting accessors in src/settings_dialog.py."""

from unittest.mock import patch

import pytest

import src.settings_dialog as sd

_KEY = "VID_DL_TEST_KEY"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("C:/videos", "C:/videos"),
        ("", "fallback"),
        (None, "fallback"),
        (42, "fallback"),
        (True, "fallback"),
    ],
)
def test_get_str_setting_returns_non_empty_str_else_default(stored: object, expected: str) -> None:
    with patch.dict(sd._runtime, {_KEY: stored}):
        assert sd.get_str_setting(_KEY, "fallback") == expected


def test_get_str_setting_unregistered_key_returns_empty_default() -> None:
    with patch.dict(sd._runtime, {}, clear=True):
        assert sd.get_str_setting(_KEY) == ""


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (15, 15),
        ("30", 30),
        (0, 60),
        ("", 60),
        (None, 60),
        (True, 60),
        (2.5, 60),
    ],
)
def test_get_int_setting_returns_int_or_numeric_str_else_default(
    stored: object, expected: int
) -> None:
    with patch.dict(sd._runtime, {_KEY: stored}):
        result = sd.get_int_setting(_KEY, 60)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(" 30 ", 30), ("-5", -5), (-5, -5)],
)
def test_get_int_setting_padded_or_negative_values_pass_through(
    stored: object, expected: int
) -> None:
    """Only zero/empty fall back to the default; padding is parsed, negatives are not clamped."""
    with patch.dict(sd._runtime, {_KEY: stored}):
        assert sd.get_int_setting(_KEY, 60) == expected


def test_get_int_setting_whitespace_only_str_raises() -> None:
    """A blank-but-truthy str is corrupt, matching the old ``int(x or d)`` behaviour."""
    with patch.dict(sd._runtime, {_KEY: "  "}), pytest.raises(ValueError, match="invalid literal"):
        sd.get_int_setting(_KEY, 60)


def test_get_int_setting_non_numeric_str_raises() -> None:
    """A corrupt numeric setting is a config error, not something to paper over."""
    with patch.dict(sd._runtime, {_KEY: "soon"}), pytest.raises(ValueError, match="soon"):
        sd.get_int_setting(_KEY, 60)

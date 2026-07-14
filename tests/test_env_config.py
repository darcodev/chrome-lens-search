"""Env-derived defaults must never break `import glens` (they are parsed at
import time by _env_int/_env_flag, which swallow malformed values)."""

import pytest

from glens.lens import _env_flag, _env_int


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("On", True),
     ("0", False), ("off", False), ("maybe", False), ("", False)],
)
def test_env_flag(monkeypatch, raw, expected):
    monkeypatch.setenv("GLENS_TEST_FLAG", raw)
    assert _env_flag("GLENS_TEST_FLAG") is expected


def test_env_flag_unset(monkeypatch):
    monkeypatch.delenv("GLENS_TEST_FLAG", raising=False)
    assert _env_flag("GLENS_TEST_FLAG") is False


def test_unset_returns_default(monkeypatch):
    monkeypatch.delenv("GLENS_MAX_MATCHES", raising=False)
    assert _env_int("GLENS_MAX_MATCHES", 20) == 20


def test_blank_returns_default(monkeypatch):
    monkeypatch.setenv("GLENS_MAX_MATCHES", "   ")
    assert _env_int("GLENS_MAX_MATCHES", 20) == 20


def test_malformed_returns_default(monkeypatch):
    # A junk value must fall back, not raise at import time.
    monkeypatch.setenv("GLENS_MAX_MATCHES", "lots")
    assert _env_int("GLENS_MAX_MATCHES", 20) == 20


def test_valid_value_is_used(monkeypatch):
    monkeypatch.setenv("GLENS_MAX_MATCHES", "7")
    assert _env_int("GLENS_MAX_MATCHES", 20) == 7


def test_clamped_to_minimum(monkeypatch):
    monkeypatch.setenv("GLENS_RESULT_TIMEOUT", "1")
    assert _env_int("GLENS_RESULT_TIMEOUT", 25, minimum=5) == 5
    monkeypatch.setenv("GLENS_MAX_MATCHES", "-3")
    assert _env_int("GLENS_MAX_MATCHES", 20) == 1

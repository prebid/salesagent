"""Tests for database configuration helpers."""

import pytest

from src.core.database.db_config import int_env


class TestIntEnv:
    """int_env parses integer environment variables with friendly errors."""

    def test_returns_int_for_valid_env(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "42")
        assert int_env("TEST_INT_ENV", "0") == 42

    def test_returns_default_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_ENV", raising=False)
        assert int_env("TEST_INT_ENV", "7") == 7

    def test_returns_default_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "")
        assert int_env("TEST_INT_ENV", "9") == 9

    def test_raises_for_invalid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "not-a-number")
        with pytest.raises(ValueError, match="Invalid integer value for TEST_INT_ENV"):
            int_env("TEST_INT_ENV", "0")

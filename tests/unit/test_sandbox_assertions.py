"""Negative-path coverage for tests/helpers/sandbox_assertions.py.

The shared reader is exercised indirectly by every sandbox production-path test, but none
of those consumers ever call it with a missing kwarg or an empty call list — so a regression
turning either raise into a silent no-op would go completely undetected. These tests drive
those two branches directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.helpers.sandbox_assertions import assert_all_live, assert_all_sandbox, sandbox_modes


def _mock_with_calls(*sandbox_values: bool) -> MagicMock:
    mock = MagicMock()
    for value in sandbox_values:
        mock(object(), sandbox=value)
    return mock


class TestSandboxModes:
    def test_returns_the_recorded_values_in_call_order(self) -> None:
        mock = _mock_with_calls(True, False, True)
        assert sandbox_modes(mock) == [True, False, True]

    def test_raises_when_a_call_omitted_the_keyword(self) -> None:
        mock = MagicMock()
        mock(object())  # no sandbox= kwarg at all

        with pytest.raises(AssertionError, match="without an explicit sandbox="):
            sandbox_modes(mock)

    @pytest.mark.parametrize("value", [None, 0, 1, "", "true", "False"])
    def test_raises_on_a_non_bool_value(self, value: object) -> None:
        """A falsy-but-not-False value must not silently satisfy assert_all_live()."""
        mock = MagicMock()
        mock(object(), sandbox=value)

        with pytest.raises(AssertionError, match="non-bool sandbox="):
            sandbox_modes(mock)


class TestAssertAllSandbox:
    def test_passes_when_every_call_is_sandbox(self) -> None:
        assert_all_sandbox(_mock_with_calls(True, True), context="test")

    def test_raises_on_an_empty_call_list(self) -> None:
        with pytest.raises(AssertionError, match="would be vacuous"):
            assert_all_sandbox(MagicMock(), context="test")

    def test_raises_when_any_call_is_live(self) -> None:
        with pytest.raises(AssertionError, match="LIVE adapter"):
            assert_all_sandbox(_mock_with_calls(True, False), context="test")


class TestAssertAllLive:
    def test_passes_when_every_call_is_live(self) -> None:
        assert_all_live(_mock_with_calls(False, False), context="test")

    def test_raises_on_an_empty_call_list(self) -> None:
        with pytest.raises(AssertionError, match="would be vacuous"):
            assert_all_live(MagicMock(), context="test")

    def test_raises_when_any_call_is_sandbox(self) -> None:
        with pytest.raises(AssertionError, match="expected the live adapter"):
            assert_all_live(_mock_with_calls(False, True), context="test")

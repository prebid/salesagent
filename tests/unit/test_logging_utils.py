"""Tests for log-forging protection at dynamic logging boundaries."""

from __future__ import annotations

import logging

import pytest

from src.core.logging_utils import sanitize_log_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("line one\rline two", r"line one\rline two"),
        ("line one\nline two", r"line one\nline two"),
        ("line one\r\nline two", r"line one\r\nline two"),
        (42, "42"),
    ],
)
def test_sanitize_log_value_returns_single_line(raw: object, expected: str) -> None:
    assert sanitize_log_value(raw) == expected


def test_sanitize_log_value_truncates_deterministically() -> None:
    assert sanitize_log_value("abcdef", max_length=5) == "abcd…"
    assert sanitize_log_value("abcdef", max_length=1) == "…"


def test_sanitize_log_value_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="max_length must be positive"):
        sanitize_log_value("value", max_length=0)


def test_sanitized_value_cannot_forge_a_second_log_line(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.log_sanitizer")

    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("media buy %s", sanitize_log_value("safe\nFORGED record"))

    assert caplog.messages == [r"media buy safe\nFORGED record"]
    assert len(caplog.text.splitlines()) == 1

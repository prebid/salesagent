"""Tests for log-forging protection at dynamic logging boundaries."""

from __future__ import annotations

import logging

import pytest

from src.core.logging_config import SingleLineFormatter
from src.core.logging_utils import sanitize_log_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("line one\rline two", r"line one\rline two"),
        ("line one\nline two", r"line one\nline two"),
        ("line one\r\nline two", r"line one\r\nline two"),
        ("line one\vline two", r"line one\vline two"),
        ("line one\fline two", r"line one\fline two"),
        ("line one\x1cline two", r"line one\x1cline two"),
        ("line one\x1dline two", r"line one\x1dline two"),
        ("line one\x1eline two", r"line one\x1eline two"),
        ("line one\x85line two", r"line one\x85line two"),
        ("line one\u2028line two", r"line one\u2028line two"),
        ("line one\u2029line two", r"line one\u2029line two"),
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


def test_single_line_formatter_sanitizes_exception_traceback() -> None:
    formatter = SingleLineFormatter("%(levelname)s %(message)s")

    try:
        raise ValueError("remote failure\u2028FORGED record")
    except ValueError:
        record = logging.getLogger("tests.log_sanitizer").makeRecord(
            "tests.log_sanitizer",
            logging.ERROR,
            __file__,
            1,
            "operation failed",
            (),
            exc_info=__import__("sys").exc_info(),
        )

    rendered = formatter.format(record)
    assert len(rendered.splitlines()) == 1
    assert r"remote failure\u2028FORGED record" in rendered

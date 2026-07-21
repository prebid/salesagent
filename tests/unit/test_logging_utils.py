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
        ("line one\x00line two", r"line one\x00line two"),
        ("line one\x01line two", r"line one\x01line two"),
        ("line one\tline two", r"line one\tline two"),
        ("line one\x1bline two", r"line one\x1bline two"),
        ("line one\x1fline two", r"line one\x1fline two"),
        ("line one\x7fline two", r"line one\x7fline two"),
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


def test_sanitize_log_value_none_limit_disables_truncation() -> None:
    long_value = "x" * 2000
    assert sanitize_log_value(long_value, max_length=None) == long_value


def test_every_c0_control_and_del_is_escaped() -> None:
    for code in [*range(0x00, 0x20), 0x7F]:
        sanitized = sanitize_log_value(f"a{chr(code)}b")
        assert chr(code) not in sanitized, f"control 0x{code:02x} leaked through"
        assert sanitized.startswith("a\\") and sanitized.endswith("b")


def test_log_safe_delegates_to_shared_escape_table() -> None:
    from src.core.logging_config import log_safe

    assert log_safe("id\nFORGED") == r"id\nFORGED"
    assert log_safe("id\x1b[31mred") == r"id\x1b[31mred"
    # No truncation — log_safe wraps whole pre-formatted messages.
    assert log_safe("x" * 2000) == "x" * 2000


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


def test_json_formatter_emits_single_line_for_all_line_separators() -> None:
    """Production JSONFormatter must serialize a message carrying every line/record
    separator onto a single physical line.

    JSONFormatter relies on json.dumps' default ensure_ascii=True to escape the
    non-ASCII separators (NEL U+0085, LS U+2028, PS U+2029) that json is NOT
    otherwise required to escape; the C0 controls are always escaped. Pinning
    single-line output means a future switch to ensure_ascii=False (which would
    emit NEL/LS/PS raw) reddens this test.
    """
    import json

    from src.core.logging_config import JSONFormatter

    # CR, LF, VT, FF, FS, GS, RS, NEL, LS, PS, and a C0 control (SOH).
    separators = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029\x01"
    message = f"boundary{separators}FORGED record"

    record = logging.getLogger("tests.log_sanitizer").makeRecord(
        "tests.log_sanitizer",
        logging.INFO,
        __file__,
        1,
        message,
        (),
        None,
    )

    rendered = JSONFormatter().format(record)

    # str.splitlines() splits on every one of the separators above, so a single
    # element proves none of them survive raw in the serialized output.
    assert len(rendered.splitlines()) == 1, f"JSONFormatter emitted a multi-line record: {rendered!r}"
    for sep in separators:
        assert sep not in rendered, f"raw separator U+{ord(sep):04X} leaked into JSON output"
    # The output is still valid, parseable JSON and preserves the message.
    parsed = json.loads(rendered)
    assert parsed["message"] == message

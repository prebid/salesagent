"""Helpers for safely rendering untrusted values in log messages."""

from __future__ import annotations

_LINE_BREAK_ESCAPES = str.maketrans(
    {
        "\n": r"\n",
        "\r": r"\r",
        "\v": r"\v",
        "\f": r"\f",
        "\x1c": r"\x1c",
        "\x1d": r"\x1d",
        "\x1e": r"\x1e",
        "\x85": r"\x85",
        "\u2028": r"\u2028",
        "\u2029": r"\u2029",
    }
)


def sanitize_log_value(value: object, *, max_length: int = 500) -> str:
    """Return a bounded, single-line representation suitable for logging.

    Escaping every separator recognized by :meth:`str.splitlines` prevents
    untrusted values from forging additional physical log records.
    """
    if max_length < 1:
        raise ValueError("max_length must be positive")

    sanitized = str(value).translate(_LINE_BREAK_ESCAPES)
    if len(sanitized) <= max_length:
        return sanitized
    if max_length == 1:
        return "…"
    return f"{sanitized[: max_length - 1]}…"

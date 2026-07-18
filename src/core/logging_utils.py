"""Helpers for safely rendering untrusted values in log messages."""

from __future__ import annotations


def sanitize_log_value(value: object, *, max_length: int = 500) -> str:
    """Return a bounded, single-line representation suitable for logging.

    Escaping carriage returns and newlines prevents untrusted values from
    forging additional physical log records in human-readable log formats.
    """
    if max_length < 1:
        raise ValueError("max_length must be positive")

    sanitized = str(value).replace("\r", r"\r").replace("\n", r"\n")
    if len(sanitized) <= max_length:
        return sanitized
    if max_length == 1:
        return "…"
    return f"{sanitized[: max_length - 1]}…"

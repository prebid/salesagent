"""Helpers for safely rendering untrusted values in log messages."""

from __future__ import annotations

# Common controls keep their conventional short escapes; every other C0
# control (0x00-0x1f) and DEL (0x7f) falls back to \xNN. NEL and the Unicode
# line separators are escaped too — str.splitlines() treats them as breaks.
_NAMED_ESCAPES: dict[int, str] = {
    0x09: r"\t",
    0x0A: r"\n",
    0x0B: r"\v",
    0x0C: r"\f",
    0x0D: r"\r",
}

_CONTROL_ESCAPES = str.maketrans(
    {
        **{code: _NAMED_ESCAPES.get(code, f"\\x{code:02x}") for code in [*range(0x00, 0x20), 0x7F]},
        0x85: r"\x85",
        0x2028: "\\u2028",
        0x2029: "\\u2029",
    }
)


def sanitize_log_value(value: object, *, max_length: int | None = 500) -> str:
    """Return a bounded, single-line representation suitable for logging.

    Escaping every C0 control character (plus DEL, NEL, and the Unicode line
    separators) prevents untrusted values from forging additional physical log
    records or smuggling terminal control sequences. ``max_length=None``
    disables truncation (for callers sanitizing a whole pre-formatted message
    rather than a single untrusted value).
    """
    if max_length is not None and max_length < 1:
        raise ValueError("max_length must be positive")

    sanitized = str(value).translate(_CONTROL_ESCAPES)
    if max_length is None or len(sanitized) <= max_length:
        return sanitized
    if max_length == 1:
        return "…"
    return f"{sanitized[: max_length - 1]}…"

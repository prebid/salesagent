"""Shared log-injection (CWE-117) neutralization for buyer-supplied values.

Any value that originates from a request and reaches an operator log line must be
scrubbed of control characters first, or a buyer can embed newlines and forge log
entries. This is the single home for that scrub so callers do not each reinvent it
(``loggable_list_id`` and the idempotency replay engine both delegate here).

This is distinct from ``sanitize_for_logging`` in the GAM formatters, which redacts
*sensitive fields* from structured data — a different concern (secret hygiene, not
injection).
"""

from __future__ import annotations


def loggable(value: object) -> str:
    """Return ``value`` as a string with non-printable characters removed.

    Strips CR/LF/TAB and other control characters so an attacker-influenced value
    cannot inject forged log lines. Printable content (including spaces) is kept.
    Does not assume the value was validated upstream — it is safe to log regardless.
    """
    return "".join(ch for ch in str(value) if ch.isprintable())

"""Shared oracle for 'no raw secret leaked to a buyer-facing wire'.

The token set IS the definition of what counts as a leak — keep it in ONE place so
tightening it (adding a new secret shape) is a single edit, not an N-site lockstep
update where a missed copy silently under-asserts.
"""

from __future__ import annotations

import json

# A message shaped like the worst realistic leak: a connection string with credentials,
# a bearer token, an internal host, and inline SQL — everything a scrubbed error must never
# echo to the buyer.
SECRET_BEARING_MESSAGE = "postgresql://svc:hunter2@db.internal/prod TOKEN=abc123 SELECT * FROM principals"

_SECRET_TOKENS = ("hunter2", "postgresql://", "db.internal", "TOKEN=abc123", "SELECT", "principals")


def assert_no_secret_leak(blob: object, *, context: str = "") -> None:
    """Assert no ``SECRET_BEARING_MESSAGE`` token appears in ``blob`` (a str, or a JSON-able
    envelope/dict). Raises with the offending token and a truncated haystack."""
    haystack = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    where = f" ({context})" if context else ""
    for token in _SECRET_TOKENS:
        assert token not in haystack, (
            f"secret fragment {token!r} leaked to the buyer-facing wire{where}: {haystack[:300]}"
        )

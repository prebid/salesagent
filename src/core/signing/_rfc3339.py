"""One RFC 3339 formatter, shared by every trust-root/governance document builder.

Promoted out of ``trust_root.py`` (#1291 A5 follow-up, salesagent-z6nr.27) so
``revocation_list.py`` does not grow a second spelling of the same format —
two formatters that happen to agree today is exactly the kind of divergence
the codebase-scan atom for this ticket flagged (``trust_root._rfc3339`` vs. a
test module's own ``strftime`` producing different bytes for the same
instant). Both builder modules import this one function; neither imports the
other.
"""

from __future__ import annotations

from datetime import datetime


def rfc3339(moment: datetime) -> str:
    """RFC 3339 with an explicit offset, as ``format: date-time`` requires.

    Requires an AWARE ``moment`` — a naive datetime's ``isoformat()`` emits no
    offset at all, which silently violates the format callers publish under.
    """
    if moment.tzinfo is None:
        raise ValueError(f"rfc3339() requires a timezone-aware datetime, got naive {moment!r}")
    return moment.isoformat()

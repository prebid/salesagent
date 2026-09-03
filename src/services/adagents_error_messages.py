"""Non-disclosing messages for adcp's adagents.json fetch/validation exceptions.

Core Invariant (GH #1802): whatever the adcp library's own exception
text says (``str(exc)``) never reaches a persisted row or an admin-facing
response -- the library's ``AdagentsValidationError`` can carry a resolved
IP address and an SSRF range classification (confirmed by reading
``adcp.signing.jwks``'s SSRF validator and ``adcp.adagents``'s pinned-client
error re-raise, adcp==6.6.0), so echoing it verbatim is an internal-network
disclosure, not a hygiene issue. This mirrors the split
``src/core/security/outbound_http.py``'s callers already use: full detail to
the log (operator-only), a fixed opaque message to anyone else.

The one function here is the single place that decides what "publisher
adagents.json failed to fetch/validate" means to a caller -- every catch site
across the codebase must route through it rather than building its own
prefix + ``str(exc)`` string.
"""

from __future__ import annotations

from adcp import (
    AdagentsAccessBlockedError,
    AdagentsNotFoundError,
    AdagentsTimeoutError,
    AdagentsValidationError,
)


def describe_adagents_error(exc: Exception) -> str:
    """Return a fixed, non-disclosing message for an adagents.json fetch/validation failure.

    Checked most-specific-first: ``AdagentsAccessBlockedError``,
    ``AdagentsNotFoundError`` and ``AdagentsTimeoutError`` are all subclasses
    of ``AdagentsValidationError``, so that check must come last or it would
    swallow the more specific cases.
    """
    if isinstance(exc, AdagentsAccessBlockedError):
        return "adagents.json fetch was blocked"
    if isinstance(exc, AdagentsNotFoundError):
        return "adagents.json not found for this domain"
    if isinstance(exc, AdagentsTimeoutError):
        return "Timed out fetching adagents.json"
    if isinstance(exc, AdagentsValidationError):
        return "adagents.json could not be validated"
    return "adagents.json verification failed"

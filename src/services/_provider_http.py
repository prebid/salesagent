"""Shared HTTP helpers for outbound TMP Provider calls.

Both the health-check scheduler (``tmp_health_scheduler.py``) and the package
sync service (``tmp_provider_sync.py``) make HTTP calls to TMP Provider
endpoints.  Centralising the URL-building and auth-header helpers here ensures
every outbound call inherits the same hardening (trailing-slash normalisation,
``follow_redirects=False`` reminder) rather than each call site re-implementing
it independently.
"""

from __future__ import annotations


def provider_url(endpoint: str, path: str) -> str:
    """Build a full URL for a TMP Provider path.

    Strips any trailing slash from *endpoint* before joining so callers
    don't need to remember to normalise the stored value.

    Args:
        endpoint: Base endpoint URL as stored in the DB (e.g. ``"http://tmp:3000/"``).
        path: Path to append (e.g. ``"/packages/sync"`` or ``"/health"``).
    """
    return endpoint.rstrip("/") + path


def bearer_headers(auth_credentials: str) -> dict[str, str]:
    """Build HTTP headers for a TMP Provider request.

    Returns an ``Authorization: Bearer`` header when *auth_credentials* is
    non-empty, otherwise an empty dict.  Centralising this ensures every
    outbound call inherits the same auth shape — no per-call copy-paste.
    """
    if auth_credentials:
        return {"Authorization": f"Bearer {auth_credentials}"}
    return {}

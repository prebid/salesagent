"""Shared HTTP helpers for outbound TMP Provider calls.

Both the health-check scheduler (``tmp_health_scheduler.py``) and the package
sync service (``tmp_provider_sync.py``) make HTTP calls to TMP Provider
endpoints.  Centralising the URL-building and auth-header helpers here ensures
every outbound call inherits the same hardening (trailing-slash normalisation,
``follow_redirects=False``) rather than each call site re-implementing
it independently.
"""

from __future__ import annotations

from typing import Any

# Default timeout for synchronous package-sync calls (seconds).
# Kept short — TMP Provider is an internal service on the same network.
_DEFAULT_SYNC_TIMEOUT_S = 5.0


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


def provider_client_kwargs(timeout: float = _DEFAULT_SYNC_TIMEOUT_S) -> dict[str, Any]:
    """Return shared ``httpx.Client`` / ``httpx.AsyncClient`` constructor kwargs.

    Centralises the two flags that every outbound TMP Provider call must set:

    - ``follow_redirects=False`` — prevents SSRF via open-redirect on both the
      GET (health probe) and POST (package sync) sides.  This flag was forgotten
      once on the POST side (round 7) and must never be omitted again.
    - ``timeout`` — callers may override for async health probes (which use a
      different constant) but the default matches the sync package-sync timeout.

    Usage::

        import httpx
        from src.services._provider_http import provider_client_kwargs

        # Sync (package sync):
        with httpx.Client(**provider_client_kwargs()) as client:
            resp = client.post(url, json=payloads, headers=headers)

        # Async (health scheduler) — override timeout:
        async with httpx.AsyncClient(**provider_client_kwargs(timeout=5)) as client:
            resp = await client.get(health_url)
    """
    return {"timeout": timeout, "follow_redirects": False}

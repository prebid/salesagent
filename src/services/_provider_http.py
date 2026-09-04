"""Shared request-shaping helpers for outbound TMP Provider calls.

Both the health-check scheduler (``tmp_health_scheduler.py``) and the package
sync service (``tmp_provider_sync.py``) address TMP Provider endpoints, so the
URL building and the auth header live here rather than once per call site.

What this module deliberately does NOT own any more is transport hardening.
``follow_redirects=False`` and the shared client kwargs used to live here; #1802
moved outbound HTTP onto ``src.core.security.outbound_http``, which owns address
policy, TLS, redirect refusal and retry classification for every call in the
application. Two call sites naming those flags identically was the shape that
made it possible to forget one (the POST side did, once) — the seam removes the
opportunity rather than the mistake.
"""

from __future__ import annotations

from src.core.schemas.tmp_provider import VALID_AUTH_SCHEMES

# Default timeout for synchronous package-sync calls (seconds).
# Kept short — TMP Provider is an internal service on the same network.
# Named with the *_SECONDS suffix (matching HEALTH_CHECK_TIMEOUT_SECONDS,
# HEALTH_CHECK_INTERVAL_SECONDS, STATUS_CHECK_INTERVAL_SECONDS) so a grep for
# *_SECONDS finds every duration constant this feature touches.
_DEFAULT_SYNC_TIMEOUT_SECONDS = 5.0


def provider_url(endpoint: str, path: str) -> str:
    """Build a full URL for a TMP Provider path.

    Strips any trailing slash from *endpoint* before joining so callers
    don't need to remember to normalise the stored value.

    Args:
        endpoint: Base endpoint URL as stored in the DB (e.g. ``"http://tmp:3000/"``).
        path: Path to append (e.g. ``"/packages/sync"`` or ``"/health"``).
    """
    return endpoint.rstrip("/") + path


def provider_auth_headers(auth_type: str | None, auth_credentials: str) -> dict[str, str]:
    """Build the auth headers for one outbound TMP Provider request.

    Returns an empty dict when the provider has no credential — an
    unauthenticated provider is a supported registration. A credential with no
    explicit ``auth_type`` is sent as Bearer: that is the only scheme implemented,
    and it is what every previously-stored registration already got.

    An ``auth_type`` outside :data:`VALID_AUTH_SCHEMES` cannot reach here from
    any write surface (the record types the field), so it is a programming error
    rather than operator input — hence a raise, not a silent fallback that would
    reintroduce "the selected scheme is ignored".
    """
    if not auth_credentials:
        return {}
    scheme = auth_type or "bearer"
    if scheme not in VALID_AUTH_SCHEMES:
        raise ValueError(
            f"Unsupported TMP provider auth scheme {scheme!r}; expected one of {sorted(VALID_AUTH_SCHEMES)}"
        )
    return {"Authorization": f"Bearer {auth_credentials}"}

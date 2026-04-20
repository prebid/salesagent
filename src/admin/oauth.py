"""Authlib ``starlette_client`` OAuth singleton + byte-immutable callback paths.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.6.

Replaces the Flask-client OAuth setup at ``src/admin/blueprints/auth.py``.
The ``starlette_client`` API is a drop-in match for Authlib's Flask
client — same ``register()`` / ``authorize_redirect()`` /
``authorize_access_token()`` method names — but uses Starlette
``Request`` / ``Response`` types.

Critical Invariant #6 — byte-immutable OAuth redirect URIs
===========================================================

The three ``OAUTH_*_CALLBACK_PATH`` module constants below are a
contract with Google Cloud Console and per-tenant OIDC provider
registration. **Any drift** — trailing slash, case change, prefix
change, or a ``{tenant_id}`` path parameter — produces a
``redirect_uri_mismatch`` error at login time and kills authentication.
The constants are exactly:

  * ``/admin/auth/google/callback``
  * ``/admin/auth/oidc/callback``     — NO ``{tenant_id}`` segment
                                         (tenant context is in the
                                         session, not the URL — per
                                         FE-3 audit correction
                                         2026-04-11)
  * ``/admin/auth/gam/callback``      — WITH ``/admin/`` prefix
                                         (the prefix IS part of the
                                         registered URI — per FE-3
                                         audit correction 2026-04-11)

Guard: ``tests/unit/admin/test_oauth_constants.py`` + (at L1a)
``tests/unit/test_oauth_redirect_uris_immutable.py``.

Client tiers
============

1. **Global ``google`` client** — registered at startup by
   :func:`init_oauth`, serves the default OIDC flow.
2. **Per-tenant clients** — lazily registered on first use in
   :func:`get_tenant_oidc_client`, cached in ``_tenant_client_cache``,
   evicted by :func:`invalidate_tenant_oidc_client` when an admin
   rotates their client secret or disables OIDC.

L0 scope
========

This module is scaffold-only at L0. It is NOT wired into
``src/app.py`` yet — callback route handlers land at L1b per the
canonical layer plan. At L0 only the constants, the ``OAuth`` instance,
and the three helper functions (:func:`init_oauth`,
:func:`get_tenant_oidc_client`, :func:`invalidate_tenant_oidc_client`)
must exist with the canonical API shapes.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client.apps import StarletteOAuth2App

logger = logging.getLogger(__name__)


# =====================================================================
# Byte-immutable OAuth redirect URIs — Critical Invariant #6
# =====================================================================
# DO NOT EDIT these strings without updating Google Cloud Console AND
# every tenant's OIDC provider registration in lock-step. See
# ``CLAUDE.md`` §Critical Invariants and
# ``flask-to-fastapi-deep-audit.md`` §1 blocker 6.

OAUTH_GOOGLE_CALLBACK_PATH: str = "/admin/auth/google/callback"
"""Google OAuth callback URI — byte-immutable per Invariant #6."""

OAUTH_OIDC_CALLBACK_PATH: str = "/admin/auth/oidc/callback"
"""Per-tenant OIDC callback URI — NO ``{tenant_id}`` segment.

Tenant context is resolved from ``request.session["tenant_id"]`` inside
the handler, not from a URL path parameter. Verified at
``src/admin/blueprints/oidc.py:209,215`` (legacy Flask) and enforced at
L1a by the route registration in ``src/admin/routers/auth.py``.
"""

OAUTH_GAM_CALLBACK_PATH: str = "/admin/auth/gam/callback"
"""Google Ad Manager OAuth callback URI — WITH the ``/admin/`` prefix.

The ``/admin/`` prefix is part of the registered URI in Google Cloud
Console. Verified at ``src/admin/blueprints/auth.py:931,959`` (legacy
Flask) and enforced at L1a by the route registration.
"""

GOOGLE_CLIENT_NAME: str = "google"
"""Authlib registration key for the global Google OAuth client.

Referenced by :func:`init_oauth` at registration time and by callback
handlers (L1b) when looking up the client via ``oauth.google`` /
``getattr(oauth, GOOGLE_CLIENT_NAME)``.
"""


# =====================================================================
# OAuth registry + per-tenant client cache
# =====================================================================

oauth: OAuth = OAuth()
"""Module-level Authlib ``OAuth`` singleton.

Safe as a module singleton because ``OAuth`` is a registration registry
— its internal state is only written at startup (:func:`init_oauth`)
and at tenant-cache invalidation
(:func:`invalidate_tenant_oidc_client`).
"""

_tenant_client_cache: dict[str, StarletteOAuth2App] = {}
_cache_lock = threading.Lock()

# Callable injected by the app wire-up layer (L1b) that resolves a
# tenant's OIDC config dict from the DB. Kept as a module-level slot
# so L0 can scaffold :func:`get_tenant_oidc_client` without importing
# the services layer and transitively pulling in the broader database
# package (which has in-flight mypy regressions being addressed in
# middle migration layers). L1b sets this to
# ``src.services.auth_config_service.get_oidc_config_for_auth``.
_oidc_config_resolver: Any = None


def set_oidc_config_resolver(resolver: Any) -> None:
    """Wire the per-tenant OIDC config resolver (called at L1b startup).

    At L0 the resolver is ``None`` and :func:`get_tenant_oidc_client`
    returns ``None`` unconditionally (scaffold-only). The production
    resolver is injected by ``src/admin/app_factory.py`` once the admin
    router is assembled, so no code path actually exercises the
    tenant-cache branch at L0.
    """
    global _oidc_config_resolver
    _oidc_config_resolver = resolver


# =====================================================================
# Public API
# =====================================================================


def init_oauth() -> None:
    """Register the default global Google OIDC client.

    Called once at startup by :func:`src.admin.app_factory.build_admin_router`.
    Reads OAuth env vars once (not on every request) and is idempotent:
    when the ``google`` client is already registered, subsequent calls
    are a no-op.

    Missing env vars log a warning and skip registration — the provider
    is then simply unavailable, and the login route will 503 at
    callback time rather than crashing startup.
    """
    google_id = os.environ.get("GOOGLE_CLIENT_ID") or os.environ.get("OAUTH_CLIENT_ID")
    google_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or os.environ.get("OAUTH_CLIENT_SECRET")
    discovery = os.environ.get(
        "OAUTH_DISCOVERY_URL",
        "https://accounts.google.com/.well-known/openid-configuration",
    )
    if not google_id or not google_secret:
        logger.warning(
            "OAuth client env vars not set — default provider unavailable. "
            "Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET or OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET."
        )
        return

    # Idempotency guard — tolerate repeated calls from tests or a
    # duplicated lifespan hook.
    if getattr(oauth, GOOGLE_CLIENT_NAME, None) is not None:
        logger.debug("OAuth %r client already registered, skipping", GOOGLE_CLIENT_NAME)
        return

    oauth.register(
        name=GOOGLE_CLIENT_NAME,
        client_id=google_id,
        client_secret=google_secret,
        server_metadata_url=discovery,
        client_kwargs={"scope": os.environ.get("OAUTH_SCOPES", "openid email profile")},
    )
    logger.info("Registered global OAuth client: %s (%s)", GOOGLE_CLIENT_NAME, discovery)


def get_tenant_oidc_client(tenant_id: str) -> StarletteOAuth2App | None:
    """Return a cached or freshly-registered per-tenant OIDC client.

    Returns ``None`` when the tenant has not configured OIDC. Callers
    fall back to the default ``oauth.google`` client.

    Thread safety: the check-then-register sequence is guarded by
    ``_cache_lock``. Without the lock, two concurrent requests for the
    same uncached tenant would both call ``oauth.register()`` and
    Authlib would raise ``OAuthError('OAuth client with name=...
    already exists')``.
    """
    # Fast path: lock-free read.
    client = _tenant_client_cache.get(tenant_id)
    if client is not None:
        return client

    # L0 scaffold-only: the per-tenant DB-backed OIDC config resolver
    # is injected via :func:`set_oidc_config_resolver` at L1b startup.
    # See module docstring for the rationale on not importing the
    # ``src.services.auth_config_service`` module directly here.
    resolver = _oidc_config_resolver
    if resolver is None:
        return None

    with _cache_lock:
        # Re-check under lock — another thread may have just populated.
        client = _tenant_client_cache.get(tenant_id)
        if client is not None:
            return client

        config = resolver(tenant_id)
        if not config:
            return None

        name = f"tenant_{tenant_id}"
        # Authlib raises on duplicate names — evict any stale entry.
        internal_clients: dict[str, Any] = getattr(oauth, "_clients", {})
        if name in internal_clients:
            del internal_clients[name]

        oauth.register(
            name=name,
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            server_metadata_url=config["discovery_url"],
            client_kwargs={"scope": config.get("scopes", "openid email profile")},
        )
        client = getattr(oauth, name)
        _tenant_client_cache[tenant_id] = client
        logger.info("Registered tenant OIDC client: %s", name)
        return client


def invalidate_tenant_oidc_client(tenant_id: str) -> None:
    """Evict a tenant's cached OIDC client.

    Callers:
      * Settings routes that save a new OIDC config (client secret /
        discovery URL rotation).
      * Settings routes that disable OIDC for a tenant.
      * Tenant deletion.

    Under multi-worker deployments, this only invalidates the CURRENT
    worker. Other workers continue to use the stale client until they
    also process an invalidation. A later v2.0 phase moves this cache
    to Redis for cross-worker invalidation.
    """
    with _cache_lock:
        client = _tenant_client_cache.pop(tenant_id, None)
        name = f"tenant_{tenant_id}"
        internal_clients: dict[str, Any] = getattr(oauth, "_clients", {})
        if name in internal_clients:
            del internal_clients[name]
        if client is not None:
            logger.info("Invalidated tenant OIDC client: %s", name)

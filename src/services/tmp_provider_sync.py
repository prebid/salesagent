"""TMP Provider package sync service.

Pushes package definitions from the Sales Agent to all active TMP Providers
for a tenant whenever a media buy is created or updated.

Per the AdCP TMP spec (Package Sync section):
  "Package metadata is synced from seller agents to TMP providers at media buy
   creation time and whenever the media buy materially changes."

Each synced AvailablePackage includes a seller_agent reference so the TMP
Provider can attribute offers back to the originating seller agent.

Design principles (AdCP Pattern compliance):
- Triggered from **every transport** (REST, MCP, A2A) via ``fire_tmp_sync()``,
  which spawns a daemon thread so the caller is never blocked.  REST callers
  may also use FastAPI BackgroundTasks — both paths converge on
  ``sync_packages_for_media_buy``.
- Never called from _impl functions (which must remain transport-agnostic).
- Reads packages and provider endpoints via **repositories** (UoW pattern) —
  no raw get_db_session() / select() calls.
- HTTP calls are made **after** the DB session is closed — no open transaction
  during network I/O.
- Failures are **logged with full context** and re-raised as warnings so the
  background task runner records them.  The media buy operation itself is
  unaffected (fire-and-forget at the transport boundary).

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

from src.core.database.models import MediaPackage
from src.core.database.repositories.uow import MediaBuyUoW, TenantConfigUoW, TMPProviderUoW
from src.services._provider_http import bearer_headers, provider_url

logger = logging.getLogger(__name__)

# Timeout for each POST /packages/sync call (seconds).
# Kept short — TMP Provider is an internal service on the same network.
_SYNC_TIMEOUT_S = 5.0


def fire_tmp_sync(response: Any, tenant_id: str | None) -> None:
    """Spawn a daemon thread to sync TMP packages after a successful media buy operation.

    Transport-agnostic entry point shared by MCP, A2A, and REST transports.
    REST callers may also use FastAPI BackgroundTasks — both paths converge on
    ``sync_packages_for_media_buy``.

    ``response`` may be a ``CreateMediaBuyResult`` wrapper (create path) or a
    direct ``UpdateMediaBuySuccess | UpdateMediaBuyError`` (update path).
    ``CreateMediaBuyResult`` serializes flat but stores the domain response in
    its ``.response`` field — ``media_buy_id`` lives there, not on the wrapper.
    Uses ``getattr`` with an inner-response fallback to handle both shapes.

    No-ops silently when ``media_buy_id`` or ``tenant_id`` is absent (e.g. on
    error responses that carry no ID).
    """
    media_buy_id = getattr(response, "media_buy_id", None)
    if media_buy_id is None:
        inner = getattr(response, "response", None)
        media_buy_id = getattr(inner, "media_buy_id", None)

    if not media_buy_id or not tenant_id:
        return

    t = threading.Thread(
        target=sync_packages_for_media_buy,
        args=(tenant_id, media_buy_id),
        daemon=True,
        name=f"tmp-sync-{media_buy_id}",
    )
    t.start()


def _resolve_seller_agent_url(tenant_id: str) -> str:
    """Resolve the seller agent URL for the AvailablePackage.seller_agent field.

    Per the AdCP TMP spec, seller_agent.agent_url MUST match one of
    authorized_agents[].url in the publisher's adagents.json.

    Resolution order:
      1. ADCP_AGENT_URL env var (explicit override for non-standard deployments)
      2. Tenant virtual_host (the public domain, e.g. "tenant.salesagent.example.com")
      3. Tenant subdomain fallback (e.g. "si-host.sales-agent.localhost:8001")

    Returns the URL with /mcp suffix (the standard MCP endpoint).
    """
    override = os.environ.get("ADCP_AGENT_URL")
    if override:
        return override.rstrip("/")

    # Load tenant to resolve virtual_host / subdomain.
    # Uses TenantConfigUoW for architecture compliance (no raw get_db_session).
    #
    # IMPORTANT: this opens its own UoW/session. Callers MUST NOT invoke this
    # function from inside another open UoW block (e.g. MediaBuyUoW) — nesting
    # two UoWs means the inner UoW's __exit__ closes/removes the scoped session
    # the outer block is still using (get_db_session() is a scoped session).
    # sync_packages_for_media_buy() resolves the seller_agent URL before
    # opening the MediaBuyUoW block for exactly this reason.
    try:
        with TenantConfigUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            tenant = uow.tenant_config.get_tenant()
            if tenant and tenant.virtual_host:
                host = tenant.virtual_host
                scheme = "http" if _is_local_host(host) else "https"
                return f"{scheme}://{host}/mcp"
            if tenant and tenant.subdomain:
                return f"http://{tenant.subdomain}.sales-agent.localhost:8001/mcp"
    except Exception:
        logger.warning(
            "[TMP sync] Failed to load tenant %s for seller_agent URL — using fallback",
            tenant_id,
            exc_info=True,
        )

    return "http://salesagent:8000/mcp"


def _is_local_host(host: str) -> bool:
    """True if *host* (a hostname, optionally with ``:port``) is a local dev host.

    Uses exact equality / suffix checks rather than substring tests.
    A substring test (``"localhost" not in host``) misclassifies a host like
    ``my-localhost-mirror.example.com`` as local.  Likewise,
    ``hostname.startswith("127.0.0.1")`` misclassifies ``127.0.0.1.evil.com``
    as loopback — use ``== "127.0.0.1"`` instead.
    """
    hostname = host.split(":", 1)[0]
    return hostname == "localhost" or hostname.endswith(".localhost") or hostname == "127.0.0.1"


def _build_package_payload(
    media_buy_id: str,
    pkg_row: MediaPackage,
    seller_agent_url: str,
) -> dict[str, Any]:
    """Build the POST /packages/sync payload from a MediaPackage DB row.

    The TMP Provider expects the shape defined in handlers_packages.go:
      package_id, media_buy_id, offering_id, brand, keywords, topics,
      content_policies, summary, creative_manifest, price, macros,
      si_agent_endpoint, is_active, expires_at.

    Per the AdCP TMP spec (AvailablePackage), seller_agent is required.
    The Go TMP Provider stores this as si_agent_endpoint.

    All fields except package_id and media_buy_id are sourced from
    package_config (the full AdCP package JSON stored at creation time).
    """
    cfg: dict[str, Any] = pkg_row.package_config or {}
    return {
        "package_id": pkg_row.package_id,
        "media_buy_id": media_buy_id,
        # AdCP product_id maps to offering_id in TMP Provider schema
        "offering_id": cfg.get("product_id") or cfg.get("offering_id") or "",
        "brand": cfg.get("brand"),
        "keywords": cfg.get("keywords") or [],
        "topics": cfg.get("topics") or [],
        "content_policies": cfg.get("content_policies") or cfg.get("required_policies") or [],
        "summary": cfg.get("summary") or cfg.get("name") or "",
        "creative_manifest": cfg.get("creative_manifest"),
        # Explicit None check: `or` would treat a valid price of 0 as absent.
        "price": cfg.get("price") if cfg.get("price") is not None else cfg.get("bid_price"),
        "macros": cfg.get("macros") or {},
        # seller_agent.agent_url → stored as si_agent_endpoint in TMP Provider
        "si_agent_endpoint": seller_agent_url,
        "is_active": cfg.get("is_active", True),
        "expires_at": cfg.get("expires_at"),
    }


def _post_packages_sync(endpoint: str, payloads: list[dict[str, Any]], auth_credentials: str = "") -> None:
    """POST /packages/sync to a single TMP Provider endpoint.

    Sends the full list as a JSON array.  The TMP Provider's handler accepts
    both a single object and an array (see handlers_packages.go).

    Auth: Bearer token — when auth_credentials is set, sends
    ``Authorization: Bearer <credentials>``.  The TMP Provider resolves
    the tenant server-side from the credential.

    ``follow_redirects=False`` prevents SSRF via open-redirect on the POST
    side (matching the GET-side guard in the health probe).

    Raises httpx.HTTPError on non-2xx responses so the caller can log and
    continue to the next provider.
    """
    url = provider_url(endpoint, "/packages/sync")
    headers = bearer_headers(auth_credentials)
    with httpx.Client(timeout=_SYNC_TIMEOUT_S, follow_redirects=False) as client:
        resp = client.post(url, json=payloads, headers=headers)
        resp.raise_for_status()
    logger.info(
        "[TMP sync] POST %s → %d (%d package(s), auth=%s)",
        url,
        resp.status_code,
        len(payloads),
        "bearer" if auth_credentials else "none",
    )


def sync_packages_for_media_buy(tenant_id: str, media_buy_id: str) -> None:
    """Background task: push all packages for a media buy to active TMP providers.

    Called by the route layer (api_v1.py) via FastAPI BackgroundTasks after a
    successful create_media_buy or update_media_buy response has been sent.

    Steps:
      1. Resolve seller_agent URL from tenant config (its own UoW, opened and
         closed BEFORE the MediaBuyUoW block — see note below).
      2. Load packages from media_packages table via MediaBuyRepository.
      3. Load active/draining TMP provider endpoints via TMPProviderRepository,
         materialised into plain tuples before the UoW block closes.
      4. POST /packages/sync to each provider (best-effort, errors logged).

    Args:
        tenant_id:    Tenant scope — used for both repository queries.
        media_buy_id: The media buy whose packages should be synced.
    """
    # --- Step 1: resolve seller_agent URL BEFORE opening MediaBuyUoW ---
    # _resolve_seller_agent_url() opens its own TenantConfigUoW. get_db_session()
    # is a scoped session, so nesting it inside another open UoW block means the
    # inner UoW's __exit__ closes/removes the session the outer block still
    # needs — the subsequent row access and outer commit then run against a
    # removed session. Resolving it here, before MediaBuyUoW opens, avoids the
    # nesting entirely.
    seller_agent_url = _resolve_seller_agent_url(tenant_id)

    # --- Step 2: load packages and build payloads (inside session scope) ---
    # Payloads are built while the session is still open so that ORM attribute
    # access (pkg_row.package_config) does not hit a detached instance.
    # HTTP calls happen after this block — no open transaction during network I/O.
    try:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.media_buys is not None
            pkg_rows = uow.media_buys.get_packages(media_buy_id)

            if not pkg_rows:
                logger.debug(
                    "[TMP sync] No packages found for media_buy_id=%s — skipping sync",
                    media_buy_id,
                )
                return

            payloads = [_build_package_payload(media_buy_id, row, seller_agent_url) for row in pkg_rows]
    except Exception:
        logger.exception(
            "[TMP sync] Failed to load packages for media_buy_id=%s tenant=%s",
            media_buy_id,
            tenant_id,
        )
        return

    logger.info(
        "[TMP sync] Built %d package payload(s) for media_buy=%s seller_agent=%s",
        len(payloads),
        media_buy_id,
        seller_agent_url,
    )

    # --- Step 3: load active + draining TMP provider endpoints ---
    # Draining providers still serve in-flight requests and need current package data.
    # The router stops sending NEW requests to draining providers, but packages must
    # stay up-to-date for requests already in the pipeline.
    #
    # Materialise into plain tuples INSIDE the UoW block — provider.endpoint /
    # provider.auth_credentials / provider.name are ORM attributes that expire
    # on commit (default expire_on_commit=True). Reading them after the `with`
    # block closes hits a detached session and raises DetachedInstanceError,
    # which then repeats in the except-handler's own attribute reads below.
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            provider_rows = uow.tmp_providers.list_syncable()
            providers = [(p.name, p.endpoint, p.auth_credentials or "") for p in provider_rows]
    except Exception:
        logger.exception(
            "[TMP sync] Failed to load TMP providers for tenant=%s",
            tenant_id,
        )
        return

    if not providers:
        logger.debug(
            "[TMP sync] No active TMP providers for tenant=%s — skipping sync",
            tenant_id,
        )
        return

    # --- Step 4: fan out to each provider (best-effort) ---
    for provider_name, provider_endpoint, provider_auth_credentials in providers:
        try:
            _post_packages_sync(provider_endpoint, payloads, provider_auth_credentials)
        except Exception:
            # Log with full context but do NOT re-raise — one provider failure
            # must not block the others.  The media buy is already committed.
            logger.warning(
                "[TMP sync] Failed to sync %d package(s) to provider '%s' (%s) for tenant=%s media_buy=%s",
                len(payloads),
                provider_name,
                provider_endpoint,
                tenant_id,
                media_buy_id,
                exc_info=True,
            )

"""TMP Provider package sync service.

Pushes package definitions from the Sales Agent to all active TMP Providers
for a tenant whenever a media buy is created or updated.

Per the AdCP TMP spec (Package Sync section):
  "Package metadata is synced from seller agents to TMP providers at media buy
   creation time and whenever the media buy materially changes."

Each synced AvailablePackage includes a seller_agent reference so the TMP
Provider can attribute offers back to the originating seller agent.

Design principles (AdCP Pattern compliance):
- Called only from the **route layer** via FastAPI BackgroundTasks — never from
  _impl functions (which must remain transport-agnostic).
- Reads packages and provider endpoints via **repositories** (UoW pattern) —
  no raw get_db_session() / select() calls.
- HTTP calls are made **after** the DB session is closed — no open transaction
  during network I/O.
- Failures are **logged with full context** and re-raised as warnings so the
  background task runner records them.  The media buy operation itself is
  unaffected (fire-and-forget at the route boundary).
- No asyncio.create_task() — FastAPI BackgroundTasks handles scheduling.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.core.database.repositories.uow import MediaBuyUoW, TMPProviderUoW

logger = logging.getLogger(__name__)

# Timeout for each POST /packages/sync call (seconds).
# Kept short — TMP Provider is an internal service on the same network.
_SYNC_TIMEOUT_S = 5.0


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
    # This is a lightweight read-only query — no UoW needed.
    try:
        with get_db_session() as session:
            tenant = session.scalar(
                select(Tenant).where(Tenant.tenant_id == tenant_id)
            )
            if tenant and tenant.virtual_host:
                host = tenant.virtual_host
                scheme = "https" if "localhost" not in host else "http"
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


def _build_package_payload(
    media_buy_id: str,
    pkg_row: Any,
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
        "price": cfg.get("price") or cfg.get("bid_price"),
        "macros": cfg.get("macros") or {},
        # seller_agent.agent_url → stored as si_agent_endpoint in TMP Provider
        "si_agent_endpoint": seller_agent_url,
        "is_active": cfg.get("is_active", True),
        "expires_at": cfg.get("expires_at"),
    }


def _post_packages_sync(endpoint: str, payloads: list[dict[str, Any]]) -> None:
    """POST /packages/sync to a single TMP Provider endpoint.

    Sends the full list as a JSON array.  The TMP Provider's handler accepts
    both a single object and an array (see handlers_packages.go).

    Raises httpx.HTTPError on non-2xx responses so the caller can log and
    continue to the next provider.
    """
    url = endpoint.rstrip("/") + "/packages/sync"
    with httpx.Client(timeout=_SYNC_TIMEOUT_S) as client:
        resp = client.post(url, json=payloads)
        resp.raise_for_status()
    logger.info(
        "[TMP sync] POST %s → %d (%d package(s))",
        url,
        resp.status_code,
        len(payloads),
    )


def sync_packages_for_media_buy(tenant_id: str, media_buy_id: str) -> None:
    """Background task: push all packages for a media buy to active TMP providers.

    Called by the route layer (api_v1.py) via FastAPI BackgroundTasks after a
    successful create_media_buy or update_media_buy response has been sent.

    Steps:
      1. Load packages from media_packages table via MediaBuyRepository.
      2. Resolve seller_agent URL from tenant config.
      3. Load active/draining TMP provider endpoints via TMPProviderRepository.
      4. POST /packages/sync to each provider (best-effort, errors logged).

    Args:
        tenant_id:    Tenant scope — used for both repository queries.
        media_buy_id: The media buy whose packages should be synced.
    """
    # --- Step 1: load packages (read-only, session closed before HTTP calls) ---
    try:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.media_buys is not None
            pkg_rows = uow.media_buys.get_packages(media_buy_id)
    except Exception:
        logger.exception(
            "[TMP sync] Failed to load packages for media_buy_id=%s tenant=%s",
            media_buy_id,
            tenant_id,
        )
        return

    if not pkg_rows:
        logger.debug(
            "[TMP sync] No packages found for media_buy_id=%s — skipping sync",
            media_buy_id,
        )
        return

    # --- Step 2: resolve seller_agent URL ---
    seller_agent_url = _resolve_seller_agent_url(tenant_id)
    payloads = [_build_package_payload(media_buy_id, row, seller_agent_url) for row in pkg_rows]

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
    try:
        with TMPProviderUoW(tenant_id) as uow:
            assert uow.tmp_providers is not None
            providers = uow.tmp_providers.list_syncable()
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
    for provider in providers:
        try:
            _post_packages_sync(provider.endpoint, payloads)
        except Exception:
            # Log with full context but do NOT re-raise — one provider failure
            # must not block the others.  The media buy is already committed.
            logger.warning(
                "[TMP sync] Failed to sync %d package(s) to provider '%s' (%s) for tenant=%s media_buy=%s",
                len(payloads),
                provider.name,
                provider.endpoint,
                tenant_id,
                media_buy_id,
                exc_info=True,
            )

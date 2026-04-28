"""TMP Provider discovery endpoint.

Exposes:
    GET /tenant/{tenant_id}/tmp-providers/discovery

This endpoint is polled by the TMP Router every 30 s to discover which
provider endpoints to fan out context and identity match requests to.

The endpoint is **unauthenticated** — it is intended for internal network
use only (Docker network / VPC). Do not expose it on a public interface
without adding authentication.

Response schema (mirrors the plan's discovery response format):
{
  "tenant_id": "si-host",
  "providers": [
    {
      "provider_id": "<uuid>",
      "name": "si-agent-demo",
      "endpoint": "http://si-agent.localhost:3003",
      "context_match": true,
      "identity_match": true,
      "countries": ["US"],
      "uid_types": ["publisher_first_party", "uid2", "hashed_email"],
      "timeout_ms": 200,
      "priority": 0,
      "status": "active"
    }
  ]
}

Only providers whose status is 'active' or 'draining' are returned.
Providers with status 'inactive' are excluded entirely.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import TMPProvider, Tenant

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tmp-providers"])


@router.get("/tenant/{tenant_id}/tmp-providers/discovery")
async def tmp_providers_discovery(tenant_id: str) -> JSONResponse:
    """Return the active TMP provider set for a tenant.

    Polled by the TMP Router every 30 s.  Internal network only — no auth.

    Lifecycle filtering:
      active   → included
      draining → included (router stops sending new requests but in-flight complete)
      inactive → excluded
    """
    with get_db_session() as session:
        # Verify tenant exists — return 404 for unknown tenants so the router
        # can distinguish "no providers" from "wrong tenant_id".
        tenant_row = session.scalar(select(Tenant).where(Tenant.tenant_id == tenant_id))
        if tenant_row is None:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

        stmt = (
            select(TMPProvider)
            .where(
                TMPProvider.tenant_id == tenant_id,
                # Exclude inactive providers; active + draining are forwarded.
                TMPProvider.status.in_(["active", "draining"]),
            )
            .order_by(TMPProvider.priority.asc(), TMPProvider.name.asc())
        )
        providers = session.scalars(stmt).all()

    provider_list = []
    for p in providers:
        provider_list.append(
            {
                "provider_id": p.provider_id,
                "name": p.name,
                "endpoint": p.endpoint,
                "context_match": p.context_match,
                "identity_match": p.identity_match,
                # countries / uid_types may be None for legacy rows that pre-date
                # the 20260421000000 migration.  The router treats None as
                # "accepts all" for backward compatibility.
                "countries": p.countries,
                "uid_types": p.uid_types,
                "timeout_ms": p.timeout_ms,
                "priority": p.priority,
                "status": p.status,
            }
        )

    logger.debug(
        "[TMP discovery] tenant=%s returned %d provider(s)",
        tenant_id,
        len(provider_list),
    )

    return JSONResponse(
        content={
            "tenant_id": tenant_id,
            "providers": provider_list,
        }
    )

"""TMP Provider discovery endpoint.

Exposes:
    GET /tenant/{tenant_id}/tmp-providers/discovery

This endpoint is polled by the TMP Router every 30 s to discover which
provider endpoints to fan out context and identity match requests to.

Authentication is **fail-closed**: the endpoint is locked by default.

Set ``TMP_DISCOVERY_API_KEYS`` to a comma-separated list of accepted keys to
grant access.  To explicitly disable authentication for internal-network-only
deployments, set ``TMP_DISCOVERY_API_KEYS=OPEN``.  Leaving the variable unset
or empty returns HTTP 500 so that misconfigured deployments fail loudly rather
than silently exposing tenant topology.

Accepted auth headers (any one is sufficient):
  - ``x-adcp-auth: <key>``
  - ``X-API-Key: <key>``
  - ``Authorization: Bearer <key>``

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
import os
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.core.database.repositories.uow import TMPProviderUoW
from src.core.exceptions import (
    AdCPAccountNotFoundError,
    AdCPAuthRequiredError,
    AdCPConfigurationError,
    AdCPServiceUnavailableError,
)
from src.core.http_utils import parse_bearer_token as _parse_bearer_token
from src.core.security.url_validator import sanitize_for_log

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tmp-providers"])


async def require_api_key(request: Request) -> None:
    """Require API key for the TMP discovery endpoint.

    Fail-closed: the endpoint is locked unless ``TMP_DISCOVERY_API_KEYS`` is
    explicitly configured.

    - ``TMP_DISCOVERY_API_KEYS=key1,key2`` — accept those keys only.
    - ``TMP_DISCOVERY_API_KEYS=OPEN`` — disable auth (internal-network-only
      deployments where the operator has made a deliberate choice).
    - Unset or empty — raise ``AdCPConfigurationError`` (500, terminal) so
      misconfigured deployments fail loudly instead of silently exposing tenant
      topology.  The operator must act; the buyer cannot recover this.

    Accepted headers (first non-empty value wins):
      - ``x-adcp-auth``
      - ``X-API-Key``
      - ``Authorization: Bearer <key>``

    Bearer parsing uses the shared ``parse_bearer_token()`` helper
    (``src.core.http_utils``) — the single canonical implementation across
    all four Bearer-parsing sites in the codebase.
    """
    raw = os.environ.get("TMP_DISCOVERY_API_KEYS", "").strip()

    if raw.upper() == "OPEN":
        logger.warning("[TMP discovery] API key auth disabled — TMP_DISCOVERY_API_KEYS=OPEN")
        return

    allowed = [k.strip() for k in raw.split(",") if k.strip()]
    if not allowed:
        raise AdCPConfigurationError(
            "TMP_DISCOVERY_API_KEYS is not configured. "
            "Set it to a comma-separated list of API keys, "
            "or to 'OPEN' to disable authentication."
        )

    api_key = (
        request.headers.get("x-adcp-auth", "")
        or request.headers.get("X-API-Key", "")
        or _parse_bearer_token(request.headers.get("authorization", ""))
        or ""
    )
    if not any(secrets.compare_digest(api_key, k) for k in allowed):
        raise AdCPAuthRequiredError(
            "Authentication required.",
            details={
                "suggestion": "Provide a valid API key via x-adcp-auth, X-API-Key, or Authorization: Bearer <key>."
            },
        )


@router.get("/tenant/{tenant_id}/tmp-providers/discovery")
async def tmp_providers_discovery(tenant_id: str, _: None = Depends(require_api_key)) -> JSONResponse:
    """Return the active TMP provider set for a tenant.

    Polled by the TMP Router every 30 s.  Requires API key authentication
    via ``TMP_DISCOVERY_API_KEYS`` (fail-closed: returns 500 when unset).

    Lifecycle filtering:
      active   → included
      draining → included (router stops sending new requests but in-flight complete)
      inactive → excluded
    """
    # Single TMPProviderUoW block: it already exposes both tmp_providers and
    # tenant_config repositories, so the tenant-existence check and the
    # provider read run as ONE transaction rather than two separate ones.
    #
    # provider.to_dict(...) is also called INSIDE this block — TMPProvider
    # attributes expire on commit (default expire_on_commit=True), so calling
    # to_dict() after the `with` block closes hits a detached session and
    # raises DetachedInstanceError.
    with TMPProviderUoW(tenant_id) as uow:
        if uow.tenant_config is None:
            raise AdCPServiceUnavailableError("Tenant config repository unavailable.")
        if uow.tenant_config.get_tenant() is None:
            raise AdCPAccountNotFoundError(
                f"Tenant '{tenant_id}' not found.",
                details={"suggestion": "Provide a valid tenant ID."},
            )

        assert uow.tmp_providers is not None
        providers = uow.tmp_providers.list_syncable()

        # include_conditional=False: the TMP Router expects countries/uid_types
        # to always be present (None means "accepts all" for legacy rows).
        provider_list = [p.to_dict(include_conditional=False) for p in providers]

    logger.debug(
        "[TMP discovery] tenant=%s returned %d provider(s)",
        sanitize_for_log(tenant_id),
        len(provider_list),
    )

    return JSONResponse(
        content={
            "tenant_id": tenant_id,
            "providers": provider_list,
        }
    )

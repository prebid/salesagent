"""Request-scoped ``get_adcp_capabilities`` helpers.

AdCP SDK beta 4 exposes
``DecisioningPlatform.get_adcp_capabilities_for_request()`` for typed,
request-scoped capability enrichment. Salesagent uses that hook for
tenant-specific ``media_buy.portfolio.publisher_domains`` so the SDK
continues to own canonical response projection and validation.

1. **``portfolio.publisher_domains``** (AdCP 3.x). v3 retired
   ``list_authorized_properties`` and moved the publisher portfolio
   onto ``get_adcp_capabilities``. Populate it per-tenant from the
   ``PublisherPartner`` table so authenticated and unauthenticated
   buyers both see the agent's inventory partners on discovery.
   Sorted alphabetically per CONSTR-PUBLISHER-DOMAINS-PORTFOLIO-01.
   Omitted when the tenant has zero partners (the schema's
   ``min_length=1`` on ``Portfolio.publisher_domains`` requires it).

2. **``webhook_signing``** (AdCP 3.x). The SDK exposes a native
   capability block for RFC 9421 webhook signing, but the data is
   tenant-specific: only tenants with an active, locally usable
   ``TenantSigningCredential`` can safely advertise it.

Importing this module still installs a narrow response patch for
``webhook_signing``. That patch remains local because Salesagent emits
buyer-protocol webhooks through its own service path, while the SDK's
typed request hook validates ``webhook_signing.supported=True`` against
an SDK-wired ``WebhookSender``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from adcp.decisioning import DecisioningCapabilities
from adcp.decisioning.capabilities import Portfolio
from adcp.decisioning.handler import PlatformHandler
from adcp.server.tenant_router import current_tenant

logger = logging.getLogger(__name__)

_ORIGINAL = PlatformHandler.get_adcp_capabilities

_WEBHOOK_SIGNING_PROFILE = "adcp/webhook-signing/v1"


def _webhook_signing_unsupported() -> dict[str, Any]:
    return {"supported": False, "legacy_hmac_fallback": True}


def _tenant_id_from_context(context: Any = None) -> str | None:
    if context is not None:
        tenant_id = getattr(context, "tenant_id", None)
        if tenant_id:
            return str(tenant_id)

    tenant = current_tenant()
    tenant_id = getattr(tenant, "id", None) if tenant is not None else None
    return str(tenant_id) if tenant_id else None


def _publisher_domains_for_tenant_id(tenant_id: str | None) -> list[str]:
    """Return sorted publisher domains for a tenant id.

    Returns an empty list when no tenant is resolved or the tenant has no
    ``PublisherPartner`` rows. Failures inside the DB read are swallowed with a
    warning — discovery should never 500 on an inventory-table hiccup.
    """
    if not tenant_id:
        return []
    # Import lazily so this module is import-safe at module-load time
    # (the patch is applied via side-effect import from core.main).
    from src.core.database.repositories.uow import TenantConfigUoW

    try:
        with TenantConfigUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            return uow.tenant_config.list_publisher_domains()
    except Exception:
        logger.warning(
            "publisher_domains lookup failed for tenant %r; emitting empty portfolio",
            tenant_id,
            exc_info=True,
        )
        return []


def _publisher_domains_for_current_tenant() -> list[str]:
    """Backward-compatible helper for tests and contextvar-only callers."""
    return _publisher_domains_for_tenant_id(_tenant_id_from_context())


def capabilities_for_request(
    base_capabilities: DecisioningCapabilities,
    params: Any = None,
    context: Any = None,
) -> DecisioningCapabilities | None:
    """Return tenant-scoped capabilities for SDK projection.

    ``params`` is accepted to match the SDK hook shape. Salesagent's current
    enrichment depends only on the resolved tenant.
    """
    del params
    domains = _publisher_domains_for_tenant_id(_tenant_id_from_context(context))
    if not domains or base_capabilities.media_buy is None:
        return None

    existing_portfolio = base_capabilities.media_buy.portfolio
    portfolio = (
        existing_portfolio.model_copy(update={"publisher_domains": domains})
        if existing_portfolio is not None
        else Portfolio(publisher_domains=domains)
    )
    media_buy = base_capabilities.media_buy.model_copy(update={"portfolio": portfolio})
    return replace(base_capabilities, media_buy=media_buy)


def _webhook_signing_for_tenant_id(tenant_id: str | None) -> dict[str, Any]:
    """Return the tenant-specific AdCP webhook-signing capability block."""
    if not tenant_id:
        return _webhook_signing_unsupported()

    from src.services.webhook_signing import (
        SIGNING_MODE_RFC9421,
        SigningConfigurationError,
        load_active_signing_credential,
    )

    try:
        snapshot = load_active_signing_credential(tenant_id=tenant_id, signing_mode=SIGNING_MODE_RFC9421)
        if snapshot is None:
            return _webhook_signing_unsupported()
    except SigningConfigurationError:
        logger.warning(
            "webhook signing credential for tenant %r is active but not usable; advertising unsupported",
            tenant_id,
            exc_info=True,
        )
        return _webhook_signing_unsupported()
    except Exception:
        logger.warning(
            "webhook signing capability lookup failed for tenant %r; advertising unsupported",
            tenant_id,
            exc_info=True,
        )
        return _webhook_signing_unsupported()

    return {
        "supported": True,
        "profile": _WEBHOOK_SIGNING_PROFILE,
        "algorithms": [snapshot.alg],
        "legacy_hmac_fallback": True,
    }


def _webhook_signing_for_current_tenant() -> dict[str, Any]:
    """Backward-compatible helper for tests and contextvar-only callers."""
    return _webhook_signing_for_tenant_id(_tenant_id_from_context())


async def _get_adcp_capabilities_patched(
    self: PlatformHandler,
    params: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    result = await _ORIGINAL(self, params, context)
    if not isinstance(result, dict):
        return result

    if "webhook_signing" not in result:
        result["webhook_signing"] = _webhook_signing_for_tenant_id(_tenant_id_from_context(context))

    return result


PlatformHandler.get_adcp_capabilities = _get_adcp_capabilities_patched  # type: ignore[method-assign]

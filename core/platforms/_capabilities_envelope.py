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

Salesagent emits buyer-protocol webhooks through its own service path,
so ``DecisioningCapabilities.webhook_signing_managed_externally`` tells
the SDK to trust this typed capability declaration instead of requiring
an SDK-wired ``WebhookSender``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from adcp.decisioning import DecisioningCapabilities
from adcp.decisioning.capabilities import Portfolio, WebhookSigning
from adcp.server.tenant_router import current_tenant

from src.core.embedded_runtime import publisher_owns_compose_products

logger = logging.getLogger(__name__)

_WEBHOOK_SIGNING_PROFILE = "adcp/webhook-signing/v1"


def _webhook_signing_unsupported() -> WebhookSigning:
    return WebhookSigning(supported=False, legacy_hmac_fallback=True)


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
    tenant_id = _tenant_id_from_context(context)
    domains = _publisher_domains_for_tenant_id(tenant_id)
    webhook_signing = _webhook_signing_for_tenant_id(tenant_id)
    supports_proposals = publisher_owns_compose_products()
    updates: dict[str, Any] = {}

    if base_capabilities.media_buy is not None:
        existing_portfolio = base_capabilities.media_buy.portfolio
        media_buy_updates: dict[str, Any] = {}

        if domains:
            portfolio = (
                existing_portfolio.model_copy(update={"publisher_domains": domains})
                if existing_portfolio is not None
                else Portfolio(publisher_domains=domains)
            )
            media_buy_updates["portfolio"] = portfolio

        if base_capabilities.media_buy.supports_proposals != supports_proposals:
            media_buy_updates["supports_proposals"] = supports_proposals

        if media_buy_updates:
            updates["media_buy"] = base_capabilities.media_buy.model_copy(update=media_buy_updates)

    base_webhook_signing = base_capabilities.webhook_signing
    if base_webhook_signing is None or webhook_signing.model_dump(mode="json", exclude_none=True) != (
        base_webhook_signing.model_dump(mode="json", exclude_none=True)
    ):
        updates["webhook_signing"] = webhook_signing

    if not updates:
        return None

    return replace(base_capabilities, **updates)


def _webhook_signing_for_tenant_id(tenant_id: str | None) -> WebhookSigning:
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

    return WebhookSigning(
        supported=True,
        profile=_WEBHOOK_SIGNING_PROFILE,
        algorithms=[snapshot.alg],
        legacy_hmac_fallback=True,
    )


def _webhook_signing_for_current_tenant() -> WebhookSigning:
    """Backward-compatible helper for tests and contextvar-only callers."""
    return _webhook_signing_for_tenant_id(_tenant_id_from_context())

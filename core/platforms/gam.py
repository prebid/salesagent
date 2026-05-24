"""GAM-backed seller platform — thin AdCP-shape adapter.

Every method delegates to ``src/core/tools/*:_*_impl``, which routes
through ``get_adapter()`` based on ``tenant.ad_server`` to the existing
``src/adapters/gam`` machinery (``GAMOrdersManager.create_order``,
``GAMOrdersManager.create_line_items``, custom-targeting key resolution,
LineItemAction pause/resume/archive, ReportService → delivery). The
greenfield layer adds nothing GAM-specific itself; the value-add is
the framework primitives (DecisioningPlatform, AccountStore,
idempotency, transport=both, LazyPlatformRouter) wrapped around the
existing brain.

Architectural rationale (#28 v2): the v1 stake reimplemented order
creation by hand-calling ``GAMOrdersManager.create_order`` directly,
which (a) skipped 762 LOC of brief/policy/validation logic in
``_create_media_buy_impl`` and (b) didn't extend to line items,
update, sync_creatives, or delivery. The right shape — same lesson
as #37 — is to delegate to the existing _impl chain and let the
adapter pattern handle ad-server routing.

Tenant binding: ``LazyPlatformRouter`` builds one of these per tenant
on first request; ``ctx.account.metadata['tenant_id']`` is the routing
key. ``_get_adapter`` inside the impl reads ``tenant.ad_server`` to
pick the GAM adapter for tenants configured ``ad_server="google_ad_manager"``.
"""

from __future__ import annotations

import logging
from typing import Any

from adcp.decisioning import (
    DecisioningCapabilities,
    DecisioningPlatform,
    RequestContext,
)
from adcp.decisioning.capabilities import (
    Account as CapabilitiesAccount,
)
from adcp.decisioning.capabilities import (
    Adcp,
    IdempotencySupported,
    MediaBuy,
    Signals,
    SignalsFeatures,
    SupportedProtocol,
)

from core.idempotency import get_idempotency_store, translate_idempotency_conflict
from core.platforms._delegate import (
    _delegate_create_media_buy,
    _delegate_get_media_buy_delivery,
    _delegate_get_media_buys,
    _delegate_get_products,
    _delegate_get_signals,
    _delegate_list_creative_formats,
    _delegate_list_creatives,
    _delegate_provide_performance_feedback,
    _delegate_sync_creatives,
    _delegate_update_media_buy,
)
from core.stores.accounts import SalesagentAccountStore

logger = logging.getLogger(__name__)

# Process-singleton idempotency store wired through ``core.idempotency``
# (PgBackend by default; CORE_IDEMPOTENCY_BACKEND=memory in tests). The
# framework's boot-time ``validate_idempotency_wiring`` is satisfied
# because the platform advertises ``IdempotencySupported(supported=True)``
# and we wrap ``create_media_buy`` below. ``update_media_buy`` is
# arg-projected and incompatible with @wrap (SDK #559 fix landed in
# #567); ``sync_creatives`` and others are idempotent at the impl layer
# via per-creative upserts and per-buy state checks.
_IDEMPOTENCY = get_idempotency_store()


class GamPlatform(DecisioningPlatform):
    """Thin platform shell — every method forwards to the existing
    salesagent _impl chain, which routes to ``src/adapters/gam`` via
    ``get_adapter()`` based on the tenant's ``ad_server`` config."""

    capabilities = DecisioningCapabilities(
        specialisms=["sales-non-guaranteed", "signal-owned"],
        adcp=Adcp(
            major_versions=[3],
            idempotency=IdempotencySupported(supported=True, replay_ttl_seconds=86400),
        ),
        account=CapabilitiesAccount(supported_billing=["operator"]),
        media_buy=MediaBuy(supported_pricing_models=["cpm"]),
        signals=Signals(discovery_modes=["brief", "wholesale"], features=SignalsFeatures(catalog_signals=True)),
        supported_protocols=[SupportedProtocol.media_buy, SupportedProtocol.signals],
    )
    accounts = SalesagentAccountStore()

    async def get_products(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_products(req, ctx)

    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def create_media_buy(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_create_media_buy(req, ctx)

    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def update_media_buy(
        self,
        media_buy_id: str,
        patch: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_update_media_buy(media_buy_id, patch, ctx)

    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def sync_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_sync_creatives(req, ctx)

    async def get_media_buys(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_media_buys(req, ctx)

    async def get_media_buy_delivery(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_media_buy_delivery(req, ctx)

    async def list_creative_formats(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creative_formats(req, ctx)

    async def list_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creatives(req, ctx)

    async def provide_performance_feedback(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_provide_performance_feedback(req, ctx)

    async def get_signals(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_signals(req, ctx)

    # sync_accounts / list_accounts dispatch lives on the shared
    # SalesagentAccountStore (accounts attribute), not as platform
    # methods — adcp >= 4.6.1's PlatformHandler dispatchers route the
    # wire skills through the AccountStoreUpsert / AccountStoreList
    # Protocols on that store.

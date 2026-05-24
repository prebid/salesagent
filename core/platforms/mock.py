"""Mock seller platform — DB-backed delegation.

Implements the AdCP ``DecisioningPlatform`` interface for the
``sales-non-guaranteed`` storyboard surface. Every method delegates to
the canonical ``src/core/tools/*`` ``_impl`` functions (the same brain
``GamPlatform`` runs through), which persist to the salesagent
``MediaBuy``/``MediaPackage`` ORM. The DB is the single source of
truth — there is no separate in-memory store.

Why delegation: the e2e webhook scheduler, ``get_media_buy_delivery``,
and ``force_approve_media_buy_in_db`` all read from the DB. An
in-memory store on the create/update side desynchronised them
(salesagent #88 / #107 fixed the same defect class on
``sync_creatives``; this finishes the job for the media-buy methods).
"""

from __future__ import annotations

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
from src.core.schemas import GetProductsRequest

# Process-singleton idempotency store wired through ``core.idempotency``.
# Defaults to :class:`PgBackend` for cross-worker durable replay; tests
# set ``CORE_IDEMPOTENCY_BACKEND=memory`` for single-process isolation.
_IDEMPOTENCY = get_idempotency_store()


class MockSellerPlatform(DecisioningPlatform):
    """Reads products from the salesagent ``products`` table and runs
    a full ``sales-non-guaranteed`` lifecycle against the salesagent
    ``MediaBuy`` ORM via ``src/core/tools/*`` ``_impl`` delegates.
    Idempotent on every mutating method."""

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

    # ─────────────────────────── get_products ────────────────────────

    async def get_products(
        self,
        req: GetProductsRequest,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_products(req, ctx)

    # ─────────────────────────── create_media_buy ────────────────────

    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def create_media_buy(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_create_media_buy(req, ctx)

    # ─────────────────────────── update_media_buy ────────────────────
    # @_IDEMPOTENCY.wrap re-enabled after adcp-client-python#567
    # taught the wrap to support arg-projected methods (the framework
    # dispatches update_media_buy as
    # ``method(self, media_buy_id=..., patch=..., ctx=...)`` rather than
    # the (self, params, context) shape the wrap originally required).
    # Salesagent task #35 closed.
    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def update_media_buy(
        self,
        media_buy_id: str,
        patch: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_update_media_buy(media_buy_id, patch, ctx)

    # ─────────────────────────── sync_creatives ──────────────────────

    @translate_idempotency_conflict
    @_IDEMPOTENCY.wrap
    async def sync_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_sync_creatives(req, ctx)

    # ─────────────────────────── get_media_buys ──────────────────────

    async def get_media_buys(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_media_buys(req, ctx)

    # ─────────────────────────── get_media_buy_delivery ──────────────

    async def get_media_buy_delivery(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_media_buy_delivery(req, ctx)

    # ─────────────────────────── list_creative_formats ───────────────

    async def list_creative_formats(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creative_formats(req, ctx)

    # ─────────────────────────── list_creatives ──────────────────────

    async def list_creatives(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_list_creatives(req, ctx)

    # ─────────────────────────── provide_performance_feedback ────────

    async def provide_performance_feedback(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_provide_performance_feedback(req, ctx)

    # ─────────────────────────── get_signals ────────────────────────

    async def get_signals(
        self,
        req: Any,
        ctx: RequestContext[Any],
    ) -> dict[str, Any]:
        return await _delegate_get_signals(req, ctx)

    # ─────────────────────────── sync_accounts / list_accounts ──────
    # Account dispatch lives on ``accounts`` (the AccountStore), not on
    # the platform — the framework's LazyPlatformRouter explicitly
    # excludes account methods from per-tenant delegation. The
    # ``upsert`` / ``list`` methods on SalesagentAccountStore handle the
    # wire flow; adcp >= 4.6.1's PlatformHandler dispatchers wire those
    # store methods to the wire skill calls.

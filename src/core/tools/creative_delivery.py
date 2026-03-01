"""Get Creative Delivery tool implementation.

Provides creative-level delivery metrics for media buys, including
per-creative impressions, clicks, spend, and CTR. Extends the
existing get_media_buy_delivery with creative-dimension granularity.

GH #1030 — AdCP v3 get_creative_delivery tool.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from adcp.types import Error
from adcp.types.generated_poc.core.context import ContextObject

from src.core.auth import get_principal_object
from src.core.database.repositories import MediaBuyUoW
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CreativeDeliveryData,
    DeliveryMetrics,
    GetCreativeDeliveryRequest,
    GetCreativeDeliveryResponse,
)
from src.core.schemas import (
    ReportingPeriod as DeliveryReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.validation_helpers import format_validation_error


def _get_creative_delivery_impl(
    req: GetCreativeDeliveryRequest, identity: ResolvedIdentity | None
) -> GetCreativeDeliveryResponse:
    """Get creative-level delivery data for media buys.

    Returns per-creative metrics (impressions, clicks, spend, CTR) for
    creatives assigned to the specified media buys.
    """
    if identity is None:
        raise AdCPValidationError("Context is required")

    testing_ctx = identity.testing_context or AdCPTestContext()

    principal_id = identity.principal_id if identity else None
    if not principal_id:
        return GetCreativeDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            creatives=[],
            errors=[Error(code="principal_id_missing", message="Principal ID not found in context")],
        )

    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        return GetCreativeDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            creatives=[],
            errors=[Error(code="principal_not_found", message=f"Principal {principal_id} not found")],
        )

    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Validate that at least one scoping filter is provided
    if not req.media_buy_ids and not req.media_buy_buyer_refs and not req.creative_ids:
        raise AdCPValidationError(
            "At least one scoping filter is required: media_buy_ids, media_buy_buyer_refs, or creative_ids"
        )

    adapter = get_adapter(
        principal, dry_run=testing_ctx.dry_run if testing_ctx else False, testing_context=testing_ctx, tenant=tenant
    )

    # Determine reporting period
    if req.start_date and req.end_date:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
        if start_dt >= end_dt:
            return GetCreativeDeliveryResponse(
                reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
                currency="USD",
                creatives=[],
                errors=[Error(code="invalid_date_range", message="Start date must be before end date")],
            )
    else:
        end_dt = datetime.now(UTC)
        start_dt = end_dt - timedelta(days=30)

    reporting_period = DeliveryReportingPeriod(start=start_dt, end=end_dt)

    # Resolve media_buy_ids from buyer_refs if needed
    resolved_media_buy_ids: list[str] = []
    not_found_errors: list[Error] = []

    with MediaBuyUoW(tenant["tenant_id"]) as uow:
        assert uow.media_buys is not None
        repo = uow.media_buys

        if req.media_buy_ids:
            # Verify all requested IDs exist for this principal
            buys = repo.get_by_principal(principal_id, media_buy_ids=req.media_buy_ids)
            found_ids = {buy.media_buy_id for buy in buys}
            for requested_id in req.media_buy_ids:
                if requested_id in found_ids:
                    resolved_media_buy_ids.append(requested_id)
                else:
                    not_found_errors.append(
                        Error(code="media_buy_not_found", message=f"Media buy {requested_id} not found")
                    )
        elif req.media_buy_buyer_refs:
            buys = repo.get_by_principal(principal_id, buyer_refs=req.media_buy_buyer_refs)
            found_refs = set()
            for buy in buys:
                resolved_media_buy_ids.append(buy.media_buy_id)
                if buy.buyer_ref:
                    found_refs.add(buy.buyer_ref)
            for requested_ref in req.media_buy_buyer_refs:
                if requested_ref not in found_refs:
                    not_found_errors.append(
                        Error(code="media_buy_not_found", message=f"Buyer ref {requested_ref} not found")
                    )
        elif req.creative_ids:
            # When filtering by creative_ids only, get all media buys for this principal
            buys = repo.get_by_principal(principal_id)
            resolved_media_buy_ids = [buy.media_buy_id for buy in buys]

    # Call adapter for creative-level delivery
    all_creatives: list[CreativeDeliveryData] = []

    for media_buy_id in resolved_media_buy_ids:
        try:
            adapter_response = adapter.get_creative_delivery(
                media_buy_id=media_buy_id,
                date_range=reporting_period,
                creative_ids=req.creative_ids,
            )
            for item in adapter_response.creatives:
                totals = DeliveryMetrics(
                    impressions=item.impressions,
                    clicks=item.clicks,
                    spend=item.spend,
                    ctr=item.ctr,
                )
                all_creatives.append(
                    CreativeDeliveryData(
                        creative_id=item.creative_id,
                        media_buy_id=item.media_buy_id or media_buy_id,
                        totals=totals,
                        variant_count=0,
                        variants=[],
                    )
                )
        except NotImplementedError:
            logger.warning(f"Adapter does not support get_creative_delivery for {media_buy_id}")
        except Exception as e:
            logger.error(f"Error getting creative delivery for {media_buy_id}: {e}")
            not_found_errors.append(
                Error(code="adapter_error", message=f"Error getting creative delivery for {media_buy_id}")
            )

    # Apply creative_ids filter if provided (adapter may return extras)
    if req.creative_ids:
        filter_set = set(req.creative_ids)
        all_creatives = [c for c in all_creatives if c.creative_id in filter_set]

    response = GetCreativeDeliveryResponse(
        reporting_period={"start": reporting_period.start, "end": reporting_period.end},
        currency="USD",
        creatives=all_creatives,
        errors=not_found_errors or None,
    )

    return response


async def get_creative_delivery(
    media_buy_ids: list[str] | None = None,
    media_buy_buyer_refs: list[str] | None = None,
    creative_ids: list[str] | None = None,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_variants: int | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get creative-level delivery metrics.

    Returns per-creative impressions, clicks, spend, and CTR for creatives
    assigned to the specified media buys. At least one scoping filter is required.

    Args:
        media_buy_ids: Filter to specific media buys by publisher ID
        media_buy_buyer_refs: Filter to specific media buys by buyer reference ID
        creative_ids: Filter to specific creatives by ID
        account_id: Account context for routing and scoping
        start_date: Start date for delivery period (YYYY-MM-DD)
        end_date: End date for delivery period (YYYY-MM-DD)
        max_variants: Maximum number of variants to return per creative
        context: Application level context object (ContextObject)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetCreativeDeliveryResponse data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    try:
        req = GetCreativeDeliveryRequest(
            media_buy_ids=media_buy_ids,
            media_buy_buyer_refs=media_buy_buyer_refs,
            creative_ids=creative_ids,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            max_variants=max_variants,
            context=cast(ContextObject | None, context),
        )

        response = _get_creative_delivery_impl(req, identity)

        return ToolResult(content=str(response), structured_content=response)
    except ValidationError as e:
        raise AdCPValidationError(format_validation_error(e, context="get_creative_delivery request"))


def get_creative_delivery_raw(
    media_buy_ids: list[str] | None = None,
    media_buy_buyer_refs: list[str] | None = None,
    creative_ids: list[str] | None = None,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_variants: int | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """Get creative-level delivery metrics (raw function for A2A server use).

    Args:
        media_buy_ids: Filter to specific media buys by publisher ID
        media_buy_buyer_refs: Filter to specific media buys by buyer reference ID
        creative_ids: Filter to specific creatives by ID
        account_id: Account context for routing and scoping
        start_date: Start date for delivery period (YYYY-MM-DD)
        end_date: End date for delivery period (YYYY-MM-DD)
        max_variants: Maximum number of variants to return per creative
        context: Application level context (ContextObject)
        ctx: Context for authentication
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetCreativeDeliveryResponse with creative delivery metrics
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx)

    req = GetCreativeDeliveryRequest(
        media_buy_ids=media_buy_ids,
        media_buy_buyer_refs=media_buy_buyer_refs,
        creative_ids=creative_ids,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        max_variants=max_variants,
        context=cast(ContextObject | None, context),
    )

    return _get_creative_delivery_impl(req, identity)

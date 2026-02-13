"""Get Media Buy Delivery tool implementation.

Handles delivery metrics reporting including:
- Campaign delivery totals (impressions, spend)
- Package-level delivery breakdown
- Status filtering (active, paused, completed)
- Date range reporting
- Testing mode simulation
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from math import floor
from typing import Any, cast

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console
from sqlalchemy import select

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)
console = Console()

from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.core.context import ContextObject

from src.core.auth import get_principal_object
from src.core.config_loader import get_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, MediaPackage, PricingOption
from src.core.helpers import get_principal_id_from_context
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schemas import (
    AggregatedTotals,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    MediaBuyDeliveryData,
    PackageDelivery,
    PricingModel,
    ReportingPeriod,
)
from src.core.testing_hooks import DeliverySimulator, TimeSimulator, apply_testing_hooks, get_testing_context
from src.core.validation_helpers import format_validation_error


def _context_to_dict(context: ContextObject | None) -> dict[str, Any] | None:
    """Convert ContextObject to dict for response, handling None case."""
    if context is None:
        return None
    if hasattr(context, "model_dump"):
        return context.model_dump()
    # Fallback for dict-like objects
    return dict(context) if context else None


def _get_media_buy_delivery_impl(
    req: GetMediaBuyDeliveryRequest, ctx: Context | ToolContext | None
) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for one or more media buys.

    AdCP-compliant implementation that handles start_date/end_date parameters
    and returns spec-compliant response format.
    """

    # Validate context is provided
    if ctx is None:
        raise ToolError("Context is required")

    # Extract testing context for time simulation and event jumping
    testing_ctx = get_testing_context(ctx)

    principal_id = get_principal_id_from_context(ctx)
    if not principal_id:
        # Return AdCP-compliant error response
        # TODO: @yusuf - Should this return only error field and not the other fields? Haven't we updated adcp spec to only return error field on errors??
        # Convert ContextObject to dict if needed
        context_dict = _context_to_dict(req.context)
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
            errors=[{"code": "principal_id_missing", "message": "Principal ID not found in context"}],
            context=context_dict,
        )

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        # Return AdCP-compliant error response
        # TODO: @yusuf - Should this return only error field and not the other fields? Haven't we updated adcp spec to only return error field on errors??
        context_dict = _context_to_dict(req.context)
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
            context=context_dict,
        )

    # Get the appropriate adapter
    # Use testing_ctx.dry_run if in testing mode, otherwise False
    adapter = get_adapter(principal, dry_run=testing_ctx.dry_run if testing_ctx else False, testing_context=testing_ctx)

    # Determine reporting period
    if req.start_date and req.end_date:
        # Use provided date range
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d")

        if start_dt >= end_dt:
            context_dict = _context_to_dict(req.context)
            return GetMediaBuyDeliveryResponse(
                reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
                currency="USD",
                aggregated_totals=AggregatedTotals(
                    impressions=0.0,
                    spend=0.0,
                    clicks=None,
                    video_completions=None,
                    media_buy_count=0,
                ),
                media_buy_deliveries=[],
                errors=[{"code": "invalid_date_range", "message": "Start date must be before end date"}],
                context=context_dict,
            )
    else:
        # Default to last 30 days
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=30)

    reporting_period = ReportingPeriod(start=start_dt.isoformat(), end=end_dt.isoformat())

    # Determine reference date for status calculations use end_date, it either will be today or the user provided end_date.
    reference_date = end_dt.date()

    # Determine which media buys to fetch from database
    tenant = get_current_tenant()

    target_media_buys = _get_target_media_buys(req, principal_id, tenant, reference_date)
    pricing_option_ids = [
        buy.raw_request.get("pricing_option_id")
        for _, buy in target_media_buys
        if buy.raw_request
        and isinstance(buy.raw_request, dict)
        and buy.raw_request.get("pricing_option_id") is not None
    ]
    pricing_options = _get_pricing_options(pricing_option_ids)

    # Collect delivery data for each media buy
    deliveries = []
    total_spend = 0.0
    total_impressions = 0
    media_buy_count = 0
    total_clicks = 0

    for media_buy_id, buy in target_media_buys:
        try:
            # Apply time simulation from testing context
            simulation_datetime = end_dt
            if testing_ctx.mock_time:
                simulation_datetime = testing_ctx.mock_time
            elif testing_ctx.jump_to_event:
                # Calculate time based on event
                # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                from typing import cast as type_cast

                buy_start_date = type_cast(date, buy.start_date)
                buy_end_date = type_cast(date, buy.end_date)
                simulation_datetime = TimeSimulator.jump_to_event_time(
                    testing_ctx.jump_to_event,
                    datetime.combine(buy_start_date, datetime.min.time()),
                    datetime.combine(buy_end_date, datetime.min.time()),
                )

            # Determine status
            # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
            from typing import cast as type_cast

            buy_start_date_status = type_cast(date, buy.start_date)
            buy_end_date_status = type_cast(date, buy.end_date)
            if simulation_datetime.date() < buy_start_date_status:
                status = "ready"
            elif simulation_datetime.date() > buy_end_date_status:
                status = "completed"
            else:
                status = "active"

            # Get delivery metrics from adapter
            adapter_package_metrics = {}  # Map package_id -> {impressions, spend, clicks}
            total_spend_from_adapter = 0.0
            total_impressions_from_adapter = 0

            if not any(
                [testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]
            ):
                # Call adapter to get per-package delivery metrics
                # Note: Mock adapter returns simulated data, GAM adapter returns real data from Reporting API
                try:
                    adapter_response = adapter.get_media_buy_delivery(
                        media_buy_id=media_buy_id,
                        date_range=reporting_period,
                        today=simulation_datetime,
                    )

                    # Map adapter's by_package to package_id -> metrics
                    for adapter_pkg in adapter_response.by_package:
                        adapter_package_metrics[adapter_pkg.package_id] = {
                            "impressions": float(adapter_pkg.impressions),
                            "spend": float(adapter_pkg.spend),
                            "clicks": None,  # AdapterPackageDelivery doesn't have clicks yet
                        }
                        total_spend_from_adapter += float(adapter_pkg.spend)
                        total_impressions_from_adapter += int(adapter_pkg.impressions)

                    # Use adapter's totals if available
                    if adapter_response.totals:
                        spend = float(adapter_response.totals.spend)
                        impressions = int(adapter_response.totals.impressions)
                    else:
                        spend = total_spend_from_adapter
                        impressions = total_impressions_from_adapter

                except Exception as e:
                    logger.error(f"Error getting delivery for {media_buy_id}: {e}")
                    context_dict = _context_to_dict(req.context)
                    return GetMediaBuyDeliveryResponse(
                        reporting_period=reporting_period,
                        currency=buy.currency,
                        aggregated_totals=AggregatedTotals(
                            impressions=0.0,
                            spend=0.0,
                            clicks=None,
                            video_completions=None,
                            media_buy_count=0,
                        ),
                        media_buy_deliveries=[],
                        errors=[{"code": "adapter_error", "message": f"Error getting delivery for {media_buy_id}"}],
                        context=context_dict,
                    )
            else:
                # Use simulation for testing
                # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                from typing import cast as type_cast

                buy_start_date_sim = type_cast(date, buy.start_date)
                buy_end_date_sim = type_cast(date, buy.end_date)
                start_dt = datetime.combine(buy_start_date_sim, datetime.min.time())
                end_dt_campaign = datetime.combine(buy_end_date_sim, datetime.min.time())
                progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt_campaign, simulation_datetime)

                simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                    float(buy.budget) if buy.budget else 0.0, progress, testing_ctx
                )

                spend = simulated_metrics["spend"]
                impressions = simulated_metrics["impressions"]

            # Create package delivery data
            package_deliveries = []

            # Get pricing info from MediaPackage.package_config
            package_pricing_map = {}
            with get_db_session() as session:
                media_package_stmt = select(MediaPackage).where(MediaPackage.media_buy_id == media_buy_id)
                media_packages = session.scalars(media_package_stmt).all()
                for media_pkg in media_packages:
                    package_config = media_pkg.package_config or {}
                    pricing_info = package_config.get("pricing_info")
                    if pricing_info:
                        package_pricing_map[media_pkg.package_id] = pricing_info

            # Get packages from raw_request
            if buy.raw_request and isinstance(buy.raw_request, dict):
                # Try to get packages from raw_request.packages (AdCP v2.2+ format)
                packages = buy.raw_request.get("packages", [])

                # Fallback: legacy format with product_ids
                if not packages and "product_ids" in buy.raw_request:
                    product_ids = buy.raw_request.get("product_ids", [])
                    packages = [{"product_id": pid} for pid in product_ids]

                i = -1
                for pkg_data in packages:
                    i += 1

                    package_id = pkg_data.get("package_id") or f"pkg_{pkg_data.get('product_id', 'unknown')}_{i}"
                    pricing_option_id = pkg_data.get("pricing_option_id") or None

                    # Get pricing info for this package
                    pricing_info = package_pricing_map.get(package_id)
                    pricing_option = pricing_options.get(pricing_option_id) if pricing_option_id is not None else None

                    # Get REAL per-package metrics from adapter if available, otherwise divide equally
                    if package_id in adapter_package_metrics:
                        # Use real metrics from adapter
                        pkg_metrics = adapter_package_metrics[package_id]
                        package_spend = pkg_metrics["spend"]
                        package_impressions = pkg_metrics["impressions"]
                    else:
                        # Fallback: divide equally if adapter didn't return this package
                        package_spend = spend / len(packages)
                        package_impressions = impressions / len(packages)

                    if pricing_option and pricing_option.pricing_model == PricingModel.CPC and pricing_option.rate:
                        package_clicks = floor(spend / (float(pricing_option.rate)))
                    else:
                        package_clicks = None

                    package_deliveries.append(
                        PackageDelivery(
                            package_id=package_id,
                            buyer_ref=pkg_data.get("buyer_ref") or buy.raw_request.get("buyer_ref", None),
                            impressions=package_impressions or 0.0,
                            spend=package_spend or 0.0,
                            clicks=package_clicks,
                            video_completions=None,  # Optional field, not calculated in this implementation
                            pacing_index=1.0 if status == "active" else 0.0,
                            # Add pricing fields from package_config
                            pricing_model=pricing_info.get("pricing_model") if pricing_info else None,
                            rate=(
                                float(pricing_info.get("rate"))
                                if pricing_info and pricing_info.get("rate") is not None
                                else None
                            ),
                            currency=pricing_info.get("currency") if pricing_info else None,
                        )
                    )

            # Create delivery data
            buyer_ref = buy.raw_request.get("buyer_ref", None)

            # Calculate clicks and CTR (click-through rate) where applicable

            clicks = 0

            ctr = (clicks / impressions) if clicks is not None and impressions > 0 else None

            # Cast status to match Literal type requirement
            from typing import Literal as LiteralType
            from typing import cast

            status_typed = cast(
                LiteralType["ready", "active", "paused", "completed", "failed", "reporting_delayed"], status
            )
            delivery_data = MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                status=status_typed,
                pricing_model=PricingModel(
                    "cpm"
                ),  # TODO: @yusuf - remove this from adcp protocol. MediaBuy itself doesn't have pricing model. It is in package level
                totals=DeliveryTotals(
                    impressions=impressions,
                    spend=spend,
                    clicks=clicks,  # Optional field
                    ctr=ctr,  # Optional field
                    video_completions=None,  # Optional field
                    completion_rate=None,  # Optional field
                ),
                by_package=package_deliveries,
                daily_breakdown=None,  # Optional field, not calculated in this implementation
            )

            deliveries.append(delivery_data)
            total_spend += spend
            total_impressions += impressions
            media_buy_count += 1
            total_clicks += clicks if clicks is not None else 0

        except Exception as e:
            raise e
            logger.error(f"Error getting delivery for {media_buy_id}: {e}")
            # TODO: @yusuf - Ask should we attach an error message for this media buy, instead of omitting it from the response?
            # Continue with other media buys

    # Create AdCP-compliant response
    context_dict = _context_to_dict(req.context)
    response = GetMediaBuyDeliveryResponse(
        reporting_period=reporting_period,
        currency="USD",  # TODO: @yusuf - This is wrong. Currency should be at the media buy delivery level, not on aggregated totals.
        aggregated_totals=AggregatedTotals(
            impressions=float(total_impressions),
            spend=total_spend,
            clicks=float(total_clicks) if total_clicks else None,
            video_completions=None,
            media_buy_count=media_buy_count,
        ),
        media_buy_deliveries=deliveries,
        errors=None,
        context=context_dict,
    )

    # Apply testing hooks if needed
    if any([testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]):
        # Create campaign info for testing hooks
        campaign_info = None
        if target_media_buys:
            first_buy = target_media_buys[0][1]
            # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
            from typing import cast as type_cast

            first_buy_start = type_cast(date, first_buy.start_date)
            first_buy_end = type_cast(date, first_buy.end_date)
            campaign_info = {
                "start_date": datetime.combine(first_buy_start, datetime.min.time()),
                "end_date": datetime.combine(first_buy_end, datetime.min.time()),
                "total_budget": float(first_buy.budget) if first_buy.budget else 0.0,
            }

        # Apply testing hooks for metadata (spend tracking, response headers)
        # No mutations survive for delivery — response model stays unchanged
        apply_testing_hooks(
            testing_ctx,
            "get_media_buy_delivery",
            campaign_info,
            spend_amount=float(response.aggregated_totals.spend or 0),
        )

    return response


def get_media_buy_delivery(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get delivery data for media buys.

    AdCP-compliant implementation of get_media_buy_delivery tool.

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus enums (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        context: Application level context object (ContextObject)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetMediaBuyDeliveryResponse data
    """
    # Create AdCP-compliant request object
    try:
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=media_buy_ids,
            buyer_refs=buyer_refs,
            status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
            start_date=start_date,
            end_date=end_date,
            context=cast(ContextObject | None, context),
        )

        response = _get_media_buy_delivery_impl(req, ctx)

        return ToolResult(content=str(response), structured_content=response)
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buy_delivery request"))


def get_media_buy_delivery_raw(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get delivery metrics for media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus enums (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        context: Application level context (ContextObject)
        ctx: Context for authentication

    Returns:
        GetMediaBuyDeliveryResponse with delivery metrics
    """
    # Create request object
    req = GetMediaBuyDeliveryRequest(
        media_buy_ids=media_buy_ids,
        buyer_refs=buyer_refs,
        status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
        start_date=start_date,
        end_date=end_date,
        context=cast(ContextObject | None, context),
    )

    # Call the implementation
    return _get_media_buy_delivery_impl(req, ctx)


# --- Admin Tools ---


def _require_admin(context: Context) -> None:
    """Verify the request is from an admin user."""
    principal_id = get_principal_id_from_context(context)
    if principal_id != "admin":
        raise PermissionError("This operation requires admin privileges")


# -- Helper functions --
def _get_target_media_buys(
    req: GetMediaBuyDeliveryRequest,
    principal_id: str,
    tenant: dict[str, Any],
    reference_date: date,
) -> list[tuple[str, MediaBuy]]:
    with get_db_session() as session:
        # Use status_filter to determine which buys to fetch
        # Internal statuses: ready, active, paused, completed, failed
        # AdCP MediaBuyStatus: pending_activation, active, paused, completed
        # Map: pending_activation -> ready (internal)
        valid_internal_statuses = ["active", "ready", "paused", "completed", "failed"]
        filter_statuses: list[str] = []

        def normalize_status(status: str) -> str:
            """Convert AdCP status to internal status."""
            # AdCP 'pending_activation' maps to internal 'ready'
            if status == "pending_activation":
                return "ready"
            return status

        if req.status_filter:
            # Handle both enum values and strings (enum has .value attribute)
            if isinstance(req.status_filter, list):
                for s in req.status_filter:
                    status_str = s.value if hasattr(s, "value") else str(s)
                    normalized = normalize_status(status_str)
                    if normalized in valid_internal_statuses:
                        filter_statuses.append(normalized)
            else:
                status_str = req.status_filter.value if hasattr(req.status_filter, "value") else str(req.status_filter)
                if status_str == "all":
                    filter_statuses = valid_internal_statuses
                else:
                    normalized = normalize_status(status_str)
                    filter_statuses = [normalized]
        else:
            # Default to active
            filter_statuses = ["active"]

        fetched_buys: Sequence[MediaBuy] = []
        target_media_buys: list[tuple[str, MediaBuy]] = []  # list of tuples(media_buy_id, MediaBuy)

        if req.media_buy_ids:
            # Specific media buy IDs requested
            stmt = select(MediaBuy).where(
                # TODO: @yusuf- Do we need to filter by tenant_id?
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.media_buy_id.in_(req.media_buy_ids),
            )
            fetched_buys = session.scalars(stmt).all()

        elif req.buyer_refs:
            # Buyer references requested
            stmt = select(MediaBuy).where(
                # TODO: @yusuf- Do we need to filter by tenant_id?
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.buyer_ref.in_(req.buyer_refs),
            )
            fetched_buys = session.scalars(stmt).all()

        else:
            # Fetch all media buys for this principal
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
            )
            fetched_buys = session.scalars(stmt).all()

        # Filter by status based on date ranges
        for buy in fetched_buys:
            # Determine current status based on dates
            # Use start_time/end_time if available, otherwise fall back to start_date/end_date
            # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
            from typing import cast as type_cast

            if buy.start_time:
                start_compare = buy.start_time.date()
            else:
                start_compare = type_cast(date, buy.start_date)

            if buy.end_time:
                end_compare = buy.end_time.date()
            else:
                end_compare = type_cast(date, buy.end_date)

            if reference_date < start_compare:
                current_status = "ready"
            elif reference_date > end_compare:
                current_status = "completed"
            else:
                current_status = "active"

            if current_status in filter_statuses:
                target_media_buys.append((buy.media_buy_id, buy))

        return target_media_buys


def _get_pricing_options(pricing_option_ids: list[Any | None]) -> dict[str, PricingOption]:
    with get_db_session() as session:
        statement = select(PricingOption).where(PricingOption.id.in_(pricing_option_ids))
        pricing_options = session.scalars(statement).all()
        return {str(pricing_option.id): pricing_option for pricing_option in pricing_options}

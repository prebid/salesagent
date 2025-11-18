"""Get Media Buy Delivery tool implementation.

Handles delivery metrics reporting including:
- Campaign delivery totals (impressions, spend)
- Package-level delivery breakdown
- Status filtering (active, paused, completed)
- Date range reporting
- Testing mode simulation
"""

import logging
from datetime import date, datetime, timedelta

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)
console = Console()

from adcp.types.generated_poc.push_notification_config import PushNotificationConfig

from src.core.auth import get_principal_object
from src.core.helpers import get_principal_id_from_context
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schema_adapters import GetMediaBuyDeliveryResponse
from src.core.schemas import (
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    MediaBuyDeliveryData,
    PackageDelivery,
    ReportingPeriod,
)
from src.core.testing_hooks import DeliverySimulator, TimeSimulator, apply_testing_hooks, get_testing_context
from src.core.validation_helpers import format_validation_error


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
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
            aggregated_totals={
                "impressions": 0,
                "spend": 0,
                "clicks": None,
                "video_completions": None,
                "media_buy_count": 0,
            },
            media_buy_deliveries=[],
            errors=[{"code": "principal_id_missing", "message": "Principal ID not found in context"}],
        )

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        # Return AdCP-compliant error response
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
            aggregated_totals={
                "impressions": 0,
                "spend": 0,
                "clicks": None,
                "video_completions": None,
                "media_buy_count": 0,
            },
            media_buy_deliveries=[],
            errors=[{"code": "principal_not_found", "message": f"Principal {principal_id} not found"}],
        )

    # Get the appropriate adapter
    # Use testing_ctx.dry_run if in testing mode, otherwise False
    adapter = get_adapter(principal, dry_run=testing_ctx.dry_run if testing_ctx else False, testing_context=testing_ctx)

    # Determine reporting period
    if req.start_date and req.end_date:
        # Use provided date range
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d")
    else:
        # Default to last 30 days
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=30)

    reporting_period = ReportingPeriod(start=start_dt.isoformat(), end=end_dt.isoformat())

    # Determine reference date for status calculations (use end_date or current date)
    reference_date = end_dt.date() if req.end_date else date.today()

    # Determine which media buys to fetch from database
    from sqlalchemy import select

    from src.core.config_loader import get_current_tenant
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy

    tenant = get_current_tenant()
    target_media_buys = []

    with get_db_session() as session:
        if req.media_buy_ids:
            # Specific media buy IDs requested
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.media_buy_id.in_(req.media_buy_ids),
            )
            buys = session.scalars(stmt).all()
            target_media_buys = [(buy.media_buy_id, buy) for buy in buys]

        elif req.buyer_refs:
            # Buyer references requested
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
                MediaBuy.buyer_ref.in_(req.buyer_refs),
            )
            buys = session.scalars(stmt).all()
            target_media_buys = [(buy.media_buy_id, buy) for buy in buys]

        else:
            # Use status_filter to determine which buys to fetch
            valid_statuses = ["active", "ready", "paused", "completed", "failed"]
            filter_statuses = []

            if req.status_filter:
                if isinstance(req.status_filter, str):
                    if req.status_filter == "all":
                        filter_statuses = valid_statuses
                    else:
                        filter_statuses = [req.status_filter]
                elif isinstance(req.status_filter, list):
                    filter_statuses = req.status_filter
            else:
                # Default to active
                filter_statuses = ["active"]

            # Fetch all media buys for this principal
            stmt = select(MediaBuy).where(
                MediaBuy.tenant_id == tenant["tenant_id"],
                MediaBuy.principal_id == principal_id,
            )
            all_buys = session.scalars(stmt).all()

            # Filter by status based on date ranges
            for buy in all_buys:
                # Determine current status based on dates
                # Use start_time/end_time if available, otherwise fall back to start_date/end_date
                # Note: buy.start_time/end_time are Python datetime objects (not SQLAlchemy DateTime type)
                # Note: buy.start_date and buy.end_date are Python date objects (not SQLAlchemy Date type)
                if buy.start_time:
                    start_compare: date = buy.start_time.date()  # type: ignore[union-attr,attr-defined]
                else:
                    start_compare = buy.start_date  # type: ignore[assignment]

                if buy.end_time:
                    end_compare: date = buy.end_time.date()  # type: ignore[union-attr,attr-defined]
                else:
                    end_compare = buy.end_date  # type: ignore[assignment]

                if reference_date < start_compare:
                    current_status = "ready"
                elif reference_date > end_compare:
                    current_status = "completed"
                else:
                    current_status = "active"

                if current_status in filter_statuses:
                    target_media_buys.append((buy.media_buy_id, buy))

    # Collect delivery data for each media buy
    deliveries = []
    total_spend = 0.0
    total_impressions = 0
    media_buy_count = 0

    for media_buy_id, buy in target_media_buys:
        try:
            # Apply time simulation from testing context
            simulation_datetime = end_dt
            if testing_ctx.mock_time:
                simulation_datetime = testing_ctx.mock_time
            elif testing_ctx.jump_to_event:
                # Calculate time based on event
                # Note: buy.start_date and buy.end_date are Python date objects (not SQLAlchemy Date type)
                buy_start_date: date = buy.start_date  # type: ignore[assignment]
                buy_end_date: date = buy.end_date  # type: ignore[assignment]
                simulation_datetime = TimeSimulator.jump_to_event_time(
                    testing_ctx.jump_to_event,
                    datetime.combine(buy_start_date, datetime.min.time()),
                    datetime.combine(buy_end_date, datetime.min.time()),
                )

            # Determine status
            # Note: buy.start_date and buy.end_date are Python date objects
            buy_start_date_status: date = buy.start_date  # type: ignore[assignment]
            buy_end_date_status: date = buy.end_date  # type: ignore[assignment]
            if simulation_datetime.date() < buy_start_date_status:
                status = "ready"
            elif simulation_datetime.date() > buy_end_date_status:
                status = "completed"
            else:
                status = "active"

            # Create delivery metrics
            if any(
                [testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]
            ):
                # Use simulation for testing
                # Note: buy.start_date and buy.end_date are Python date objects
                buy_start_date_sim: date = buy.start_date  # type: ignore[assignment]
                buy_end_date_sim: date = buy.end_date  # type: ignore[assignment]
                start_dt = datetime.combine(buy_start_date_sim, datetime.min.time())
                end_dt_campaign = datetime.combine(buy_end_date_sim, datetime.min.time())
                progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt_campaign, simulation_datetime)

                simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                    float(buy.budget) if buy.budget else 0.0, progress, testing_ctx
                )

                spend = simulated_metrics["spend"]
                impressions = simulated_metrics["impressions"]
            else:
                # Generate realistic delivery metrics
                # Note: buy.start_date and buy.end_date are Python date objects
                buy_start_date_metrics: date = buy.start_date  # type: ignore[assignment]
                buy_end_date_metrics: date = buy.end_date  # type: ignore[assignment]
                campaign_days = (buy_end_date_metrics - buy_start_date_metrics).days
                days_elapsed = max(0, (simulation_datetime.date() - buy_start_date_metrics).days)

                if campaign_days > 0:
                    progress = min(1.0, days_elapsed / campaign_days) if status != "ready" else 0.0
                else:
                    progress = 1.0 if status == "completed" else 0.0

                spend = float(buy.budget) * progress if buy.budget else 0.0
                impressions = int(spend * 1000)  # Assume $1 CPM for simplicity

            # Create package delivery data
            package_deliveries = []
            if buy.raw_request and isinstance(buy.raw_request, dict) and "product_ids" in buy.raw_request:
                product_ids = buy.raw_request.get("product_ids", [])
                for i, product_id in enumerate(product_ids):
                    package_spend = spend / len(product_ids) if product_ids else spend
                    package_impressions = impressions / len(product_ids) if product_ids else impressions

                    package_deliveries.append(
                        PackageDelivery(
                            package_id=f"pkg_{product_id}_{i}",
                            buyer_ref=buy.raw_request.get("buyer_ref", None),
                            impressions=package_impressions,
                            spend=package_spend,
                            # TODO: Calculate clicks for CPC pricing - extract pricing model from raw_request
                            clicks=None,  # Optional field, not calculated in this implementation
                            video_completions=None,  # Optional field, not calculated in this implementation
                            pacing_index=1.0 if status == "active" else 0.0,
                        )
                    )

            # Create delivery data
            buyer_ref = buy.raw_request.get("buyer_ref", None) if buy.raw_request else None
            # Type cast status to match Literal type
            status_literal: str = status
            delivery_data = MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                status=status_literal,  # type: ignore[arg-type]
                totals=DeliveryTotals(
                    impressions=impressions,
                    spend=spend,
                    # TODO: Calculate clicks for CPC pricing models - should be required for CPC
                    # Need to: 1) Extract pricing model from raw_request packages
                    #          2) Calculate clicks based on spend/CPC rate
                    #          3) Make clicks required (not None) for CPC pricing
                    clicks=None,  # Optional field
                    ctr=None,  # Optional field
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

        except Exception as e:
            logger.error(f"Error getting delivery for {media_buy_id}: {e}")
            # Continue with other media buys

    # Create AdCP-compliant response
    response = GetMediaBuyDeliveryResponse(
        reporting_period=reporting_period,
        currency="USD",
        aggregated_totals={
            "impressions": total_impressions,
            "spend": total_spend,
            "clicks": None,
            "video_completions": None,
            "media_buy_count": media_buy_count,
        },
        media_buy_deliveries=deliveries,
        context=req.context or None,
    )

    # Apply testing hooks if needed
    if any([testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]):
        # Create campaign info for testing hooks
        campaign_info = None
        if target_media_buys:
            first_buy = target_media_buys[0][1]
            # Note: first_buy.start_date and first_buy.end_date are Python date objects
            first_buy_start: date = first_buy.start_date  # type: ignore[assignment]
            first_buy_end: date = first_buy.end_date  # type: ignore[assignment]
            campaign_info = {
                "start_date": datetime.combine(first_buy_start, datetime.min.time()),
                "end_date": datetime.combine(first_buy_end, datetime.min.time()),
                "total_budget": float(first_buy.budget) if first_buy.budget else 0.0,
            }

        # Convert to dict for testing hooks
        response_data = response.model_dump()
        response_data = apply_testing_hooks(response_data, testing_ctx, "get_media_buy_delivery", campaign_info)

        # Reconstruct response from modified data - filter out testing hook fields
        valid_fields = {
            "reporting_period",
            "currency",
            "aggregated_totals",
            "media_buy_deliveries",
            "notification_type",
            "partial_data",
            "unavailable_count",
            "sequence_number",
            "next_expected_at",
            "errors",
        }
        filtered_data = {k: v for k, v in response_data.items() if k in valid_fields}

        # Ensure required fields are present (validator compliance)
        if "reporting_period" not in filtered_data:
            filtered_data["reporting_period"] = response_data.get("reporting_period", reporting_period)
        if "currency" not in filtered_data:
            filtered_data["currency"] = response_data.get("currency", "USD")
        if "aggregated_totals" not in filtered_data:
            filtered_data["aggregated_totals"] = response_data.get(
                "aggregated_totals",
                {
                    "impressions": total_impressions,
                    "spend": total_spend,
                    "clicks": None,
                    "video_completions": None,
                    "media_buy_count": media_buy_count,
                },
            )
        if "media_buy_deliveries" not in filtered_data:
            filtered_data["media_buy_deliveries"] = response_data.get("media_buy_deliveries", [])

        # Use explicit fields for validator (instead of **kwargs)
        response = GetMediaBuyDeliveryResponse(
            reporting_period=filtered_data["reporting_period"],
            currency=filtered_data["currency"],
            aggregated_totals=filtered_data["aggregated_totals"],
            media_buy_deliveries=filtered_data["media_buy_deliveries"],
            notification_type=filtered_data.get("notification_type"),
            partial_data=filtered_data.get("partial_data"),
            unavailable_count=filtered_data.get("unavailable_count"),
            sequence_number=filtered_data.get("sequence_number"),
            next_expected_at=filtered_data.get("next_expected_at"),
            errors=filtered_data.get("errors"),
            context=req.context or None,
        )

    return response


def get_media_buy_delivery(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    context: dict | None = None,  # Application level context per adcp spec
    webhook_url: str | None = None,
    push_notification_config: PushNotificationConfig | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get delivery data for media buys.

    AdCP-compliant implementation of get_media_buy_delivery tool.

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array: 'active', 'pending', 'paused', 'completed', 'failed', 'all' (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        push_notification_config: Optional webhook configuration (accepted, ignored by this operation)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetMediaBuyDeliveryResponse data
    """
    # Create AdCP-compliant request object
    try:
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=media_buy_ids,
            buyer_refs=buyer_refs,
            status_filter=status_filter,
            start_date=start_date,
            end_date=end_date,
            push_notification_config=push_notification_config,
            context=context,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buy_delivery request")) from e

    response = _get_media_buy_delivery_impl(req, ctx)
    return ToolResult(content=str(response), structured_content=response.model_dump())


def get_media_buy_delivery_raw(
    media_buy_ids: list[str] | None = None,
    buyer_refs: list[str] | None = None,
    status_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    context: dict | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """Get delivery metrics for media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        context: Context for authentication

    Returns:
        GetMediaBuyDeliveryResponse with delivery metrics
    """
    # Create request object
    from src.core.schemas import GetMediaBuyDeliveryRequest

    req = GetMediaBuyDeliveryRequest(
        media_buy_ids=media_buy_ids,
        buyer_refs=buyer_refs,
        status_filter=status_filter,
        start_date=start_date,
        end_date=end_date,
        push_notification_config=None,
        context=context,
    )

    # Call the implementation
    return _get_media_buy_delivery_impl(req, ctx)


# --- Admin Tools ---


def _require_admin(context: Context) -> None:
    """Verify the request is from an admin user."""
    principal_id = get_principal_id_from_context(context)
    if principal_id != "admin":
        raise PermissionError("This operation requires admin privileges")

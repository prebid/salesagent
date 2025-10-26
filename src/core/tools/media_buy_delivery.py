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
from pydantic import ValidationError
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

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


def _get_media_buy_delivery_impl(req: GetMediaBuyDeliveryRequest, context: Context) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for one or more media buys.

    AdCP-compliant implementation that handles start_date/end_date parameters
    and returns spec-compliant response format.
    """

    # Extract testing context for time simulation and event jumping
    testing_ctx = get_testing_context(context)

    principal_id = get_principal_id_from_context(context)

    # Get the Principal object
    principal = get_principal_object(principal_id)
    if not principal:
        # Return AdCP-compliant error response
        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=datetime.now().isoformat(), end=datetime.now().isoformat()),
            currency="USD",
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
                if reference_date < buy.start_time.date():
                    current_status = "ready"
                elif reference_date > buy.end_time.date():
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
                simulation_datetime = TimeSimulator.jump_to_event_time(
                    testing_ctx.jump_to_event,
                    datetime.combine(buy.start_date, datetime.min.time()),
                    datetime.combine(buy.end_date, datetime.min.time()),
                )

            # Determine status
            if simulation_datetime.date() < buy.start_date:
                status = "ready"
            elif simulation_datetime.date() > buy.end_date:
                status = "completed"
            else:
                status = "active"

            # Create delivery metrics
            if any(
                [testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]
            ):
                # Use simulation for testing
                start_dt = datetime.combine(buy.start_date, datetime.min.time())
                end_dt_campaign = datetime.combine(buy.end_date, datetime.min.time())
                progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt_campaign, simulation_datetime)

                simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                    float(buy.budget) if buy.budget else 0.0, progress, testing_ctx
                )

                spend = simulated_metrics["spend"]
                impressions = simulated_metrics["impressions"]
            else:
                # Generate realistic delivery metrics
                campaign_days = (buy_request.flight_end_date - buy_request.flight_start_date).days
                days_elapsed = max(0, (simulation_datetime.date() - buy_request.flight_start_date).days)

                if campaign_days > 0:
                    progress = min(1.0, days_elapsed / campaign_days) if status != "ready" else 0.0
                else:
                    progress = 1.0 if status == "completed" else 0.0

                spend = float(buy_request.total_budget * progress)
                impressions = int(spend * 1000)  # Assume $1 CPM for simplicity

            # Create package delivery data
            package_deliveries = []
            if hasattr(buy_request, "product_ids"):
                for i, product_id in enumerate(buy_request.product_ids):
                    package_spend = spend / len(buy_request.product_ids)
                    package_impressions = impressions / len(buy_request.product_ids)

                    package_deliveries.append(
                        PackageDelivery(
                            package_id=f"pkg_{product_id}_{i}",
                            buyer_ref=getattr(buy_request, "buyer_ref", None),
                            impressions=package_impressions,
                            spend=package_spend,
                            pacing_index=1.0 if status == "active" else 0.0,
                        )
                    )

            # Create delivery data
            delivery_data = MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                buyer_ref=getattr(buy_request, "buyer_ref", None),
                status=status,
                totals=DeliveryTotals(impressions=impressions, spend=spend),
                by_package=package_deliveries,
            )

            deliveries.append(delivery_data)
            total_spend += spend
            total_impressions += impressions
            media_buy_count += 1

        except Exception as e:
            console.print(f"[red]Error getting delivery for {media_buy_id}: {e}[/red]")
            # Continue with other media buys

    # Create AdCP-compliant response
    response = GetMediaBuyDeliveryResponse(
        reporting_period=reporting_period,
        currency="USD",
        media_buy_deliveries=deliveries,
    )

    # Apply testing hooks if needed
    if any([testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]):
        # Create campaign info for testing hooks
        campaign_info = None
        if target_media_buys:
            first_buy = target_media_buys[0][1]
            campaign_info = {
                "start_date": datetime.combine(first_buy.flight_start_date, datetime.min.time()),
                "end_date": datetime.combine(first_buy.flight_end_date, datetime.min.time()),
                "total_budget": first_buy.total_budget,
            }

        # Convert to dict for testing hooks
        response_data = response.model_dump()
        response_data = apply_testing_hooks(response_data, testing_ctx, "get_media_buy_delivery", campaign_info)

        # Reconstruct response from modified data - filter out testing hook fields
        valid_fields = {
            "reporting_period",
            "currency",
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
        if "media_buy_deliveries" not in filtered_data:
            filtered_data["media_buy_deliveries"] = response_data.get("media_buy_deliveries", [])

        # Use explicit fields for validator (instead of **kwargs)
        response = GetMediaBuyDeliveryResponse(
            reporting_period=filtered_data["reporting_period"],
            currency=filtered_data["currency"],
            media_buy_deliveries=filtered_data["media_buy_deliveries"],
            notification_type=filtered_data.get("notification_type"),
            partial_data=filtered_data.get("partial_data"),
            unavailable_count=filtered_data.get("unavailable_count"),
            sequence_number=filtered_data.get("sequence_number"),
            next_expected_at=filtered_data.get("next_expected_at"),
            errors=filtered_data.get("errors"),
        )

    return response


def get_media_buy_delivery(
    media_buy_ids: list[str] = None,
    buyer_refs: list[str] = None,
    status_filter: str = None,
    start_date: str = None,
    end_date: str = None,
    webhook_url: str | None = None,
    context: Context = None,
) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for media buys.

    AdCP-compliant implementation of get_media_buy_delivery tool.

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        buyer_refs: Array of buyer reference IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array: 'active', 'pending', 'paused', 'completed', 'failed', 'all' (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        webhook_url: URL for async task completion notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        GetMediaBuyDeliveryResponse with AdCP-compliant delivery data for the requested media buys
    """
    # Create AdCP-compliant request object
    try:
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=media_buy_ids,
            buyer_refs=buyer_refs,
            status_filter=status_filter,
            start_date=start_date,
            end_date=end_date,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="get_media_buy_delivery request")) from e

    return _get_media_buy_delivery_impl(req, context)


def get_media_buy_delivery_raw(
    media_buy_ids: list[str] = None,
    buyer_refs: list[str] = None,
    status_filter: str = None,
    start_date: str = None,
    end_date: str = None,
    context: Context = None,
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
    )

    # Call the implementation
    return _get_media_buy_delivery_impl(req, context)


# --- Admin Tools ---


def _require_admin(context: Context) -> None:
    """Verify the request is from an admin user."""
    principal_id = get_principal_id_from_context(context)
    if principal_id != "admin":
        raise PermissionError("This operation requires admin privileges")

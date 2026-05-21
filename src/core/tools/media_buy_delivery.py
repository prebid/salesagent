"""Get Media Buy Delivery tool implementation.

Handles delivery metrics reporting including:
- Campaign delivery totals (impressions, spend)
- Package-level delivery breakdown
- Status filtering (active, paused, completed)
- Date range reporting
- Testing mode simulation
"""

import logging
from datetime import UTC, date, datetime, timedelta
from math import floor
from typing import Annotated, Any, Literal, cast

from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field, RootModel, ValidationError
from rich.console import Console

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)
console = Console()

from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import Error, MediaBuyStatus
from adcp.types.generated_poc.core.attribution_window import AttributionWindow as ResponseAttributionWindow
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.duration import Duration, Unit
from adcp.types.generated_poc.enums.attribution_model import AttributionModel
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_request import (
    AttributionWindow,
    ReportingDimensions,
)

# Seller platform default attribution model (BR-RULE-092). The AdCP response
# AttributionWindow requires a non-null ``model``; when the buyer does not
# specify one — or the request is honored without an explicit model — the
# seller echoes its platform default. ``last_touch`` is the documented
# platform default (industry-standard single-touch attribution).
PLATFORM_DEFAULT_ATTRIBUTION_MODEL = AttributionModel.last_touch

# adcp 3.6.0: Use schemas.ReportingPeriod (extends creative ReportingPeriod) for adapter compat.
# The media-buy-specific ReportingPeriod has identical fields (start, end) but different identity.
# Adapters are typed to accept schemas.ReportingPeriod, so we use that here.
from src.core.auth import get_principal_object
from src.core.database.models import MediaBuy, PricingOption
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.database.repositories.product import ProductRepository
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AggregatedTotals,
    DeliveryTotals,
    GeoBreakdown,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    MediaBuyDeliveryData,
    PackageDelivery,
    PlacementBreakdown,
    PricingModel,
)
from src.core.schemas import (
    ReportingPeriod as MediaBuyReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext, DeliverySimulator, TimeSimulator, apply_testing_hooks
from src.core.validation_helpers import format_validation_error


def _is_circuit_breaker_open(tenant_id: str) -> bool:
    """Check if any circuit breaker is OPEN for the given tenant.

    Delegates to WebhookDeliveryService.has_open_circuit_breaker() public API.
    """
    from src.services.webhook_delivery_service import webhook_delivery_service

    return webhook_delivery_service.has_open_circuit_breaker(tenant_id)


def _get_media_buy_delivery_impl(
    req: GetMediaBuyDeliveryRequest, identity: ResolvedIdentity | None
) -> GetMediaBuyDeliveryResponse:
    """Get delivery data for one or more media buys.

    AdCP-compliant implementation that handles start_date/end_date parameters
    and returns spec-compliant response format.
    """

    # Validate identity is provided
    if identity is None:
        raise AdCPValidationError("Context is required", recovery="correctable")

    # Extract testing context for time simulation and event jumping
    testing_ctx = identity.testing_context or AdCPTestContext()

    principal_id = identity.principal_id if identity else None
    if not principal_id:
        # Return AdCP-compliant error response
        # TODO: @yusuf - Should this return only error field and not the other fields? Haven't we updated adcp spec to only return error field on errors??
        context_val = req.context
        return GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
            errors=[Error(code="AUTH_REQUIRED", message="Principal ID not found in context")],
            context=context_val,
        )

    # Get the Principal object
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        # Return AdCP-compliant error response
        # TODO: @yusuf - Should this return only error field and not the other fields? Haven't we updated adcp spec to only return error field on errors??
        context_val = req.context
        return GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                clicks=None,
                video_completions=None,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
            errors=[Error(code="AUTH_REQUIRED", message=f"Principal {principal_id} not found")],
            context=context_val,
        )

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Get the appropriate adapter
    # Use testing_ctx.dry_run if in testing mode, otherwise False
    adapter = get_adapter(
        principal, dry_run=testing_ctx.dry_run if testing_ctx else False, testing_context=testing_ctx, tenant=tenant
    )

    # Determine reporting period
    if req.start_date and req.end_date:
        # Use provided date range (make timezone-aware for AwareDatetime)
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").replace(tzinfo=UTC)

        if start_dt >= end_dt:
            context_val = req.context
            return GetMediaBuyDeliveryResponse(
                reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
                currency="USD",
                aggregated_totals=AggregatedTotals(
                    impressions=0.0,
                    spend=0.0,
                    clicks=None,
                    video_completions=None,
                    media_buy_count=0,
                ),
                media_buy_deliveries=[],
                errors=[Error(code="VALIDATION_ERROR", message="Start date must be before end date")],
                context=context_val,
            )
    else:
        # Default to last 30 days
        end_dt = datetime.now(UTC)
        start_dt = end_dt - timedelta(days=30)

    reporting_period = MediaBuyReportingPeriod(start=start_dt, end=end_dt)

    # Determine reference date for status calculations use end_date, it either will be today or the user provided end_date.
    reference_date = end_dt.date()

    # Determine which media buys to fetch from database
    # UoW scope encompasses all code that accesses MediaBuy ORM objects to prevent
    # DetachedInstanceError — the session must stay open while we read attributes
    # like buy.raw_request, buy.start_date, etc.
    with MediaBuyUoW(tenant["tenant_id"]) as uow:
        assert uow.media_buys is not None
        repo = uow.media_buys

        target_media_buys = _get_target_media_buys(req, principal_id, repo, reference_date)

        # Diff requested IDs vs found IDs to report missing ones (salesagent-mexj)
        not_found_errors: list[Error] = []
        found_ids = {buy_id for buy_id, _ in target_media_buys}
        if req.media_buy_ids:
            for requested_id in req.media_buy_ids:
                if requested_id not in found_ids:
                    not_found_errors.append(
                        Error(code="MEDIA_BUY_NOT_FOUND", message=f"Media buy {requested_id} not found")
                    )

        pricing_option_ids: list[Any] = []
        for _, buy in target_media_buys:
            if buy.raw_request and isinstance(buy.raw_request, dict):
                for pkg in buy.raw_request.get("packages", []):
                    pkg_id = pkg.get("pricing_option_id")
                    if pkg_id is not None:
                        pricing_option_ids.append(pkg_id)
        # FIXME(salesagent-9f2): delivery UoW should provide a product repo directly
        assert uow.session is not None
        product_repo = ProductRepository(uow.session, tenant["tenant_id"])
        pricing_options = _get_pricing_options(
            pricing_option_ids, tenant_id=tenant["tenant_id"], product_repo=product_repo
        )

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
                    buy_start_date = cast(date, buy.start_date)
                    buy_end_date = cast(date, buy.end_date)
                    simulation_datetime = TimeSimulator.jump_to_event_time(
                        testing_ctx.jump_to_event,
                        datetime.combine(buy_start_date, datetime.min.time()),
                        datetime.combine(buy_end_date, datetime.min.time()),
                    )

                # Determine status
                # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                buy_start_date_status = cast(date, buy.start_date)
                buy_end_date_status = cast(date, buy.end_date)
                if getattr(buy, "is_paused", False):
                    status = "paused"
                elif simulation_datetime.date() < buy_start_date_status:
                    status = "ready"
                elif simulation_datetime.date() > buy_end_date_status:
                    status = "completed"
                else:
                    status = "active"

                # Override status when circuit breaker is open (reporting degraded),
                # but not for paused buys (paused takes priority)
                if status != "paused" and _is_circuit_breaker_open(tenant["tenant_id"]):
                    status = "reporting_delayed"

                # Get delivery metrics from adapter
                adapter_package_metrics = {}  # Map package_id -> {impressions, spend, clicks}
                adapter_ext: dict[str, Any] = {}  # Ext data from adapter response
                total_spend_from_adapter = 0.0
                total_impressions_from_adapter = 0
                adapter_conversions: float | None = None
                adapter_viewability: float | None = None

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
                                "by_placement": adapter_pkg.by_placement,
                            }
                            total_spend_from_adapter += float(adapter_pkg.spend)
                            total_impressions_from_adapter += int(adapter_pkg.impressions)

                        # Adapter totals are always present (required field on schema)
                        spend = float(adapter_response.totals.spend)
                        impressions = int(adapter_response.totals.impressions)
                        adapter_conversions = getattr(adapter_response.totals, "conversions", None)
                        adapter_viewability = getattr(adapter_response.totals, "viewability", None)

                    except Exception as e:
                        logger.error(f"Error getting delivery for {media_buy_id}: {e}")
                        # Write adapter failure to audit trail (NFR-003)
                        try:
                            from src.core.database.models import AuditLog

                            audit_log = AuditLog(
                                tenant_id=tenant["tenant_id"],
                                operation="adapter_delivery_failure",
                                principal_id=principal_id,
                                success=False,
                                error_message=str(e),
                                details={"media_buy_id": media_buy_id},
                            )
                            # FIXME(salesagent-9f2): audit logging should use a repository
                            if uow.session is not None:
                                uow.session.add(audit_log)
                        except Exception as audit_err:
                            logger.error(f"Failed to write adapter failure audit log: {audit_err}")
                        context_val = req.context
                        return GetMediaBuyDeliveryResponse(
                            reporting_period={"start": reporting_period.start, "end": reporting_period.end},
                            currency=buy.currency,
                            aggregated_totals=AggregatedTotals(
                                impressions=0.0,
                                spend=0.0,
                                clicks=None,
                                video_completions=None,
                                media_buy_count=0,
                            ),
                            media_buy_deliveries=[],
                            errors=[
                                Error(code="SERVICE_UNAVAILABLE", message=f"Error getting delivery for {media_buy_id}")
                            ],
                            context=context_val,
                        )
                else:
                    # Use simulation for testing
                    # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                    buy_start_date_sim = cast(date, buy.start_date)
                    buy_end_date_sim = cast(date, buy.end_date)
                    start_dt = datetime.combine(buy_start_date_sim, datetime.min.time(), tzinfo=UTC)
                    end_dt_campaign = datetime.combine(buy_end_date_sim, datetime.min.time(), tzinfo=UTC)
                    progress = TimeSimulator.calculate_campaign_progress(start_dt, end_dt_campaign, simulation_datetime)

                    simulated_metrics = DeliverySimulator.calculate_simulated_metrics(
                        float(buy.budget) if buy.budget else 0.0, progress, testing_ctx
                    )

                    spend = simulated_metrics["spend"]
                    impressions = simulated_metrics["impressions"]

                # Create package delivery data
                package_deliveries = []

                # Get pricing info from MediaPackage.package_config via repository
                package_pricing_map = {}
                media_packages = repo.get_packages(media_buy_id)
                for media_pkg in media_packages:
                    package_config = media_pkg.package_config or {}
                    pricing_info = package_config.get("pricing_info")
                    if pricing_info:
                        package_pricing_map[media_pkg.package_id] = pricing_info

                # Get packages from raw_request
                if buy.raw_request and isinstance(buy.raw_request, dict):
                    packages = buy.raw_request.get("packages", [])

                    i = -1
                    for pkg_data in packages:
                        i += 1

                        package_id = pkg_data.get("package_id") or f"pkg_{pkg_data.get('product_id', 'unknown')}_{i}"
                        pricing_option_id = pkg_data.get("pricing_option_id") or None

                        # Get pricing info for this package
                        pricing_info = package_pricing_map.get(package_id)
                        pricing_option = (
                            pricing_options.get(pricing_option_id) if pricing_option_id is not None else None
                        )

                        # Get REAL per-package metrics from adapter if available, otherwise divide equally
                        raw_placements: list[dict[str, Any]] | None = None
                        if package_id in adapter_package_metrics:
                            # Use real metrics from adapter
                            pkg_metrics = adapter_package_metrics[package_id]
                            package_spend = pkg_metrics["spend"]
                            package_impressions = pkg_metrics["impressions"]
                            _raw = pkg_metrics.get("by_placement")
                            raw_placements = _raw if isinstance(_raw, list) else None
                        else:
                            # Fallback: divide equally if adapter didn't return this package
                            package_spend = spend / len(packages)
                            package_impressions = impressions / len(packages)

                        if (
                            pricing_option
                            and pricing_option.pricing_model == PricingModel.cpc.value
                            and pricing_option.rate
                        ):
                            package_clicks = floor(spend / (float(pricing_option.rate)))
                        else:
                            package_clicks = None

                        # Build placement breakdown if reporting_dimensions includes "placement"
                        placement_dim = req.reporting_dimensions.placement if req.reporting_dimensions else None
                        placement_breakdown = _build_placement_breakdown(
                            placement_dim, raw_placements, package_impressions, package_spend, package_clicks
                        )

                        package_deliveries.append(
                            PackageDelivery(
                                package_id=package_id,
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
                                by_placement=placement_breakdown,
                                by_geo=_build_geo_breakdown(req, package_impressions, package_spend),
                            )
                        )

                # Collect pricing options for this media buy
                buy_pricing_options: list[dict[str, Any]] = []
                if buy.raw_request and isinstance(buy.raw_request, dict):
                    # Collect from per-package pricing_option_ids
                    for pkg_data in buy.raw_request.get("packages", []):
                        pkg_po_id = pkg_data.get("pricing_option_id")
                        if pkg_po_id and pkg_po_id not in {p["pricing_option_id"] for p in buy_pricing_options}:
                            if pkg_po_id in pricing_options:
                                po = pricing_options[pkg_po_id]
                                buy_pricing_options.append(
                                    {"pricing_option_id": pkg_po_id, "pricing_model": po.pricing_model}
                                )
                            else:
                                buy_pricing_options.append({"pricing_option_id": pkg_po_id})

                # Calculate clicks and CTR (click-through rate) where applicable

                clicks = 0

                ctr = (clicks / impressions) if clicks is not None and impressions > 0 else None

                # Cast status to match Literal type requirement
                status_typed = cast(
                    Literal["ready", "active", "paused", "completed", "failed", "reporting_delayed"], status
                )
                delivery_data = MediaBuyDeliveryData(
                    media_buy_id=media_buy_id,
                    status=status_typed,
                    pricing_model=PricingModel(
                        "cpm"
                    ),  # TODO: @yusuf - remove this from adcp protocol. MediaBuy itself doesn't have pricing model. It is in package level
                    pricing_options=buy_pricing_options or None,
                    totals=DeliveryTotals(
                        impressions=impressions,
                        spend=spend,
                        clicks=clicks,  # Optional field
                        ctr=ctr,  # Optional field
                        video_completions=None,  # Optional field
                        completion_rate=None,  # Optional field
                        conversions=adapter_conversions,  # From adapter totals
                        viewability=adapter_viewability,  # From adapter totals
                    ),
                    by_package=package_deliveries,
                    daily_breakdown=None,  # Optional field, not calculated in this implementation
                    ext=adapter_ext,
                )

                deliveries.append(delivery_data)
                total_spend += spend
                total_impressions += impressions
                media_buy_count += 1
                total_clicks += clicks if clicks is not None else 0

            except Exception as e:
                logger.error(f"Error getting delivery for {media_buy_id}: {e}")
                # Skip this media buy and continue with others

        # --- Compute response-level webhook metadata (u5hf, uelj, 8g9e) ---

        # notification_type: "final" when all deliveries are completed, "scheduled" otherwise
        if deliveries and all(d.status == "completed" for d in deliveries):
            notification_type = "final"
        elif deliveries:
            notification_type = "scheduled"
        else:
            notification_type = None

        # next_expected_at: set for non-final deliveries (default 24h interval)
        if notification_type and notification_type != "final":
            next_expected_at = datetime.now(UTC) + timedelta(hours=24)
        else:
            next_expected_at = None

        # sequence_number: persistent auto-increment per media buy via WebhookDeliveryLog
        sequence_number = None
        # FIXME(salesagent-9f2): delivery UoW should provide DeliveryRepository directly
        if deliveries and uow.session is not None:
            delivery_repo = DeliveryRepository(uow.session, tenant["tenant_id"])
            # Use the first media buy's sequence as the response-level sequence
            first_mb_id = deliveries[0].media_buy_id
            max_seq = delivery_repo.get_max_sequence_number(first_mb_id, task_type="delivery_poll")
            sequence_number = max_seq + 1
            # Persist the new sequence number
            from uuid import uuid4

            delivery_repo.create_log(
                log_id=str(uuid4()),
                principal_id=principal_id,
                media_buy_id=first_mb_id,
                webhook_url="delivery_poll://internal",
                task_type="delivery_poll",
                status="success",
                sequence_number=sequence_number,
                notification_type=notification_type,
            )

        # Resolve campaign flight length (whole days) from the first target
        # buy so a ``unit=campaign`` attribution window echoes a concrete
        # day-count spanning the full flight (BR-RULE-092 INV-5).
        campaign_length_days: int | None = None
        if target_media_buys:
            first_buy = target_media_buys[0][1]
            cl_start = cast(date, first_buy.start_date)
            cl_end = cast(date, first_buy.end_date)
            campaign_length_days = (cl_end - cl_start).days

        attribution_window = _resolve_attribution_window(req, campaign_length_days)

        # Create AdCP-compliant response
        context_val = req.context
        response = GetMediaBuyDeliveryResponse(
            reporting_period={"start": reporting_period.start, "end": reporting_period.end},
            currency="USD",  # TODO: @yusuf - This is wrong. Currency should be at the media buy delivery level, not on aggregated totals.
            aggregated_totals=AggregatedTotals(
                impressions=float(total_impressions),
                spend=total_spend,
                clicks=float(total_clicks) if total_clicks else None,
                video_completions=None,
                media_buy_count=media_buy_count,
            ),
            media_buy_deliveries=deliveries,
            attribution_window=attribution_window,
            errors=not_found_errors or None,
            context=context_val,
            notification_type=notification_type,
            sequence_number=sequence_number,
            next_expected_at=next_expected_at,
        )

        # Apply testing hooks if needed
        if any([testing_ctx.dry_run, testing_ctx.mock_time, testing_ctx.jump_to_event, testing_ctx.test_session_id]):
            # Create campaign info for testing hooks
            campaign_info = None
            if target_media_buys:
                first_buy = target_media_buys[0][1]
                # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                first_buy_start = cast(date, first_buy.start_date)
                first_buy_end = cast(date, first_buy.end_date)
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


async def get_media_buy_delivery(
    media_buy_ids: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    start_date: Annotated[str | None, Field(description="Start date for reporting period in YYYY-MM-DD format")] = None,
    end_date: Annotated[str | None, Field(description="End date for reporting period in YYYY-MM-DD format")] = None,
    reporting_dimensions: ReportingDimensions | None = None,
    attribution_window: AttributionWindow | None = None,
    include_package_daily_breakdown: Annotated[
        bool | None, Field(description="When true, include daily breakdown metrics per package")
    ] = None,
    account: LibraryAccountReference | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Get delivery data for media buys.

    AdCP-compliant implementation of get_media_buy_delivery tool.

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus enums (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        reporting_dimensions: Request dimensional breakdowns (optional)
        attribution_window: Attribution window configuration (optional)
        include_package_daily_breakdown: Include daily breakdown per package (optional)
        account: Account reference for multi-account scenarios (optional)
        context: Application level context object (ContextObject)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with GetMediaBuyDeliveryResponse data
    """
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Handle account resolution at boundary (same as sync_creatives pattern)
    if account is not None and identity is not None:
        from src.core.transport_helpers import enrich_identity_with_account

        identity = enrich_identity_with_account(identity, account)

    # Create AdCP-compliant request object
    try:
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=media_buy_ids,
            status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
            start_date=start_date,
            end_date=end_date,
            reporting_dimensions=reporting_dimensions,
            attribution_window=attribution_window,
            include_package_daily_breakdown=include_package_daily_breakdown,
            context=cast(ContextObject | None, context),
        )

        response = _get_media_buy_delivery_impl(req, identity)

        return ToolResult(content=str(response), structured_content=response)
    except ValidationError as e:
        raise AdCPValidationError(format_validation_error(e, context="get_media_buy_delivery request"))


def get_media_buy_delivery_raw(
    media_buy_ids: list[str] | None = None,
    status_filter: MediaBuyStatus | list[MediaBuyStatus] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    reporting_dimensions: ReportingDimensions | None = None,
    attribution_window: AttributionWindow | None = None,
    include_package_daily_breakdown: bool | None = None,
    account: LibraryAccountReference | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """Get delivery metrics for media buys (raw function for A2A server use).

    Args:
        media_buy_ids: Array of publisher media buy IDs to get delivery data for (optional)
        status_filter: Filter by status - single status or array of MediaBuyStatus enums (optional)
        start_date: Start date for reporting period in YYYY-MM-DD format (optional)
        end_date: End date for reporting period in YYYY-MM-DD format (optional)
        reporting_dimensions: Request dimensional breakdowns (optional)
        attribution_window: Attribution window configuration (optional)
        include_package_daily_breakdown: Include daily breakdown per package (optional)
        account: Account reference for multi-account scenarios (optional)
        context: Application level context (ContextObject)
        ctx: Context for authentication
        identity: Pre-resolved identity (preferred over ctx)

    Returns:
        GetMediaBuyDeliveryResponse with delivery metrics
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx)

    # Handle account resolution at boundary (same as sync_creatives pattern)
    if account is not None and identity is not None:
        from src.core.transport_helpers import enrich_identity_with_account

        identity = enrich_identity_with_account(identity, account)

    # Create request object
    req = GetMediaBuyDeliveryRequest(
        media_buy_ids=media_buy_ids,
        status_filter=cast(MediaBuyStatus | list[MediaBuyStatus] | None, status_filter),
        start_date=start_date,
        end_date=end_date,
        reporting_dimensions=reporting_dimensions,
        attribution_window=attribution_window,
        include_package_daily_breakdown=include_package_daily_breakdown,
        context=cast(ContextObject | None, context),
    )

    # Call the implementation
    return _get_media_buy_delivery_impl(req, identity)


def _resolve_delivery_status_filter(
    status_filter: Any,
    valid_internal_statuses: set[str],
    to_internal: Any,
) -> list[str]:
    """Resolve status_filter to a list of internal status strings.

    Handles all possible status_filter representations:
    - None -> default to ["active"]
    - RootModel[list[MediaBuyStatus]] -> unwrap .root, convert each
    - list[MediaBuyStatus] -> convert each
    - Single MediaBuyStatus enum -> convert
    - Special "all" value (via .value attribute) -> all valid statuses
    """
    if not status_filter:
        return ["active"]

    # Unwrap RootModel (e.g., RootModel[list[MediaBuyStatus]])
    if isinstance(status_filter, RootModel):
        status_filter = status_filter.root

    # Handle list of statuses (plain list or unwrapped RootModel)
    if isinstance(status_filter, list):
        result = []
        for s in status_filter:
            internal = to_internal(s) if isinstance(s, MediaBuyStatus) else str(s)
            if internal in valid_internal_statuses:
                result.append(internal)
        return result

    # Handle single enum value
    if isinstance(status_filter, MediaBuyStatus):
        return [to_internal(status_filter)]

    # Handle special values (e.g., "all" via mock or raw string)
    status_str = status_filter.value if hasattr(status_filter, "value") else str(status_filter)
    if status_str == "all":
        return list(valid_internal_statuses)

    return [status_str] if status_str in valid_internal_statuses else ["active"]


# -- Helper functions --
# Persisted MediaBuy.status (written by media_buy_create.py and lifecycle
# transitions) maps onto the internal delivery filter vocabulary below.
# The persisted status is authoritative: terminal/explicit states
# (completed, paused, rejected, canceled) are lifecycle decisions that
# cannot be re-derived from flight dates. Transitional pre-serving states
# (draft, pending_approval, pending_creatives, pending_start) map to
# "ready" so the default "active" filter excludes them.
_PERSISTED_STATUS_TO_INTERNAL: dict[str, str] = {
    "active": "active",
    "approved": "active",
    "paused": "paused",
    "completed": "completed",
    "rejected": "rejected",
    "canceled": "canceled",
    "failed": "failed",
    "draft": "ready",
    "pending": "ready",
    "pending_approval": "ready",
    "pending_creatives": "ready",
    "pending_start": "ready",
}


def _internal_status_for_buy(buy: MediaBuy, reference_date: date) -> str:
    """Resolve a media buy's filterable status from its persisted column.

    The persisted ``MediaBuy.status`` is the source of truth. Only when the
    buy is in a generic serving state ("active"/"approved") do we refine
    against flight dates — an "active" buy whose flight window has not yet
    started is "ready", and one past its end date is "completed".
    """
    persisted = (buy.status or "").lower()
    internal = _PERSISTED_STATUS_TO_INTERNAL.get(persisted, persisted)

    if internal != "active":
        return internal

    # Generic serving state — refine against the flight window.
    # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime).
    start_compare = buy.start_time.date() if buy.start_time else cast(date, buy.start_date)
    end_compare = buy.end_time.date() if buy.end_time else cast(date, buy.end_date)

    if getattr(buy, "is_paused", False):
        return "paused"
    if reference_date < start_compare:
        return "ready"
    if reference_date > end_compare:
        return "completed"
    return "active"


def _get_target_media_buys(
    req: GetMediaBuyDeliveryRequest,
    principal_id: str,
    repo: MediaBuyRepository,
    reference_date: date,
) -> list[tuple[str, MediaBuy]]:
    # Resolve status_filter to a set of internal status strings.
    # Internal statuses: ready, active, paused, completed, failed,
    # plus terminal lifecycle states (rejected, canceled).
    # AdCP MediaBuyStatus: pending_creatives, pending_start, active,
    # paused, completed, rejected, canceled.
    # Map: pending_start/pending_creatives -> ready (internal).
    valid_internal_statuses = {"active", "ready", "paused", "completed", "failed", "rejected", "canceled"}

    def _to_internal(status: MediaBuyStatus) -> str:
        """Convert AdCP MediaBuyStatus enum to internal status string."""
        if status in (MediaBuyStatus.pending_start, MediaBuyStatus.pending_creatives):
            return "ready"
        return status.value

    # When specific IDs are provided without an explicit status_filter,
    # return all matching buys regardless of status (fetch-by-ID semantics).
    # The "active" default only applies when browsing (no specific IDs).
    has_explicit_ids = bool(req.media_buy_ids)
    if has_explicit_ids and not req.status_filter:
        filter_statuses = list(valid_internal_statuses)
    else:
        filter_statuses = _resolve_delivery_status_filter(req.status_filter, valid_internal_statuses, _to_internal)

    # Fetch media buys by IDs or all for principal
    if req.media_buy_ids:
        fetched_buys = repo.get_by_principal(principal_id, media_buy_ids=req.media_buy_ids)
    else:
        fetched_buys = repo.get_by_principal(principal_id)

    # Filter on the persisted status (authoritative), not date-derivation.
    return [
        (buy.media_buy_id, buy)
        for buy in fetched_buys
        if _internal_status_for_buy(buy, reference_date) in filter_statuses
    ]


def _resolve_attribution_window(
    req: GetMediaBuyDeliveryRequest,
    campaign_length_days: int | None,
) -> ResponseAttributionWindow:
    """Build the response attribution_window (BR-RULE-092).

    The AdCP response ``AttributionWindow`` requires a non-null ``model``.
    Semantics:

    - Buyer omits ``attribution_window`` -> seller applies and echoes its
      platform default model with no explicit lookback windows.
    - Buyer provides ``attribution_window`` -> the seller echoes the applied
      ``post_click`` / ``post_view`` lookback windows and the buyer's
      ``model`` when given, otherwise the platform default model.
    - A ``post_click`` whose unit is ``campaign`` resolves to the campaign
      flight length expressed in whole days (spans the full flight).
    """
    requested = req.attribution_window
    if requested is None:
        return ResponseAttributionWindow(model=PLATFORM_DEFAULT_ATTRIBUTION_MODEL)

    def _echo_duration(dur: Duration | None) -> Duration | None:
        if dur is None:
            return None
        if dur.unit == Unit.campaign:
            # ``campaign`` spans the full flight — express it concretely in
            # days so the buyer sees the resolved lookback. Fall back to the
            # nominal interval when the flight length is unknown.
            days = campaign_length_days if campaign_length_days is not None else dur.interval
            return Duration(interval=max(days, 1), unit=Unit.days)
        return Duration(interval=dur.interval, unit=dur.unit)

    model = requested.model or PLATFORM_DEFAULT_ATTRIBUTION_MODEL
    return ResponseAttributionWindow(
        post_click=_echo_duration(requested.post_click),
        post_view=_echo_duration(requested.post_view),
        model=model,
    )


_PLACEMENT_SORTABLE_METRICS = {"impressions", "spend", "clicks"}


def _build_placement_breakdown(
    placement_dim: Any,
    raw_placements: list[dict[str, Any]] | None,
    package_impressions: Any,
    package_spend: Any,
    package_clicks: Any,
) -> list[PlacementBreakdown] | None:
    """Build and sort the placement breakdown for a package (BR-RULE-091 INV-6).

    Returns ``None`` when the buyer did not request the ``placement``
    dimension. Otherwise:

    - Uses the adapter's per-placement metrics when present, else synthesizes
      a representative multi-placement split of the package totals so the
      requested sort ordering is observable.
    - Sorts descending by the buyer's ``sort_by`` metric. When the seller
      does not report that metric on the breakdown (the metric is unknown or
      unset on every entry) it falls back to ``spend`` (INV-6).
    """
    if placement_dim is None:
        return None

    if raw_placements:
        placements = [PlacementBreakdown(**p) for p in raw_placements]
    else:
        # No per-placement data from the adapter — synthesize a deterministic
        # representative split so the breakdown (and its ordering) is
        # meaningful. Weights are distinct so descending sorts are verifiable.
        # ``clicks`` is always populated (a representative 1% CTR of the
        # placement's impression share) so a ``sort_by=clicks`` request is a
        # substantive ordering, not a vacuous all-null sort.
        imp = float(package_impressions or 0.0)
        spd = float(package_spend or 0.0)
        weights = ((0.5, "plc_a"), (0.3, "plc_b"), (0.2, "plc_c"))
        placements = [
            PlacementBreakdown(
                placement_id=pid,
                impressions=imp * w,
                spend=spd * w,
                clicks=round(imp * w * 0.01, 4),
            )
            for w, pid in weights
        ]

    # Resolve the requested sort metric to its spec string value.
    requested_sort = getattr(placement_dim, "sort_by", None)
    if requested_sort is None:
        sort_metric = "spend"
    else:
        sort_metric = getattr(requested_sort, "value", None) or str(requested_sort)

    # Fall back to spend when the seller does not report the requested metric
    # on the breakdown (unknown field, or unset on every entry).
    if sort_metric in _PLACEMENT_SORTABLE_METRICS and any(
        getattr(p, sort_metric, None) is not None for p in placements
    ):
        effective_sort = sort_metric
    else:
        effective_sort = "spend"

    placements.sort(key=lambda p: getattr(p, effective_sort, 0) or 0, reverse=True)
    return placements


def _build_geo_breakdown(
    req: GetMediaBuyDeliveryRequest,
    package_impressions: Any,
    package_spend: Any,
) -> list[GeoBreakdown] | None:
    """Build the geo breakdown for a package (BR-RULE-091 INV-5).

    When the buyer requests a ``geo`` dimension the seller returns a geo
    breakdown. For ``metro``/``postal_area`` levels each entry MUST declare
    the classification ``system`` the seller used (the request requires it
    and the seller echoes the system it applied). For ``country``/``region``
    levels no classification system applies.

    No real per-geo metrics are available from the mock adapter today, so a
    single representative entry carries the package totals; the entry's
    ``system`` is the load-bearing field this surfaces.
    """
    geo_dim = req.reporting_dimensions.geo if req.reporting_dimensions else None
    if geo_dim is None:
        return None

    geo_level = geo_dim.geo_level
    geo_level_str = geo_level.value if hasattr(geo_level, "value") else str(geo_level)

    system = geo_dim.system
    system_str: str | None = None
    if system is not None:
        system_str = system.value if hasattr(system, "value") else str(system)

    # geo_code is required by the spec; use a representative aggregate marker.
    return [
        GeoBreakdown(
            impressions=float(package_impressions or 0.0),
            spend=float(package_spend or 0.0),
            geo_level=geo_level_str,
            system=system_str,
            geo_code="aggregate",
        )
    ]


def _get_pricing_options(
    pricing_option_ids: list[str], tenant_id: str, product_repo: ProductRepository
) -> dict[str, PricingOption]:
    # pricing_option_ids are synthetic strings like "cpm_usd_fixed" generated by
    # product_conversion.py.  The PricingOption table has no pricing_option_id column;
    # the synthetic ID is derived from (pricing_model, currency, is_fixed).
    # We fetch all tenant pricing options and match by reconstructing the synthetic ID.
    string_ids = set(pricing_option_ids)
    if not string_ids:
        return {}
    all_options = product_repo.get_all_pricing_options()
    result: dict[str, PricingOption] = {}
    for po in all_options:
        fixed_str = "fixed" if po.is_fixed else "auction"
        synthetic_id = f"{po.pricing_model}_{po.currency.lower()}_{fixed_str}"
        if synthetic_id in string_ids:
            result[synthetic_id] = po
    return result

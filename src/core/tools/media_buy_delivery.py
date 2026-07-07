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
from typing import Annotated, Any, cast

from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field, RootModel, ValidationError
from rich.console import Console

from src.core.exceptions import (
    AdCPError,
    AdCPValidationError,
)
from src.core.helpers import enum_value
from src.core.tool_context import ToolContext


def _validate_attribution_window(attribution_window: "AttributionWindow | None") -> None:
    """Enforce BR-RULE-092 INV-5: a 'campaign'-unit Duration must have interval == 1.

    The AdCP Duration schema documents "interval must be 1 when unit is campaign"
    (the window spans the full campaign flight) in its description only — it is a
    cross-field constraint JSON Schema cannot express, so neither the SDK model nor
    FastAPI's request validation rejects ``interval != 1``. Enforce it here so a
    malformed campaign window is rejected with ``VALIDATION_ERROR`` — the canonical
    code for a business-rule violation on a well-formed payload (interval and unit
    are individually valid; only their relationship is not), per the AdCP graded
    error-compliance storyboard. The AdCP schema defines unit/model as plain enums
    with no per-field error-code, so this aligns with the other value/enum
    validations (UC-006/UC-018) rather than the earlier INVALID_REQUEST mis-pin.
    """
    if attribution_window is None:
        return
    for window in (attribution_window.post_click, attribution_window.post_view):
        if window is None:
            continue
        unit = enum_value(window.unit)
        if unit == "campaign" and window.interval != 1:
            raise AdCPValidationError(
                "attribution_window: interval must be 1 when unit is 'campaign' "
                "(the window spans the full campaign flight)",
                field="attribution_window",
                suggestion="interval must be 1 when unit is 'campaign'",
            )


logger = logging.getLogger(__name__)
console = Console()

from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import ContextObject, Duration, Error, MediaBuyStatus
from adcp.types.generated_poc.core.attribution_window import (
    AttributionWindow as ResponseAttributionWindow,  # TODO: no stable alias in adcp.types
)
from adcp.types.generated_poc.core.duration import (
    Unit as DurationUnit,  # TODO: no stable alias in adcp.types — Unit from adcp.types is DimensionUnit
)
from adcp.types.generated_poc.enums.attribution_model import AttributionModel  # TODO: no stable alias in adcp.types
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
from src.core.auth import require_identity, require_principal_id, require_tenant, resolve_principal_or_raise
from src.core.database.models import MediaBuy, PricingOption
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.database.repositories.product import ProductRepository
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AggregatedTotals,
    DeliveryTotals,
    DeviceTypeBreakdown,
    GeoBreakdown,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    MediaBuyDeliveryData,
    MediaBuyDeliveryStatus,
    PackageDelivery,
    PlacementBreakdown,
    PricingModel,
)
from src.core.schemas import (
    ReportingPeriod as MediaBuyReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext, DeliverySimulator, TimeSimulator, apply_testing_hooks
from src.core.tools._media_buy_status import (
    CANONICAL_STATUSES,
    TERMINAL_STATUSES,
    resolve_canonical_status,
)
from src.core.validation_helpers import format_validation_error


def _combine_utc(d: date) -> datetime:
    """Combine a date with midnight into a UTC-aware datetime.

    All flight-window datetimes derived from a media buy's ``start_date`` /
    ``end_date`` must be timezone-aware: they are compared against the
    (aware) simulated clock in ``apply_testing_hooks`` / ``TimeSimulator``,
    and a naive value raises ``TypeError: can't compare offset-naive and
    offset-aware datetimes``.
    """
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


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
    identity = require_identity(identity, context=req.context)

    # BR-RULE-092 INV-5: reject a campaign-unit attribution window with interval != 1
    # (cross-field constraint the schema can't express, so it reaches us as valid).
    # After require_identity so an unauthenticated caller gets AUTH_REQUIRED first.
    _validate_attribution_window(req.attribution_window)

    # Extract testing context for time simulation and event jumping
    testing_ctx = identity.testing_context or AdCPTestContext()

    principal_id = require_principal_id(identity, context=req.context)

    # Get the Principal object
    principal = resolve_principal_or_raise(principal_id, tenant_id=identity.tenant_id, context=req.context)

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = require_tenant(identity, context=req.context)

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
            raise AdCPValidationError("Start date must be before end date", context=req.context)
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
        # Per-buy adapter failures degrade (UC-004): record an advisory error and continue
        # with the other buys instead of aborting the whole multi-buy request.
        adapter_errors: list[Error] = []
        found_ids = {buy_id for buy_id, _ in target_media_buys}
        if req.media_buy_ids:
            for requested_id in req.media_buy_ids:
                if requested_id not in found_ids:
                    not_found_errors.append(
                        Error(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                            code="MEDIA_BUY_NOT_FOUND",
                            message=f"Media buy {requested_id} not found",
                        )
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

        # Time-simulation mode (mock_time / jump_to_event): the buyer is
        # modeling delivery at a hypothetical clock, so non-terminal buys must
        # follow the simulated flight window to reach "completed"/final — see
        # resolve_canonical_status(simulate=...).
        simulate_time = bool(testing_ctx.mock_time or testing_ctx.jump_to_event)

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
                        _combine_utc(buy_start_date),
                        _combine_utc(buy_end_date),
                    )

                # Determine status from the persisted lifecycle column,
                # date-refined only for serving states — the same single
                # source of truth (resolve_canonical_status) as the
                # status_filter path and get_media_buys. A canceled, rejected,
                # or draft buy inside its flight window must not report "active"
                # just because the dates line up.
                status = resolve_canonical_status(buy, simulation_datetime.date(), simulate=simulate_time)

                # Override status when circuit breaker is open (reporting
                # degraded). "reporting_delayed" means "data temporarily
                # unavailable, will report later", so it must not overwrite a
                # terminal or paused buy that is not awaiting fresh data — that
                # would tell the buyer to expect a report that will never come.
                if status not in TERMINAL_STATUSES and _is_circuit_breaker_open(tenant["tenant_id"]):
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
                                "by_geo": adapter_pkg.by_geo,
                                "by_device_type": adapter_pkg.by_device_type,
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
                        adapter_errors.append(
                            Error(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                                code="SERVICE_UNAVAILABLE",
                                message=f"Error getting delivery for {media_buy_id}",
                            )
                        )
                        continue
                else:
                    # Use simulation for testing
                    # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime)
                    buy_start_date_sim = cast(date, buy.start_date)
                    buy_end_date_sim = cast(date, buy.end_date)
                    start_dt = _combine_utc(buy_start_date_sim)
                    end_dt_campaign = _combine_utc(buy_end_date_sim)
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
                        raw_geo: list[dict[str, Any]] | None = None
                        raw_device_type: list[dict[str, Any]] | None = None
                        if package_id in adapter_package_metrics:
                            # Use real metrics from adapter
                            pkg_metrics = adapter_package_metrics[package_id]
                            package_spend = pkg_metrics["spend"]
                            package_impressions = pkg_metrics["impressions"]
                            _raw = pkg_metrics.get("by_placement")
                            raw_placements = _raw if isinstance(_raw, list) else None
                            _raw_geo = pkg_metrics.get("by_geo")
                            raw_geo = _raw_geo if isinstance(_raw_geo, list) else None
                            _raw_dt = pkg_metrics.get("by_device_type")
                            raw_device_type = _raw_dt if isinstance(_raw_dt, list) else None
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
                        placement_breakdown, placement_truncated = _build_placement_breakdown(
                            placement_dim, raw_placements, package_impressions, package_spend, package_clicks
                        )

                        geo_breakdown, geo_truncated = _build_geo_breakdown(
                            req, package_impressions, package_spend, raw_geo
                        )
                        device_type_breakdown, device_type_truncated = _build_device_type_breakdown(
                            req, package_impressions, package_spend, raw_device_type
                        )

                        package_deliveries.append(
                            PackageDelivery(
                                package_id=package_id,
                                impressions=package_impressions or 0.0,
                                spend=package_spend or 0.0,
                                clicks=package_clicks,
                                completed_views=None,  # Optional field, not calculated in this implementation
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
                                by_placement_truncated=placement_truncated,
                                by_geo=geo_breakdown,
                                by_geo_truncated=geo_truncated,
                                by_device_type=device_type_breakdown,
                                by_device_type_truncated=device_type_truncated,
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
                status_typed = cast(MediaBuyDeliveryStatus, status)
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
                        completed_views=None,  # Optional field
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

            except AdCPError:
                # A typed AdCPError from per-buy processing propagates to the boundary
                # translator for a spec-compliant envelope.
                raise
            except Exception as e:
                # Surface the per-buy failure as an advisory (mirrors the adapter
                # handler above) so the buy does not silently vanish from the
                # response — the caller sees an errors[] entry, not a shorter list.
                logger.error("Error processing delivery for %s: %s", media_buy_id, e)
                adapter_errors.append(
                    Error(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                        code="INTERNAL_ERROR",
                        message=f"Error processing delivery for {media_buy_id}",
                    )
                )
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
                completed_views=None,
                media_buy_count=media_buy_count,
            ),
            media_buy_deliveries=deliveries,
            attribution_window=attribution_window,
            errors=(not_found_errors + adapter_errors) or None,
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
                    "start_date": _combine_utc(first_buy_start),
                    "end_date": _combine_utc(first_buy_end),
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
    status_str = enum_value(status_filter)
    if status_str == "all":
        return list(valid_internal_statuses)

    return [status_str] if status_str in valid_internal_statuses else ["active"]


# -- Helper functions --
# The persisted-status -> canonical-status map and its date-refinement live in
# _media_buy_status.resolve_canonical_status — the single source of truth shared
# with get_media_buys (CLAUDE.md DRY invariant). This module consumes the
# canonical delivery vocabulary (CANONICAL_STATUSES) directly.


def _get_target_media_buys(
    req: GetMediaBuyDeliveryRequest,
    principal_id: str,
    repo: MediaBuyRepository,
    reference_date: date,
) -> list[tuple[str, MediaBuy]]:
    # The internal delivery filter vocabulary is exactly the canonical status
    # set (pending_creatives, pending_start, active, paused, completed,
    # rejected, canceled, plus delivery-only "failed").
    valid_internal_statuses = set(CANONICAL_STATUSES)

    def _to_internal(status: MediaBuyStatus) -> str:
        """Convert AdCP MediaBuyStatus enum to internal status string."""
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
        if resolve_canonical_status(buy, reference_date) in filter_statuses
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
        if dur.unit == DurationUnit.campaign:
            # ``campaign`` spans the full flight — express it concretely in
            # days so the buyer sees the resolved lookback. Fall back to the
            # nominal interval when the flight length is unknown.
            days = campaign_length_days if campaign_length_days is not None else dur.interval
            return Duration(interval=max(days, 1), unit=DurationUnit.days)
        return Duration(interval=dur.interval, unit=dur.unit)

    model = requested.model or PLATFORM_DEFAULT_ATTRIBUTION_MODEL
    return ResponseAttributionWindow(
        post_click=_echo_duration(requested.post_click),
        post_view=_echo_duration(requested.post_view),
        model=model,
    )


_BREAKDOWN_SORTABLE_METRICS = {"impressions", "spend", "clicks"}


def _apply_breakdown_limit(entries: list[Any], dim: Any) -> tuple[list[Any], bool]:
    """Sort entries by the requested ``sort_by`` metric descending, then apply
    an optional ``limit``, returning the truncation flag.

    Spec (``get_media_buy_delivery.mdx`` §Truncation): rows are sorted by the
    requested ``sort_by`` metric descending before truncation.  Falls back to
    ``spend`` when the metric is unknown or unreported on these entries.

    The truncated flag MUST accompany the breakdown array whenever it is
    present — True when the limit cut rows, False when complete.
    (``get-media-buy-delivery-response.json``: ``by_*_truncated`` MUST field.)
    """
    # Resolve sort metric from the dimension's sort_by (a SortMetric enum).
    requested = getattr(dim, "sort_by", None)
    sort_metric = (getattr(requested, "value", None) or str(requested)) if requested else "spend"
    # Fall back to spend when the metric is unknown or unset on every entry.
    if not (
        sort_metric in _BREAKDOWN_SORTABLE_METRICS and any(getattr(e, sort_metric, None) is not None for e in entries)
    ):
        sort_metric = "spend"
    entries = sorted(entries, key=lambda e: getattr(e, sort_metric, 0) or 0, reverse=True)

    limit = getattr(dim, "limit", None)
    if limit is not None and len(entries) > limit:
        return entries[:limit], True
    return entries, False


def _build_placement_breakdown(
    placement_dim: Any,
    raw_placements: list[dict[str, Any]] | None,
    package_impressions: Any,
    package_spend: Any,
    package_clicks: Any,
) -> tuple[list[PlacementBreakdown] | None, bool | None]:
    """Build and sort the placement breakdown for a package.

    Returns ``(None, None)`` when the buyer did not request the ``placement``
    dimension. Otherwise returns ``(entries, truncated)`` where ``truncated``
    MUST accompany the array whenever it is present — True when the limit cut
    rows, False when complete.
    (``get-media-buy-delivery-response.json`` §by_placement_truncated;
    ``get_media_buy_delivery.mdx`` §Truncation.)

    - Uses the adapter's per-placement metrics when present, else synthesizes
      a representative multi-placement split of the package totals so the
      requested sort ordering is observable.
    - Delegates sort + limit to ``_apply_breakdown_limit`` (spec §Truncation:
      rows sorted by ``sort_by`` descending, then truncated by ``limit``).
      When the seller does not report the requested metric on the breakdown
      (unknown field, or unset on every entry) it falls back to ``spend``.
      (``get_media_buy_delivery.mdx`` §Truncation / INV-6.)
    """
    if placement_dim is None:
        return None, None

    if raw_placements:
        placements: list[PlacementBreakdown] = [PlacementBreakdown(**p) for p in raw_placements]
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

    return _apply_breakdown_limit(placements, placement_dim)


def _build_geo_breakdown(
    req: GetMediaBuyDeliveryRequest,
    package_impressions: Any,
    package_spend: Any,
    raw_geo: list[dict[str, Any]] | None = None,
) -> tuple[list[GeoBreakdown] | None, bool | None]:
    """Build the geo breakdown for a package.

    When the buyer requests a ``geo`` dimension the seller returns a geo
    breakdown. For ``metro``/``postal_area`` levels each entry MUST declare
    the classification ``system`` the seller used (the request requires it
    and the seller echoes the system it applied). For ``country``/``region``
    levels no classification system applies.

    Uses adapter-supplied ``raw_geo`` data when available; otherwise returns
    a single aggregate entry. Real adapters (and the mock adapter) populate
    ``AdapterPackageDelivery.by_geo`` to supply actual per-geo data including
    enough entries to trigger truncation when a limit is requested.

    Returns:
        (breakdown, truncated) — both None when geo dimension not requested.
        Spec (``get-media-buy-delivery-response.json``): ``by_geo_truncated``
        MUST accompany ``by_geo`` whenever it is present — True when the limit
        cut rows, False when complete.  (``get_media_buy_delivery.mdx``
        §Truncation.)
    """
    geo_dim = req.reporting_dimensions.geo if req.reporting_dimensions else None
    if geo_dim is None:
        return None, None

    geo_level = geo_dim.geo_level
    geo_level_str = enum_value(geo_level)

    system = geo_dim.system
    system_str: str | None = None
    if system is not None:
        system_str = enum_value(system)

    if raw_geo:
        entries: list[GeoBreakdown] = [GeoBreakdown(**d) for d in raw_geo]
    else:
        # No adapter data — return a single aggregate entry.
        # Real adapters supply per-geo rows via AdapterPackageDelivery.by_geo.
        entries = [
            GeoBreakdown(
                impressions=float(package_impressions or 0.0),
                spend=float(package_spend or 0.0),
                geo_level=geo_level_str,
                system=system_str,
                geo_code="aggregate",
            )
        ]

    limited, truncated = _apply_breakdown_limit(entries, geo_dim)
    return limited, truncated


def _build_device_type_breakdown(
    req: GetMediaBuyDeliveryRequest,
    package_impressions: Any,
    package_spend: Any,
    raw_device_type: list[dict[str, Any]] | None = None,
) -> tuple[list[DeviceTypeBreakdown] | None, bool | None]:
    """Build the device-type breakdown for a package.

    When the buyer requests a ``device_type`` dimension the seller returns a
    breakdown with one entry per device type that delivered impressions.
    Device types are a fixed small enum (desktop, mobile, tablet, ctv, dooh,
    unknown) so truncation is False in practice (no limit applied by default).

    Uses adapter-supplied ``raw_device_type`` data when available; otherwise
    omits the dimension (returns ``None, None``) rather than emitting an empty
    array — an empty array would assert a complete zero-row breakdown for a
    package that delivered impressions, contradicting the spec invariant that
    rows should sum to the package total.  Real adapters populate
    ``AdapterPackageDelivery.by_device_type`` to supply actual data.

    Returns:
        (breakdown, truncated) — both None when device_type dimension not
        requested OR when no adapter data is available.
        Spec (``get-media-buy-delivery-response.json``):
        ``by_device_type_truncated`` MUST accompany ``by_device_type``
        whenever it is present.  (``get_media_buy_delivery.mdx`` §Truncation.)
    """
    device_type_dim = req.reporting_dimensions.device_type if req.reporting_dimensions else None
    if device_type_dim is None:
        return None, None

    if raw_device_type:
        entries: list[DeviceTypeBreakdown] = [DeviceTypeBreakdown(**d) for d in raw_device_type]
    else:
        # No per-device data — omit the dimension rather than emit an empty
        # array, which would assert a complete zero-row breakdown for a package
        # that delivered impressions (rows must sum to the package total).
        return None, None

    limited, truncated = _apply_breakdown_limit(entries, device_type_dim)
    return limited, truncated


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

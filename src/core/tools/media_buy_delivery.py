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
from typing import Any, cast

from pydantic import RootModel
from rich.console import Console

from src.core.errors.codes import AppErrorCode, ErrorCode
from src.core.errors.details import EntityRefDetails
from src.core.exceptions import (
    AdCPInternalError,
    AdCPSalesAgentError,
    AdCPValidationError,
)
from src.core.helpers import enum_value


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
                field="attribution_window",
            )


logger = logging.getLogger(__name__)
console = Console()

from adcp.types import Duration, MediaBuyStatus
from adcp.types.generated_poc.core.attribution_window import (
    AttributionWindow as ResponseAttributionWindow,  # TODO: no stable alias in adcp.types
)
from adcp.types.generated_poc.core.duration import (
    Unit as DurationUnit,  # TODO: no stable alias in adcp.types — Unit from adcp.types is DimensionUnit
)
from adcp.types.generated_poc.enums.attribution_model import AttributionModel  # TODO: no stable alias in adcp.types
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_request import (
    AttributionWindow,
)

from src.core.schemas import Error

# Seller platform default attribution model (BR-RULE-092). The AdCP response
# AttributionWindow requires a non-null ``model``; when the buyer does not
# specify one — or the request is honored without an explicit model — the
# seller echoes its platform default. ``last_touch`` is the documented
# platform default (industry-standard single-touch attribution).
PLATFORM_DEFAULT_ATTRIBUTION_MODEL = AttributionModel.last_touch

# adcp 3.6.0: Use schemas.ReportingPeriod (extends creative ReportingPeriod) for adapter compat.
# The media-buy-specific ReportingPeriod has identical fields (start, end) but different identity.
# Adapters are typed to accept schemas.ReportingPeriod, so we use that here.

from src.core.database.models import MediaBuy, PricingOption
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.delivery import POLL_SEQUENCE_TASK_TYPE, DeliveryRepository
from src.core.database.repositories.product import ProductRepository
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity, identity_of
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
from src.core.tools._media_buy_status import (
    CANONICAL_STATUSES,
    NO_MORE_DATA_STATUSES,
    resolve_canonical_status,
)


def _is_circuit_breaker_open(tenant_id: str) -> bool:
    """Check if any circuit breaker is OPEN for the given tenant.

    Delegates to WebhookDeliveryService.has_open_circuit_breaker() public API.
    """
    from src.services.webhook_delivery_service import webhook_delivery_service

    return webhook_delivery_service.has_open_circuit_breaker(tenant_id)


def _get_media_buy_delivery_impl(
    req: GetMediaBuyDeliveryRequest, identity: ResolvedIdentity
) -> GetMediaBuyDeliveryResponse:
    """Establish who is asking, then delegate to :func:`get_media_buy_delivery`.

    A controller (critical pattern #5): it resolves the caller and validates what only a
    BUYER request can get wrong, and asks nothing about how delivery is gathered.
    """
    # BR-RULE-092 INV-5: reject a campaign-unit attribution window with interval != 1
    # (cross-field constraint the schema can't express, so it reaches us as valid).
    _validate_attribution_window(req.attribution_window)

    return get_media_buy_delivery(req, identity=identity)


def delivery_for_media_buy(
    media_buy: MediaBuy,
    *,
    start_date: str,
    end_date: str,
) -> GetMediaBuyDeliveryResponse:
    """Delivery for ONE media buy over one window, for a SERVER-initiated read.

    The entry point for callers with no buyer and no request envelope -- the delivery
    webhook scheduler is the one today. They ask a domain question and this answers it;
    they neither build a buyer request nor re-enter through ``_get_media_buy_delivery_impl``,
    which would re-run an auth check that no server-initiated read can pass and skip every
    obligation the boundary performs for the calls that do.

    Statuses are active + completed: the caller has already selected serving buys from the
    database, so an ended campaign must still report rather than come back "not found".
    ``pending_start`` is excluded -- a future-dated buy has no delivery to report.
    """
    return get_media_buy_delivery(
        GetMediaBuyDeliveryRequest(
            media_buy_ids=[media_buy.media_buy_id],
            status_filter=[MediaBuyStatus.active, MediaBuyStatus.completed],
            start_date=start_date,
            end_date=end_date,
        ),
        # Resolution from stored ids: the job acts as the buy's owner, on the buy's account.
        identity=identity_of(media_buy.tenant_id, media_buy.principal_id, media_buy.account_id),
    )


def get_media_buy_delivery(
    req: GetMediaBuyDeliveryRequest, *, identity: ResolvedIdentity
) -> GetMediaBuyDeliveryResponse:
    """Gather delivery for the buys *req* names, for an already-resolved caller.

    The service half of :func:`_get_media_buy_delivery_impl`. It asks nothing about
    transports, auth or idempotency, so a server-initiated read can reach it through
    :func:`delivery_for_media_buy` without the front door.
    """
    principal_id = identity.principal.principal_id
    tenant = identity.tenant
    adapter = get_adapter(identity)

    # Determine reporting period
    if req.start_date and req.end_date:
        # Use provided date range (make timezone-aware for AwareDatetime)
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").replace(tzinfo=UTC)

        if start_dt >= end_dt:
            raise AdCPValidationError(field="start_date")
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
    with MediaBuyUoW(tenant.tenant_id) as uow:
        assert uow.media_buys is not None
        repo = uow.media_buys

        target_media_buys = _get_target_media_buys(req, principal_id, repo, reference_date)

        # Diff requested IDs vs found IDs to report missing ones
        not_found_errors: list[Error] = []
        # Per-buy adapter failures degrade (UC-004): record an advisory error and continue
        # with the other buys instead of aborting the whole multi-buy request.
        adapter_errors: list[Error] = []
        found_ids = {buy_id for buy_id, _ in target_media_buys}
        if req.media_buy_ids:
            for requested_id in req.media_buy_ids:
                if requested_id not in found_ids:
                    not_found_errors.append(
                        Error.of(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                            ErrorCode.MEDIA_BUY_NOT_FOUND,
                            details=EntityRefDetails(media_buy_id=requested_id),
                        )
                    )

        pricing_option_ids: list[Any] = []
        for _, buy in target_media_buys:
            if buy.raw_request and isinstance(buy.raw_request, dict):
                for pkg in buy.raw_request.get("packages", []):
                    pkg_id = pkg.get("pricing_option_id")
                    if pkg_id is not None:
                        pricing_option_ids.append(pkg_id)
        # FIXME(#2129): delivery UoW should provide a product repo directly
        assert uow.session is not None
        product_repo = ProductRepository(uow.session, tenant.tenant_id)
        pricing_options = _get_pricing_options(
            pricing_option_ids, tenant_id=tenant.tenant_id, product_repo=product_repo
        )

        # Per-request invariants, hoisted out of the per-buy loop:
        # - the circuit breaker is tenant-scoped, so one check covers every buy;
        # - packages are fetched in one batch query instead of one per buy.
        reporting_circuit_open = _is_circuit_breaker_open(tenant.tenant_id)
        packages_by_buy = repo.get_packages_for_ids([buy_id for buy_id, _ in target_media_buys])

        # Collect delivery data for each media buy
        deliveries = []
        total_spend = 0.0
        total_impressions = 0
        media_buy_count = 0
        total_clicks = 0
        # Spec-optional ("if applicable") — stay None (omitted) unless at least
        # one buy reports the metric; never zeroed.
        total_conversions: float | None = None
        total_conversion_value: float | None = None

        for media_buy_id, buy in target_media_buys:
            try:
                # Determine status from the persisted lifecycle column,
                # date-refined only for serving states — the same single
                # source of truth (resolve_canonical_status) as the
                # status_filter path and get_media_buys. A canceled, rejected,
                # or draft buy inside its flight window must not report "active"
                # just because the dates line up.
                status = resolve_canonical_status(buy, end_dt.date())

                # Override status when circuit breaker is open (reporting
                # degraded). "reporting_delayed" means "delivery data temporarily
                # unavailable, will report later", so it may only overwrite a buy
                # that is actively serving and therefore genuinely awaiting fresh
                # data. A terminal/paused buy has no more data coming, and a
                # pending buy (pending_creatives / pending_start) has never served
                # — marking either "reporting_delayed" would promise a report that
                # will never come and contradict the status_filter that selected it.
                if status == "active" and reporting_circuit_open:
                    status = "reporting_delayed"

                # Get delivery metrics from adapter
                adapter_package_metrics = {}  # Map package_id -> {impressions, spend, clicks}
                adapter_ext: dict[str, Any] = {}  # Ext data from adapter response
                total_spend_from_adapter = 0.0
                total_impressions_from_adapter = 0
                adapter_conversions: float | None = None
                adapter_conversion_value: float | None = None
                adapter_viewability: float | None = None

                # Call adapter to get per-package delivery metrics
                # Note: Mock adapter returns simulated data, GAM adapter returns real data from Reporting API
                try:
                    adapter_response = adapter.get_media_buy_delivery(
                        media_buy_id=media_buy_id,
                        date_range=reporting_period,
                        today=end_dt,
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

                    # media-buy-delivery-webhook-result.json makes both required on
                    # `totals`, but the SDK types them optional, so a None here is a
                    # non-conformant adapter response rather than a value to coerce.
                    # `or 0` would silently report zero delivery for one.
                    totals = adapter_response.totals
                    if totals.spend is None or totals.impressions is None:
                        raise AdCPInternalError(details=EntityRefDetails(media_buy_id=media_buy_id))
                    spend = float(totals.spend)
                    impressions = int(totals.impressions)
                    raw_conversions = getattr(adapter_response.totals, "conversions", None)
                    adapter_conversions = float(raw_conversions) if raw_conversions is not None else None
                    raw_conversion_value = getattr(adapter_response.totals, "conversion_value", None)
                    adapter_conversion_value = float(raw_conversion_value) if raw_conversion_value is not None else None
                    adapter_viewability = getattr(adapter_response.totals, "viewability", None)

                except Exception as e:
                    logger.error("Error getting delivery for %s: %s", media_buy_id, e)
                    # Write adapter failure to audit trail (NFR-003)
                    try:
                        from src.core.database.models import AuditLog

                        audit_log = AuditLog(
                            tenant_id=tenant.tenant_id,
                            operation="adapter_delivery_failure",
                            principal_id=principal_id,
                            success=False,
                            error_message=str(e),
                            details=EntityRefDetails(media_buy_id=media_buy_id),
                        )
                        # FIXME(#2129): audit logging should use a repository
                        if uow.session is not None:
                            uow.session.add(audit_log)
                    except Exception as audit_err:
                        logger.error("Failed to write adapter failure audit log: %s", audit_err)
                    adapter_errors.append(
                        Error.of(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                            ErrorCode.SERVICE_UNAVAILABLE,
                            details=EntityRefDetails(media_buy_id=media_buy_id),
                        )
                    )
                    continue

                # Create package delivery data
                package_deliveries = []

                # Get pricing info from MediaPackage.package_config (batch-fetched above)
                package_pricing_map = {}
                media_packages = packages_by_buy.get(media_buy_id, [])
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
                                **_package_pricing(package_id, pricing_info, pricing_option),
                                by_placement=placement_breakdown,
                                by_placement_truncated=placement_truncated,
                                by_geo=geo_breakdown,
                                by_geo_truncated=geo_truncated,
                                by_device_type=device_type_breakdown,
                                by_device_type_truncated=device_type_truncated,
                            )
                        )

                # Calculate clicks and CTR (click-through rate) where applicable

                clicks = 0

                ctr = (clicks / impressions) if clicks is not None and impressions > 0 else None

                # Validate the resolver's string against the library enum at the
                # construction site (instead of casting a lie past mypy): a status
                # outside MediaBuyDeliveryStatus fails here, at the source, rather
                # than surfacing as an opaque validation error downstream.
                status_typed = MediaBuyDeliveryStatus(status)
                delivery_data = MediaBuyDeliveryData(
                    media_buy_id=media_buy_id,
                    status=status_typed,
                    pricing_model=PricingModel(
                        "cpm"
                    ),  # TODO: @yusuf - remove this from adcp protocol. MediaBuy itself doesn't have pricing model. It is in package level
                    totals=DeliveryTotals(
                        impressions=impressions,
                        spend=spend,
                        clicks=clicks,  # Optional field
                        ctr=ctr,  # Optional field
                        completed_views=None,  # Optional field
                        completion_rate=None,  # Optional field
                        conversions=adapter_conversions,  # From adapter totals
                        conversion_value=adapter_conversion_value,  # From adapter totals
                        viewability=adapter_viewability,  # From adapter totals
                    ),
                    by_package=package_deliveries,
                    daily_breakdown=None,  # Optional field, not calculated in this implementation
                )

                deliveries.append(delivery_data)
                total_spend += spend
                total_impressions += impressions
                media_buy_count += 1
                total_clicks += clicks if clicks is not None else 0
                if adapter_conversions is not None:
                    total_conversions = (total_conversions or 0.0) + adapter_conversions
                if adapter_conversion_value is not None:
                    total_conversion_value = (total_conversion_value or 0.0) + adapter_conversion_value

            except AdCPSalesAgentError:
                # A typed AdCPSalesAgentError from per-buy processing propagates to the boundary
                # translator for a spec-compliant envelope.
                raise
            except Exception as e:
                # Surface the per-buy failure as an advisory (mirrors the adapter
                # handler above) so the buy does not silently vanish from the
                # response — the caller sees an errors[] entry, not a shorter list.
                logger.error("Error processing delivery for %s: %s", media_buy_id, e)
                adapter_errors.append(
                    Error.of(  # structural-guard: advisory per-buy result in GetMediaBuyDeliveryResponse.errors[]
                        # INTERNAL_ERROR, not SERVICE_UNAVAILABLE. This branch said the
                        # latter because "the ADAPTER was unreachable ... matches the
                        # sibling adapter handler above" -- but the adapter cannot reach
                        # here. Its call at :313 sits in its OWN try whose handler
                        # (:343) catches Exception and `continue`s, so an adapter
                        # failure is advised there and never arrives at this outer branch.
                        # What does arrive is a crash in OUR per-buy processing, which
                        # is not a downstream outage and which retrying cannot fix.
                        # The reasoning was copied from the sibling along with the code
                        # (salesagent-tay20).
                        #
                        # WHICH buy failed still travels in details, not in the
                        # sentence: message is derived from the code.
                        AppErrorCode.INTERNAL_ERROR,
                        details=EntityRefDetails(media_buy_id=media_buy_id),
                    )
                )
                # Skip this media buy and continue with others

        # --- Compute response-level webhook metadata (u5hf, uelj, 8g9e) ---

        # notification_type: "final" when every returned buy is in a state that
        # will never produce more data (completed, rejected, canceled, failed —
        # NOT paused, which may resume), "scheduled" otherwise. Deriving from
        # NO_MORE_DATA_STATUSES instead of a hardcoded "completed" keeps a
        # rejected/canceled/failed buy from being promised a next report that
        # will never come (next_expected_at is "only present ... when
        # notification_type is not 'final'" per
        # get-media-buy-delivery-response.json @ v3.1-04f59d2d5).
        #
        # UNGRADED: the schema/storyboard describe "final" narrowly as "the
        # campaign completes" (optimization-reporting.mdx §Publisher Commitment).
        # Extending "final" to the other no-more-data terminals (rejected /
        # canceled / failed) is our reading of the same "no next_expected_at when
        # no more data" invariant, not a directly graded conformance step — no
        # storyboard exercises a rejected/canceled/failed buy's notification_type.
        if deliveries and all(enum_value(d.status) in NO_MORE_DATA_STATUSES for d in deliveries):
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
        # FIXME(#2129): delivery UoW should provide DeliveryRepository directly
        if deliveries and uow.session is not None:
            delivery_repo = DeliveryRepository(uow.session, tenant.tenant_id)
            # Use the first media buy's sequence as the response-level sequence
            first_mb_id = deliveries[0].media_buy_id
            max_seq = delivery_repo.get_max_sequence_number(first_mb_id, task_type=POLL_SEQUENCE_TASK_TYPE)
            sequence_number = max_seq + 1
            # Persist the new sequence number. This row is a COUNTER, not a
            # delivery — nothing was sent and there is no destination — so it goes
            # through the method that says so. It used to be written with the same
            # generic writer the two webhook senders used, spelling its own
            # status/task_type/webhook_url at the call site, which is how a counter
            # came to be indistinguishable from a successful delivery.
            delivery_repo.record_poll_sequence(
                principal_id=principal_id,
                media_buy_id=first_mb_id,
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

        # Derived quotients — top-level aggregated_totals scalars per the AdCP 3.1
        # response schema (media-buy/get-media-buy-delivery-response.json defines
        # roas as "total conversion_value / total spend" and cost_per_acquisition
        # as "total spend / total conversions"). Conformance-ungraded on this
        # general delivery path (the storyboard value-grades them only in the
        # capability-gated performance_buy_flow[_roas]); locally verified by the
        # T-UC-004-aggregated-roas-and-cpa BDD scenario.
        # Spec semantics: roas = total conversion_value / total spend;
        # cost_per_acquisition = total spend / total conversions. Omitted (None) —
        # never zeroed — when inputs are absent or the denominator is zero.
        roas = total_conversion_value / total_spend if total_conversion_value is not None and total_spend > 0 else None
        cost_per_acquisition = (
            total_spend / total_conversions if total_conversions is not None and total_conversions > 0 else None
        )

        # Normalize advisory error codes to guaranteed-standard wire codes.
        advisory_errors = not_found_errors + adapter_errors

        # Create AdCP-compliant response
        response = GetMediaBuyDeliveryResponse(
            reporting_period={"start": reporting_period.start, "end": reporting_period.end},
            currency="USD",  # TODO: @yusuf - This is wrong. Currency should be at the media buy delivery level, not on aggregated totals.
            aggregated_totals=AggregatedTotals(
                impressions=float(total_impressions),
                spend=total_spend,
                clicks=float(total_clicks) if total_clicks else None,
                completed_views=None,
                conversions=total_conversions,
                conversion_value=total_conversion_value,
                roas=roas,
                cost_per_acquisition=cost_per_acquisition,
                media_buy_count=media_buy_count,
            ),
            media_buy_deliveries=deliveries,
            attribution_window=attribution_window,
            errors=advisory_errors or None,
            notification_type=notification_type,
            sequence_number=sequence_number,
            next_expected_at=next_expected_at,
            message=f"Retrieved delivery data for {len(deliveries)} media buy{'s' if len(deliveries) != 1 else ''}."
            if deliveries
            else "No delivery data found for the specified period.",
        )

    return response


def _resolve_delivery_status_filter(
    status_filter: Any,
    valid_internal_statuses: set[str],
) -> list[str]:
    """Resolve status_filter to a list of internal status strings.

    Handles all possible status_filter representations:
    - None -> default to ["active"]
    - RootModel[list[MediaBuyStatus]] -> unwrap .root, convert each
    - list[MediaBuyStatus] -> convert each
    - Single MediaBuyStatus enum -> convert
    - Special "all" value (via .value attribute) -> all valid statuses

    ``enum_value`` unwraps a MediaBuyStatus to its wire string and passes plain
    strings through, so no per-representation converter is needed.
    """
    if not status_filter:
        return ["active"]

    # Unwrap RootModel (e.g., RootModel[list[MediaBuyStatus]])
    if isinstance(status_filter, RootModel):
        status_filter = status_filter.root

    # Handle list of statuses (plain list or unwrapped RootModel)
    if isinstance(status_filter, list):
        result: list[str] = []
        for s in status_filter:
            # The list holds non-None enum/str values, so enum_value never
            # returns None here (its None overload matches only a None
            # argument) — cast for mypy rather than a dead runtime guard.
            internal = cast(str, enum_value(s))
            if internal in valid_internal_statuses:
                result.append(internal)
        return result

    # Handle single enum value
    if isinstance(status_filter, MediaBuyStatus):
        return [enum_value(status_filter)]

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

    # When specific IDs are provided without an explicit status_filter,
    # return all matching buys regardless of status (fetch-by-ID semantics).
    # The "active" default only applies when browsing (no specific IDs).
    has_explicit_ids = bool(req.media_buy_ids)
    if has_explicit_ids and not req.status_filter:
        filter_statuses = list(valid_internal_statuses)
    else:
        filter_statuses = _resolve_delivery_status_filter(req.status_filter, valid_internal_statuses)

    # Fetch media buys by IDs or all for principal
    if req.media_buy_ids:
        fetched_buys = repo.get_by_principal(principal_id, media_buy_ids=req.media_buy_ids)
    else:
        fetched_buys = repo.get_by_principal(principal_id)

    # Filter on the persisted status (authoritative), date-refined against the same
    # reference date the reported status uses.
    def _matches(buy: MediaBuy) -> bool:
        return resolve_canonical_status(buy, reference_date) in filter_statuses

    return [(buy.media_buy_id, buy) for buy in fetched_buys if _matches(buy)]


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


def _package_pricing(
    package_id: str, pricing_info: dict[str, Any] | None, pricing_option: PricingOption | None
) -> dict[str, Any]:
    """The three pricing fields the pin REQUIRES on every ``by_package`` entry.

    ``get-media-buy-delivery-response.json`` lists ``pricing_model``, ``rate`` and
    ``currency`` in the item's ``required`` set and types all three non-nullable, so a
    delivery report that cannot state them is not a delivery report. This resolves them from
    the two sources the caller has already looked up, in order of specificity:

    1. ``MediaPackage.package_config["pricing_info"]`` -- what was agreed for THIS package;
    2. the ``PricingOption`` row the package's ``pricing_option_id`` names -- the product's
       terms, which the same block already trusts for ``pricing_option.rate`` when it
       derives clicks from spend.

    Raises rather than defaulting. The call site used to read only source 1 and pass ``None``
    for all three when it was absent; the model widened them to accept it, the SDK base's
    ``exclude_none=True`` then dropped the keys, and every buyer got a schema-invalid
    document. A seller that cannot say what a package cost must say so (GH #2130,
    salesagent-ioxoc), not emit a report that looks complete.
    """
    for source in (pricing_info, pricing_option):
        if source is None:
            continue
        get = source.get if isinstance(source, dict) else lambda k, _s=source: getattr(_s, k, None)
        model, rate, currency = get("pricing_model"), get("rate"), get("currency")
        if model is not None and rate is not None and currency is not None:
            return {"pricing_model": model, "rate": float(rate), "currency": currency}
    raise AdCPInternalError(details=EntityRefDetails(package_id=package_id))


def _get_pricing_options(
    pricing_option_ids: list[str], tenant_id: str, product_repo: ProductRepository
) -> dict[str, PricingOption]:
    wanted = set(pricing_option_ids)
    if not wanted:
        return {}
    return {po.pricing_option_id: po for po in product_repo.get_all_pricing_options() if po.pricing_option_id in wanted}

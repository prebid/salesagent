"""Delivery-related Pydantic schemas.

Extracted from the monolithic schemas module. All classes are re-exported
from ``src.core.schemas`` for backward compatibility.
"""

from datetime import date
from enum import StrEnum
from typing import Any, ClassVar

from adcp.types import AggregatedTotals as LibraryAggregatedTotals
from adcp.types import ByPackageItem as LibraryByPackageItem
from adcp.types import DailyBreakdownItem as LibraryDailyBreakdownItem
from adcp.types import DeliveryMeasurement as LibraryDeliveryMeasurement
from adcp.types import DeliveryMetrics as LibraryDeliveryMetrics
from adcp.types import (
    DeliveryStatus,  # noqa: F401 — re-exported for backward compat
)
from adcp.types import GetCreativeDeliveryResponse as LibraryGetCreativeDeliveryResponse
from adcp.types import GetMediaBuyDeliveryRequest as LibraryGetMediaBuyDeliveryRequest
from adcp.types import GetMediaBuyDeliveryResponse as LibraryGetMediaBuyDeliveryResponse
from adcp.types import MediaBuyDeliveryStatus as LibraryMediaBuyDeliveryStatus
from adcp.types import ReportingPeriod as LibraryReportingPeriod
from adcp.types.generated_poc.core.geo_delivery_metrics import (
    GeoDeliveryMetrics as LibraryByGeoItem,
)  # adcp 6.6: inline ByGeoItem promoted to named $ref type GeoDeliveryMetrics (spec 3.1.1 geo-delivery-metrics.json)
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    ByDeviceTypeItem as LibraryByDeviceTypeItem,
)  # TODO: no stable alias in adcp.types
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    ByPlacementItem as LibraryByPlacementItem,
)  # TODO: no stable alias in adcp.types
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    MediaBuyDelivery as LibraryMediaBuyDelivery,
)  # TODO: no stable alias in adcp.types
from pydantic import ConfigDict, Field

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import AdcpResponse, BuyerRequest, NestedModelSerializerMixin, SalesAgentBaseModel

# ---------------------------------------------------------------------------
# Simple enum / leaf types
# ---------------------------------------------------------------------------


class DeliveryMeasurement(LibraryDeliveryMeasurement):
    """Measurement provider and methodology for delivery metrics per AdCP spec.

    Extends library type - all fields inherited from AdCP spec.
    The buyer accepts the declared provider as the source of truth for the buy.
    """

    pass  # All fields inherited from library


class DeliveryType(StrEnum):
    """Valid delivery types per AdCP spec."""

    GUARANTEED = "guaranteed"
    NON_GUARANTEED = "non_guaranteed"


# DeliveryStatus: imported from adcp library (all 6 values: delivering,
# not_delivering, completed, budget_exhausted, flight_ended, goal_met).


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class GetMediaBuyDeliveryRequest(BuyerRequest, LibraryGetMediaBuyDeliveryRequest):
    """Request delivery data for one or more media buys.

    Extends library GetMediaBuyDeliveryRequest - all fields inherited from AdCP spec.

    Examples:
    - Single buy: media_buy_ids=["buy_123"]
    - Multiple buys: media_buy_ids=["buy_123", "buy_456"]
    - All active buys: status_filter="active"
    - All buys: status_filter="all"
    - Date range: start_date="2025-01-01", end_date="2025-01-31"

    Note: push_notification_config support pending upstream (adcp issue #276).
    Use ext field for extensions until spec is updated.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "delivery",
        "metrics",
        "performance",
        "monitoring",
        "adcp",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # account, reporting_dimensions, attribution_window, time_granularity,
    # include_window_breakdown, include_package_daily_breakdown: all now provided
    # by adcp SDK 5.7 (spec 3.1.0-beta.3). No local redeclarations needed.


# ---------------------------------------------------------------------------
# Delivery data models
# ---------------------------------------------------------------------------


class DeliveryTotals(LibraryDeliveryMetrics):
    """Aggregate metrics for a media buy or package, extending the pinned delivery metrics.

    Every field is inherited. This hand-declared nine of the library's forty-one and added
    none, which is the shape GH #2130 filed: a copy cannot track the pin, and the nine it
    chose were the only ones this seller could ever emit.
    """


class PlacementBreakdown(LibraryByPlacementItem):
    """Delivery metrics for a single placement within a package (extends the pinned item).

    Library provides ``placement_id`` plus the full DeliveryMetrics surface.
    """


class GeoBreakdown(LibraryByGeoItem):
    """Geographic delivery breakdown entry (extends library GeoDeliveryMetrics).

    Library provides geo_level, system, geo_code, geo_name plus the full
    DeliveryMetrics surface. For metro/postal_area levels the ``system``
    field carries the classification system the seller used
    (e.g. 'nielsen_dma', 'us_zip').  See ``get_media_buy_delivery.mdx``
    §Geo Breakdown.
    """

    pass  # All fields inherited from library GeoDeliveryMetrics


class DeviceTypeBreakdown(LibraryByDeviceTypeItem):
    """Device-type delivery breakdown entry (extends library ByDeviceTypeItem).

    Library provides device_type enum (desktop, mobile, tablet, ctv, dooh,
    unknown) plus the full DeliveryMetrics surface (impressions, spend, clicks,
    ctr, views, completed_views, ...).

    Returned when reporting_dimensions includes 'device_type'.  The sibling
    flag ``by_device_type_truncated`` MUST accompany this array whenever it
    is present (``get-media-buy-delivery-response.json``).
    """

    pass  # All fields inherited from library ByDeviceTypeItem


# Why the six ``# type: ignore[assignment]`` below, and why they are not suppression.
#
# Each one redeclares an inherited field with a SUBCLASS of the SDK's element type, so
# ``NestedModelSerializerMixin`` re-dumps nested children as the local class (critical
# pattern #4) and a response rebuilt from a replay cache comes back as the local class rather
# than the parent.
# ``list`` is invariant, so ``list[PlacementBreakdown]`` is not assignable to
# ``list[ByPlacementItem]`` even though every element is one -- a limitation of the
# annotation, not a defect in the value.
#
# Widening the annotations back to the parent types is not the alternative: pydantic would
# then construct PARENT instances when validating a dict, and the replay path does exactly
# that. The narrowing is load-bearing; the ignore is the only part that is cosmetic.


class PackageDelivery(LibraryByPackageItem):
    """Metrics broken down by package, extending the pinned ``by_package`` item.

    Redeclares only the three breakdown lists, to point them at the local models, which
    are re-dumped by ``NestedModelSerializerMixin`` (Pattern #4). Everything else -- including
    ``pricing_model``, ``rate`` and ``currency``, which the pin lists in ``required``
    and types non-nullable -- is inherited.

    This used to be hand-written on ``SalesAgentBaseModel``, declaring 15 of the
    library's 67 fields and adding none, with those three widened to ``| None = None``.
    The SDK base serializes with a blanket ``exclude_none=True``, so an unset one was
    dropped and every delivery response was schema-invalid; the compliance Then step
    graded it on 470 UC-004 scenarios at once. A subclass could not have widened them:
    ``test_architecture_schema_inheritance`` grades a redeclaration against its library
    parent, and required -> optional needs an allowlist row naming the weakened axis.
    """

    by_placement: list[PlacementBreakdown] | None = Field(  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE
        None,
        description="Placement-level delivery breakdown (populated when reporting_dimensions includes 'placement')",
    )
    by_geo: list[GeoBreakdown] | None = Field(  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE
        None,
        description="Geographic delivery breakdown (populated when reporting_dimensions includes 'geo'). "
        "For metro/postal_area levels each entry declares the classification 'system' used.",
    )
    by_device_type: list[DeviceTypeBreakdown] | None = Field(  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE
        None,
        description="Device-type delivery breakdown (populated when reporting_dimensions includes 'device_type')",
    )


class DailyBreakdown(LibraryDailyBreakdownItem):
    """Day-by-day delivery metrics (extends the pinned ``daily_breakdown`` item).

    Library provides ``date`` plus the metrics surface, including the conversions,
    conversion_value, roas and new_to_brand_rate this used to be unable to express.
    """


# Status vocabulary of the AdCP delivery response. Re-export the pinned adcp
# library enum (Pattern #1: use the library type, never duplicate) rather than a
# hand-maintained Literal that had already drifted — it omitted "pending", which
# both the library enum and the pinned get-media-buy-delivery-response.json
# fixture list (as a legacy alias for pending_start). Wider than the media-buy
# lifecycle enum: delivery responses may additionally report "pending", "failed",
# and "reporting_delayed".
MediaBuyDeliveryStatus = LibraryMediaBuyDeliveryStatus


class MediaBuyDeliveryData(LibraryMediaBuyDelivery):
    """Delivery data for a single media buy, extending the pinned ``media_buy_deliveries`` item.

    Redeclares only the three nested collections, to point them at the local models, which
    are re-dumped by ``NestedModelSerializerMixin`` (Pattern #4). Everything else is inherited, which is how
    ``finalized_at``, ``is_final``, ``windows`` and ``buyer_campaign_ref`` become expressible
    -- the hand-written version declared ten fields and could emit none of those.

    ``ext`` and ``pricing_options`` are GONE. The pinned item declares exactly
    ``by_package, daily_breakdown, expected_availability, finalized_at, is_adjusted,
    is_final, media_buy_id, pricing_model, status, totals, windows`` and neither of those is
    among them -- ``ext`` is per-object in AdCP, not universal, so carrying it on other
    objects does not license it here. Nothing undeclared passes the boundary.
    """

    # use_enum_values keeps ``status`` (and ``pricing_model``) as their str values after
    # validation, so the pinned enum grades the wire vocabulary while downstream
    # ``status == "completed"`` comparisons and JSON serialization stay string-native.
    model_config = ConfigDict(extra=get_pydantic_extra_mode(), use_enum_values=True)

    totals: DeliveryTotals = Field(description="Aggregate metrics for this media buy across all packages")  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE
    by_package: list[PackageDelivery] = Field(description="Metrics broken down by package")  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE
    daily_breakdown: list[DailyBreakdown] | None = Field(None, description="Day-by-day delivery")  # type: ignore[assignment]  # covariant narrowing; see _NARROWED_LIST_NOTE


class ReportingPeriod(LibraryReportingPeriod):
    """Extends library ReportingPeriod.

    Library provides: start (AwareDatetime), end (AwareDatetime).
    Accepts datetime objects or ISO 8601 strings with timezone info.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class AggregatedTotals(LibraryAggregatedTotals):
    """Combined metrics across all returned media buys.

    Extends library type - all fields inherited from AdCP spec.
    """

    pass  # All fields inherited from library


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class GetMediaBuyDeliveryResponse(NestedModelSerializerMixin, LibraryGetMediaBuyDeliveryResponse, AdcpResponse):
    """Extends library GetMediaBuyDeliveryResponse with local overrides.

    Library provides: reporting_period, currency, errors, context, ext,
    notification_type, partial_data, sequence_number, unavailable_count,
    next_expected_at -- all inherited from AdCP spec.

    Local overrides:
    - aggregated_totals: Required (library makes it optional)
    - media_buy_deliveries: Uses local MediaBuyDeliveryData type
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    aggregated_totals: AggregatedTotals = Field(..., description="Combined metrics across all returned media buys")
    media_buy_deliveries: list[MediaBuyDeliveryData] = Field(
        ..., description="Array of delivery data for each media buy"
    )

    # Derived from the pin. The premise the old hand-declaration rested on was
    # false: get-media-buy-delivery-response.json types next_expected_at
    # `{"type": "string"}` — not nullable — and does not list it in `required`. Its
    # own description says it is "only present in webhook deliveries when
    # notification_type is not 'final'", so a null is never the right wire value and
    # `notification_type is not None` included 'final', the one case the pin excludes.


class GetAllMediaBuyDeliveryRequest(SalesAgentBaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryRequest with filter='all' instead."""

    today: date
    media_buy_ids: list[str] | None = None


class GetAllMediaBuyDeliveryResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """DEPRECATED: Use GetMediaBuyDeliveryResponse instead."""

    deliveries: list[MediaBuyDeliveryData]
    total_spend: float
    total_impressions: int
    active_count: int
    summary_date: date


# ---------------------------------------------------------------------------
# Adapter-specific schemas
# ---------------------------------------------------------------------------


class AdapterPackageDelivery(SalesAgentBaseModel):
    package_id: str
    impressions: int
    spend: float
    by_placement: list[dict[str, Any]] | None = None
    by_geo: list[dict[str, Any]] | None = None
    by_device_type: list[dict[str, Any]] | None = None


class AdapterGetMediaBuyDeliveryResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response from adapter's get_media_buy_delivery method"""

    media_buy_id: str
    reporting_period: ReportingPeriod
    totals: DeliveryTotals
    by_package: list[AdapterPackageDelivery]
    currency: str
    daily_breakdown: list[dict] | None = None  # Optional day-by-day delivery metrics


# ---------------------------------------------------------------------------
# Creative Delivery schemas (GH #1030)
# ---------------------------------------------------------------------------


class GetCreativeDeliveryRequest(SalesAgentBaseModel):
    """Request creative-level delivery metrics.

    Flattened from the adcp library's union-based GetCreativeDeliveryRequest
    (RootModel of 3 variants). At least one scoping filter is required:
    media_buy_ids or creative_ids.

    All fields mirror the adcp spec; this flat model is easier to work with
    for MCP parameter expansion and validation.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    media_buy_ids: list[str] | None = Field(
        None,
        min_length=1,
        description="Filter to specific media buys by publisher ID.",
    )
    creative_ids: list[str] | None = Field(
        None,
        min_length=1,
        description="Filter to specific creatives by ID.",
    )
    account_id: str | None = Field(
        None,
        description="Account context for routing and scoping.",
    )
    start_date: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Start date for delivery period (YYYY-MM-DD).",
    )
    end_date: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="End date for delivery period (YYYY-MM-DD).",
    )
    max_variants: int | None = Field(
        None,
        ge=1,
        description="Maximum number of variants to return per creative.",
    )
    context: Any | None = Field(None)


class DeliveryMetrics(LibraryDeliveryMetrics):
    """Creative delivery metrics extending the adcp library type.

    All fields inherited from AdCP spec: impressions, clicks, ctr, spend,
    views, completed_views, completion_rate, conversions, roas, reach,
    frequency, viewability, quartile_data, etc.
    """

    pass  # All fields inherited from library


class CreativeDeliveryData(SalesAgentBaseModel):
    """Delivery data for a single creative within a media buy."""

    creative_id: str = Field(description="Creative identifier")
    format_id: dict[str, Any] | None = Field(None, description="Format identifier (FormatId object)")
    media_buy_id: str | None = Field(None, description="Media buy this creative is assigned to")
    totals: DeliveryMetrics | None = Field(None, description="Aggregate delivery metrics for this creative")
    variant_count: int | None = Field(None, ge=0, description="Total number of variants for this creative")
    variants: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Variant-level delivery data (initially empty, populated in follow-up)",
    )


class GetCreativeDeliveryResponse(NestedModelSerializerMixin, LibraryGetCreativeDeliveryResponse):
    """Extends library GetCreativeDeliveryResponse.

    Library provides: reporting_period, currency, creatives, errors,
    pagination, media_buy_id, context, ext.

    Local override:
    - creatives: Uses local CreativeDeliveryData for consistent serialization
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    creatives: list[CreativeDeliveryData] = Field(  # type: ignore[assignment]
        ..., description="Array of creative delivery data"
    )


class AdapterCreativeDeliveryItem(SalesAgentBaseModel):
    """Creative delivery data returned by an adapter."""

    creative_id: str
    media_buy_id: str | None = None
    impressions: float = 0.0
    clicks: float | None = None
    spend: float | None = None
    ctr: float | None = None


class AdapterGetCreativeDeliveryResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response from adapter's get_creative_delivery method."""

    creatives: list[AdapterCreativeDeliveryItem]
    reporting_period: ReportingPeriod
    currency: str

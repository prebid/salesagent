"""Delivery-related Pydantic schemas.

Extracted from the monolithic schemas module. All classes are re-exported
from ``src.core.schemas`` for backward compatibility.
"""

from datetime import date
from enum import Enum
from typing import Any, Literal

from adcp.types import AggregatedTotals as LibraryAggregatedTotals
from adcp.types import DeliveryMeasurement as LibraryDeliveryMeasurement
from adcp.types import DeliveryMetrics as LibraryDeliveryMetrics
from adcp.types import (
    DeliveryStatus,  # noqa: F401 — re-exported for backward compat
    PricingModel,
)
from adcp.types import GetCreativeDeliveryResponse as LibraryGetCreativeDeliveryResponse
from adcp.types import GetMediaBuyDeliveryRequest as LibraryGetMediaBuyDeliveryRequest
from adcp.types import GetMediaBuyDeliveryResponse as LibraryGetMediaBuyDeliveryResponse
from adcp.types import ReportingPeriod as LibraryReportingPeriod
from pydantic import ConfigDict, Field

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import NestedModelSerializerMixin, SalesAgentBaseModel

# ---------------------------------------------------------------------------
# Simple enum / leaf types
# ---------------------------------------------------------------------------


class DeliveryMeasurement(LibraryDeliveryMeasurement):
    """Measurement provider and methodology for delivery metrics per AdCP spec.

    Extends library type - all fields inherited from AdCP spec.
    The buyer accepts the declared provider as the source of truth for the buy.
    """

    pass  # All fields inherited from library


class DeliveryType(str, Enum):
    """Valid delivery types per AdCP spec."""

    GUARANTEED = "guaranteed"
    NON_GUARANTEED = "non_guaranteed"


# DeliveryStatus: imported from adcp library (all 6 values: delivering,
# not_delivering, completed, budget_exhausted, flight_ended, goal_met).


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class GetMediaBuyDeliveryRequest(LibraryGetMediaBuyDeliveryRequest):
    """Request delivery data for one or more media buys.

    Extends library GetMediaBuyDeliveryRequest - all fields inherited from AdCP spec.

    Examples:
    - Single buy: media_buy_ids=["buy_123"]
    - Multiple buys: buyer_refs=["ref_123", "ref_456"]
    - All active buys: status_filter="active"
    - All buys: status_filter="all"
    - Date range: start_date="2025-01-01", end_date="2025-01-31"

    Note: push_notification_config support pending upstream (adcp issue #276).
    Use ext field for extensions until spec is updated.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # account, reporting_dimensions, attribution_window: now provided by adcp 3.10 library
    # with proper types (AccountReference, ReportingDimensions, AttributionWindow).

    # --- Salesagent extensions (NOT in adcp spec/library) ---
    include_package_daily_breakdown: bool | None = Field(
        None,
        description="Include daily_breakdown arrays within each package (salesagent extension, not in adcp spec)",
    )


# ---------------------------------------------------------------------------
# Delivery data models
# ---------------------------------------------------------------------------


# AdCP-compliant delivery models
# FIXME(salesagent-jz3y): DeliveryTotals and PackageDelivery duplicate fields from
# adcp library Totals/ByPackageItem instead of inheriting. These should extend the
# library types (Pattern #1). Blocked on aligning video_completions -> completed_views
# and adjusting all adapter call sites.
class DeliveryTotals(SalesAgentBaseModel):
    """Aggregate metrics for a media buy or package.

    Note: Does not yet extend library Totals. Library uses ``completed_views``;
    salesagent uses ``video_completions``. A rename across all adapters is needed
    before switching to inheritance.
    """

    impressions: float = Field(ge=0, description="Total impressions delivered")
    spend: float = Field(ge=0, description="Total amount spent")
    clicks: float | None = Field(None, ge=0, description="Total clicks (if applicable)")
    ctr: float | None = Field(None, ge=0, le=1, description="Click-through rate (clicks/impressions)")
    # FIXME(salesagent-jz3y): adcp spec uses ``completed_views``, not ``video_completions``.
    # Rename across all adapters to align with spec, then inherit from library Totals.
    video_completions: float | None = Field(None, ge=0, description="Total video completions (if applicable)")
    completion_rate: float | None = Field(
        None, ge=0, le=1, description="Video completion rate (completions/impressions)"
    )
    conversions: float | None = Field(None, ge=0, description="Total conversions (if applicable)")
    viewability: float | None = Field(None, ge=0, le=1, description="Viewability percentage as 0.0-1.0 (if applicable)")


class PlacementBreakdown(SalesAgentBaseModel):
    """Delivery metrics for a single placement within a package."""

    placement_id: str = Field(description="Placement identifier")
    impressions: float = Field(ge=0, description="Placement impressions")
    spend: float = Field(ge=0, description="Placement spend")
    clicks: float | None = Field(None, ge=0, description="Placement clicks")


class PackageDelivery(SalesAgentBaseModel):
    """Metrics broken down by package.

    Note: Does not yet extend library ByPackageItem. See DeliveryTotals note.
    """

    package_id: str = Field(description="Publisher's package identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this package")
    impressions: float = Field(ge=0, description="Package impressions")
    spend: float = Field(ge=0, description="Package spend")
    clicks: float | None = Field(None, ge=0, description="Package clicks")
    # FIXME(salesagent-jz3y): adcp spec uses ``completed_views``, not ``video_completions``.
    video_completions: float | None = Field(None, ge=0, description="Package video completions")
    pacing_index: float | None = Field(
        None, ge=0, description="Delivery pace (1.0 = on track, <1.0 = behind, >1.0 = ahead)"
    )
    pricing_model: str | None = Field(
        None, description="Pricing model for this package during delivery (e.g., 'cpm', 'cpc', 'vpm', 'flat_rate')"
    )
    rate: float | None = Field(
        None,
        ge=0,
        description="Pricing rate for this package during delivery (required if fixed pricing, null for auction-based)",
    )
    currency: str | None = Field(
        None,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 currency code for this package during delivery (e.g., USD, EUR, GBP)",
    )
    by_placement: list[PlacementBreakdown] | None = Field(
        None,
        description="Placement-level delivery breakdown (populated when reporting_dimensions includes 'placement')",
    )


class DailyBreakdown(SalesAgentBaseModel):
    """Day-by-day delivery metrics.

    Note: Does not yet extend library DailyBreakdownItem. Library also includes
    conversions, conversion_value, roas, new_to_brand_rate fields.
    """

    date: str = Field(description="Date (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$")
    impressions: float = Field(ge=0, description="Daily impressions")
    spend: float = Field(ge=0, description="Daily spend")


class MediaBuyDeliveryData(SalesAgentBaseModel):
    """AdCP-compliant delivery data for a single media buy.

    Note: Does not yet extend library MediaBuyDelivery. Blocked on aligning
    DeliveryTotals (video_completions -> completed_views) and PackageDelivery
    with their library counterparts.

    TODO(salesagent-jz3y): Add buyer_campaign_ref field from adcp spec
    (present in library MediaBuyDelivery but missing here).
    """

    media_buy_id: str = Field(description="Publisher's media buy identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this media buy")
    # FIXME(salesagent-jz3y): Library uses Status enum with ``pending_activation``
    # where salesagent uses ``ready``. Align naming to spec when updating
    # _compute_media_buy_status and all status references.
    status: Literal["ready", "active", "paused", "completed", "failed", "reporting_delayed"] = Field(
        description="Current media buy status. 'ready' means scheduled to go live at flight start date (spec: pending_activation)."
    )
    expected_availability: str | None = Field(
        default=None,
        description="When delayed data is expected to be available (only present when status is reporting_delayed)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    is_adjusted: bool = Field(
        description="Indicates this delivery contains updated data for a previously reported period. Buyer should replace previous period data with these totals.",
        default=False,
    )
    pricing_model: PricingModel | None = Field(default=None, description="Pricing model for this media buy")
    pricing_options: list[dict[str, Any]] | None = Field(
        default=None,
        description="Pricing options active for this media buy, linking back to PricingOption records",
    )
    totals: DeliveryTotals = Field(description="Aggregate metrics for this media buy across all packages")
    by_package: list[PackageDelivery] = Field(description="Metrics broken down by package")
    daily_breakdown: list[DailyBreakdown] | None = Field(None, description="Day-by-day delivery")
    ext: dict[str, Any] = Field(
        default_factory=dict,
        description="AdCP extension object for adapter-specific data",
    )


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


class GetMediaBuyDeliveryResponse(NestedModelSerializerMixin, LibraryGetMediaBuyDeliveryResponse):
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
    media_buy_deliveries: list[MediaBuyDeliveryData] = Field(  # type: ignore[assignment]
        ..., description="Array of delivery data for each media buy"
    )

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Override to ensure webhook metadata fields are present when notification_type is set.

        The base AdCPBaseModel excludes None values, but the AdCP protocol requires
        next_expected_at to be explicitly present (as null) when notification_type
        is 'final' so consumers know no further reports are expected.
        """
        result = super().model_dump(**kwargs)
        if self.notification_type is not None and "next_expected_at" not in result:
            result["next_expected_at"] = None
        return result

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.media_buy_deliveries)
        if count == 0:
            return "No delivery data found for the specified period."
        elif count == 1:
            return "Retrieved delivery data for 1 media buy."
        return f"Retrieved delivery data for {count} media buys."

    def webhook_payload(
        self,
        requested_metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Serialize response as a webhook payload.

        Webhook payloads differ from polling responses:
        - ``aggregated_totals`` is excluded (polling-only field)
        - When *requested_metrics* is provided, each media-buy ``totals``
          dict is filtered to only include those metric keys.

        Args:
            requested_metrics: If provided, only these metric names are
                kept in each ``totals`` dict.  Non-metric keys (like
                ``media_buy_id``, ``status``) are never filtered.

        Returns:
            JSON-ready dict suitable for webhook POST body.
        """
        data = self.model_dump(mode="json", exclude={"aggregated_totals"})

        if requested_metrics is not None:
            metrics_set = set(requested_metrics)
            for delivery in data.get("media_buy_deliveries", []):
                totals = delivery.get("totals")
                if totals is not None:
                    filtered = {k: v for k, v in totals.items() if k in metrics_set}
                    delivery["totals"] = filtered

        return data


# Deprecated - kept for backward compatibility
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
    media_buy_ids, media_buy_buyer_refs, or creative_ids.

    All fields mirror the adcp spec; this flat model is easier to work with
    for MCP parameter expansion and validation.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    media_buy_ids: list[str] | None = Field(
        None,
        min_length=1,
        description="Filter to specific media buys by publisher ID.",
    )
    media_buy_buyer_refs: list[str] | None = Field(
        None,
        min_length=1,
        description="Filter to specific media buys by buyer reference ID.",
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
    pagination, media_buy_id, media_buy_buyer_ref, context, ext.

    Local override:
    - creatives: Uses local CreativeDeliveryData for consistent serialization
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    creatives: list[CreativeDeliveryData] = Field(  # type: ignore[assignment]
        ..., description="Array of creative delivery data"
    )

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.creatives)
        if count == 0:
            return "No creative delivery data found for the specified period."
        elif count == 1:
            return "Retrieved delivery data for 1 creative."
        return f"Retrieved delivery data for {count} creatives."


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

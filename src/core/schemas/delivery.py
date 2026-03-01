"""Delivery-related Pydantic schemas.

Extracted from the monolithic schemas module. All classes are re-exported
from ``src.core.schemas`` for backward compatibility.
"""

from datetime import date
from enum import Enum
from typing import Any, Literal

from adcp.types import AggregatedTotals as LibraryAggregatedTotals
from adcp.types import DeliveryMeasurement as LibraryDeliveryMeasurement
from adcp.types import GetMediaBuyDeliveryRequest as LibraryGetMediaBuyDeliveryRequest
from adcp.types import GetMediaBuyDeliveryResponse as LibraryGetMediaBuyDeliveryResponse
from adcp.types import PricingModel
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


class DeliveryStatus(str, Enum):
    """Operational delivery state of a package."""

    delivering = "delivering"
    not_delivering = "not_delivering"
    completed = "completed"
    budget_exhausted = "budget_exhausted"


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

    # Fields in AdCP spec but not yet in adcp library v3.2.0
    account_id: str | None = Field(
        None,
        description="Filter delivery data to a specific account",
    )
    account: dict[str, Any] | None = Field(
        None,
        description="Filter delivery data to a specific account (spec: account-ref.json)",
    )
    reporting_dimensions: dict[str, Any] | None = Field(
        None,
        description="Request dimensional breakdowns in delivery reporting (geo, device_type, device_platform, audience, placement)",
    )
    include_package_daily_breakdown: bool | None = Field(
        None,
        description="When true, include daily_breakdown arrays within each package in by_package",
    )
    attribution_window: dict[str, Any] | None = Field(
        None,
        description="Attribution window to apply for conversion metrics (post_click, post_view, model)",
    )


# ---------------------------------------------------------------------------
# Delivery data models
# ---------------------------------------------------------------------------


# AdCP-compliant delivery models
class DeliveryTotals(SalesAgentBaseModel):
    """Aggregate metrics for a media buy or package."""

    impressions: float = Field(ge=0, description="Total impressions delivered")
    spend: float = Field(ge=0, description="Total amount spent")
    clicks: float | None = Field(None, ge=0, description="Total clicks (if applicable)")
    ctr: float | None = Field(None, ge=0, le=1, description="Click-through rate (clicks/impressions)")
    video_completions: float | None = Field(None, ge=0, description="Total video completions (if applicable)")
    completion_rate: float | None = Field(
        None, ge=0, le=1, description="Video completion rate (completions/impressions)"
    )


class PackageDelivery(SalesAgentBaseModel):
    """Metrics broken down by package."""

    package_id: str = Field(description="Publisher's package identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this package")
    impressions: float = Field(ge=0, description="Package impressions")
    spend: float = Field(ge=0, description="Package spend")
    clicks: float | None = Field(None, ge=0, description="Package clicks")
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


class DailyBreakdown(SalesAgentBaseModel):
    """Day-by-day delivery metrics."""

    # Webhook-specific metadata (only present in webhook deliveries)
    notification_type: str | None = Field(
        None,
        description="Type of webhook notification: scheduled = regular periodic update, final = campaign completed, delayed = data not yet available, adjusted = resending period with updated data (only present in webhook deliveries)",
    )
    partial_data: bool | None = Field(
        None,
        description="Indicates if any media buys in this webhook have missing/delayed data (only present in webhook deliveries)",
    )
    unavailable_count: int | None = Field(
        None,
        description="Number of media buys with reporting_delayed or failed status (only present in webhook deliveries when partial_data is true)",
        ge=0,
    )
    sequence_number: int | None = Field(
        None, description="Sequential notification number (only present in webhook deliveries, starts at 1)", ge=1
    )
    next_expected_at: str | None = Field(
        None,
        description="ISO 8601 timestamp for next expected notification (only present in webhook deliveries when notification_type is not 'final')",
    )

    date: str = Field(description="Date (YYYY-MM-DD)", pattern=r"^\d{4}-\d{2}-\d{2}$")
    impressions: float = Field(ge=0, description="Daily impressions")
    spend: float = Field(ge=0, description="Daily spend")


class MediaBuyDeliveryData(SalesAgentBaseModel):
    """AdCP-compliant delivery data for a single media buy."""

    media_buy_id: str = Field(description="Publisher's media buy identifier")
    buyer_ref: str | None = Field(None, description="Buyer's reference identifier for this media buy")
    status: Literal["ready", "active", "paused", "completed", "failed", "reporting_delayed"] = Field(
        description="Current media buy status. 'ready' means scheduled to go live at flight start date."
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
    totals: DeliveryTotals = Field(description="Aggregate metrics for this media buy across all packages")
    by_package: list[PackageDelivery] = Field(description="Metrics broken down by package")
    daily_breakdown: list[DailyBreakdown] | None = Field(None, description="Day-by-day delivery")


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

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.media_buy_deliveries)
        if count == 0:
            return "No delivery data found for the specified period."
        elif count == 1:
            return "Retrieved delivery data for 1 media buy."
        return f"Retrieved delivery data for {count} media buys."


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


class AdapterGetMediaBuyDeliveryResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response from adapter's get_media_buy_delivery method"""

    media_buy_id: str
    reporting_period: ReportingPeriod
    totals: DeliveryTotals
    by_package: list[AdapterPackageDelivery]
    currency: str
    daily_breakdown: list[dict] | None = None  # Optional day-by-day delivery metrics

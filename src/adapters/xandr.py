"""
Xandr (Microsoft Monetize) adapter for AdCP.

Implements the AdServerAdapter interface for Microsoft's Xandr platform.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from src.adapters.base import AdServerAdapter
from src.core.retry_utils import api_retry
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    MediaPackage,
    Principal,
    Product,
    ReportingPeriod,
    UpdateMediaBuyResponse,
    url,
)

# NOTE: Xandr adapter needs full refactor - it's using old schemas and patterns
# The other methods (get_media_buy_status, get_media_buy_delivery, etc.) still use old schemas
# that no longer exist. Only create_media_buy has been updated to match the current API.
#
# TODO: Complete Xandr adapter refactor to use current AdCP schemas throughout
# - Replace MediaBuy/MediaBuyDetails stubs with proper schema classes
# - Update all methods to match current API patterns
# - Add comprehensive test coverage
# - Remove this entire stub section


# Temporary stubs for old schemas until Xandr adapter is properly refactored
class MediaBuy:
    """Temporary stub for MediaBuy until xandr.py is properly refactored."""

    media_buy_id: str
    platform_id: str
    order_name: str
    status: str
    details: Any | None

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyDetails:
    """Temporary stub for MediaBuyDetails until xandr.py is properly refactored."""

    total_budget: float | None
    status: str | None

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyStatus:
    """Temporary stub for MediaBuyStatus until xandr.py is properly refactored."""

    media_buy_id: str
    order_status: str
    package_statuses: list[Any]
    total_budget: float
    total_spent: float
    start_date: datetime
    end_date: datetime
    approval_status: str

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class PackageStatus:
    """Temporary stub for Xandr package status tracking (Xandr-specific, not AdCP).

    NOTE: This is NOT the AdCP PackageStatus enum (which was removed in adcp 2.12.0).
    This is an internal class for tracking Xandr-specific package state information
    like delivery percentage and editability.

    Temporary stub until xandr.py is properly refactored to use current schemas.
    """

    state: str
    is_editable: bool
    delivery_percentage: float

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyDeliveryData:
    """Temporary stub for MediaBuyDeliveryData until xandr.py is properly refactored."""

    media_buy_id: str
    reporting_period: Any
    totals: Any
    hourly_delivery: list[Any]
    creative_delivery: list[Any]
    pacing: Any
    alerts: list[Any]

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class HourlyDelivery:
    """Temporary stub for HourlyDelivery until xandr.py is properly refactored."""

    hour: datetime
    impressions: int
    spend: float

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class CreativeDelivery:
    """Temporary stub for CreativeDelivery until xandr.py is properly refactored."""

    creative_id: str
    creative_name: str
    impressions: int
    clicks: int
    spend: float

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class PacingAnalysis:
    """Temporary stub for PacingAnalysis until xandr.py is properly refactored."""

    daily_target_spend: float
    actual_daily_spend: float
    pacing_index: float
    projected_delivery: float
    recommendation: str

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class PerformanceAlert:
    """Temporary stub for PerformanceAlert until xandr.py is properly refactored."""

    level: str
    metric: str
    message: str
    recommendation: str

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class DeliveryMetrics:
    """Temporary stub for DeliveryMetrics until xandr.py is properly refactored."""

    impressions: int
    clicks: int
    spend: float
    cpm: float
    ctr: float

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class CreativeAsset:
    """Temporary stub for CreativeAsset until xandr.py is properly refactored."""

    creative_id: str
    name: str
    format: str
    width: int | None
    height: int | None
    media_url: str
    click_url: str
    duration: int | None
    package_assignments: list[str]

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


logger = logging.getLogger(__name__)


class XandrAdapter(AdServerAdapter):
    """Adapter for Microsoft Xandr (formerly AppNexus) platform."""

    def __init__(self, config: dict[str, Any], principal: Principal):
        """Initialize Xandr adapter with configuration and principal."""
        super().__init__(config, principal)

        # Extract Xandr-specific config
        self.api_endpoint = config.get("api_endpoint", "https://api.appnexus.com")
        self.username = config.get("username")
        self.password = config.get("password")
        self.member_id = config.get("member_id")

        # Principal's advertiser ID mapping
        self.advertiser_id = None
        if principal.platform_mappings and "xandr" in principal.platform_mappings:
            mapping = principal.platform_mappings["xandr"]
            self.advertiser_id = mapping.get("advertiser_id")

        # Session management
        self.token = None
        self.token_expiry = None

        # Manual approval mode
        self.manual_approval = config.get("manual_approval_required", False)
        self.manual_operations = config.get("manual_approval_operations", [])

        logger.info(f"Initialized Xandr adapter for principal {principal.name}")

    @api_retry
    def _authenticate(self):
        """Authenticate with Xandr API and get session token."""
        if self.token and self.token_expiry and datetime.now(UTC) < self.token_expiry:
            return  # Token still valid

        auth_url = f"{self.api_endpoint}/auth"
        auth_data = {"auth": {"username": self.username, "password": self.password}}

        try:
            response = requests.post(auth_url, json=auth_data)
            response.raise_for_status()

            data = response.json()
            if data.get("response", {}).get("status") == "OK":
                self.token = data["response"]["token"]
                # Xandr tokens typically last 2 hours
                self.token_expiry = datetime.now(UTC) + timedelta(hours=2)
                logger.info("Successfully authenticated with Xandr")
            else:
                raise Exception(f"Authentication failed: {data}")

        except Exception as e:
            logger.error(f"Xandr authentication error: {e}")
            raise

    @api_retry
    def _make_request(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        """Make authenticated request to Xandr API."""
        self._authenticate()

        headers = {"Authorization": self.token, "Content-Type": "application/json"}

        url = f"{self.api_endpoint}{endpoint}"

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=data)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data)
            elif method == "PUT":
                response = requests.put(url, headers=headers, json=data)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Xandr API request failed: {e}")
            raise

    def _requires_manual_approval(self, operation: str) -> bool:
        """Check if an operation requires manual approval."""
        return self.manual_approval and operation in self.manual_operations

    def _create_human_task(self, operation: str, details: dict[str, Any]) -> str:
        """Create a task for human approval."""
        import uuid

        from database_session import get_db_session

        from src.core.database.models import Tenant

        task_id = f"task_{uuid.uuid4().hex[:8]}"

        with get_db_session() as session:
            # DEPRECATED: Task system replaced with workflow steps
            # TODO: Update to use workflow system for human-in-the-loop operations
            pass

            # Get tenant config for Slack webhooks
            from sqlalchemy import select

            stmt = select(Tenant).filter_by(tenant_id=self.tenant_id)
            tenant = session.scalars(stmt).first()

            if tenant and tenant.slack_webhook_url:
                # Send Slack notification
                from slack_notifier import get_slack_notifier

                # Build config for Slack notifier
                tenant_config = {"features": {"slack_webhook_url": tenant.slack_webhook_url}}

                slack = get_slack_notifier(tenant_config)
                slack.notify_new_task(
                    task_id=task_id,
                    task_type=operation,
                    title=f"Xandr: {operation.replace('_', ' ').title()}",
                    description=f"Manual approval required for {self.principal.name}",
                    media_buy_id=details.get("media_buy_id", "N/A"),
                )

        return task_id

    def get_products(self) -> list[Product]:
        """Get available products (placement groups in Xandr)."""
        try:
            # Use V3 consolidated pricing types
            from adcp.types import CpmPricingOption, DeliveryMeasurement, DeliveryType
            from adcp.types.generated_poc.core.publisher_property_selector import PublisherPropertySelector1
            from adcp.types.generated_poc.pricing_options.price_guidance import (
                PriceGuidance as AdCPPriceGuidance,
            )

            from src.core.schemas import FormatId

            # In Xandr, products map to placement groups or custom deals
            # For now, return standard IAB formats as products
            products = [
                Product(
                    product_id="xandr_display_standard",
                    name="Display - Standard Banners",
                    description="Standard display banner placements (supports geo, device, os, browser targeting)",
                    format_ids=[
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="display_728x90"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="display_300x250"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="display_320x50"),
                    ],
                    delivery_type=DeliveryType.non_guaranteed,
                    pricing_options=[
                        CpmPricingOption(  # type: ignore[list-item]
                            pricing_option_id="xandr_display_cpm",
                            pricing_model="cpm",
                            currency="USD",
                            floor_price=0.50,  # V3: floor moved to top-level
                            price_guidance=AdCPPriceGuidance(p75=10.0),
                        )
                    ],
                    publisher_properties=[PublisherPropertySelector1(selection_type="all", publisher_domain="*")],  # type: ignore[list-item]
                    measurement=None,
                    creative_policy=None,
                    brief_relevance=None,
                    estimated_exposures=None,
                    delivery_measurement=DeliveryMeasurement(provider="Xandr Reporting"),
                    product_card=None,
                    product_card_detailed=None,
                    placements=None,
                    reporting_capabilities=None,
                ),
                Product(
                    product_id="xandr_video_instream",
                    name="Video - In-Stream",
                    description="Pre-roll, mid-roll, and post-roll video (supports geo, device, content targeting)",
                    format_ids=[
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="video_16x9"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="video_9x16"),
                    ],
                    delivery_type=DeliveryType.non_guaranteed,
                    pricing_options=[
                        CpmPricingOption(  # type: ignore[list-item]
                            pricing_option_id="xandr_video_cpm",
                            pricing_model="cpm",
                            currency="USD",
                            floor_price=10.0,  # V3: floor moved to top-level
                            price_guidance=AdCPPriceGuidance(p75=30.0),
                        )
                    ],
                    publisher_properties=[PublisherPropertySelector1(selection_type="all", publisher_domain="*")],  # type: ignore[list-item]
                    measurement=None,
                    creative_policy=None,
                    brief_relevance=None,
                    estimated_exposures=None,
                    delivery_measurement=DeliveryMeasurement(provider="Xandr Reporting"),
                    product_card=None,
                    product_card_detailed=None,
                    placements=None,
                    reporting_capabilities=None,
                ),
                Product(
                    product_id="xandr_native",
                    name="Native Advertising",
                    description="Native ad placements (supports geo, device, context targeting)",
                    format_ids=[
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="native_1x1"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="native_1.2x1"),
                    ],
                    delivery_type=DeliveryType.non_guaranteed,
                    pricing_options=[
                        CpmPricingOption(  # type: ignore[list-item]
                            pricing_option_id="xandr_native_cpm",
                            pricing_model="cpm",
                            currency="USD",
                            floor_price=2.0,  # V3: floor moved to top-level
                            price_guidance=AdCPPriceGuidance(p75=15.0),
                        )
                    ],
                    publisher_properties=[PublisherPropertySelector1(selection_type="all", publisher_domain="*")],  # type: ignore[list-item]
                    measurement=None,
                    creative_policy=None,
                    brief_relevance=None,
                    estimated_exposures=None,
                    delivery_measurement=DeliveryMeasurement(provider="Xandr Reporting"),
                    product_card=None,
                    product_card_detailed=None,
                    placements=None,
                    reporting_capabilities=None,
                ),
                Product(
                    product_id="xandr_deals",
                    name="Private Marketplace Deals",
                    description="Access to premium inventory through deals (pricing varies by deal)",
                    format_ids=[
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="display_300x250"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="display_728x90"),
                        FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id="video_16x9"),
                    ],
                    delivery_type=DeliveryType.non_guaranteed,
                    pricing_options=[
                        CpmPricingOption(  # type: ignore[list-item]
                            pricing_option_id="xandr_deals_cpm",
                            pricing_model="cpm",
                            currency="USD",
                            floor_price=5.0,  # V3: floor moved to top-level
                            price_guidance=AdCPPriceGuidance(p75=25.0),
                        )
                    ],
                    publisher_properties=[PublisherPropertySelector1(selection_type="all", publisher_domain="*")],  # type: ignore[list-item]
                    measurement=None,
                    creative_policy=None,
                    brief_relevance=None,
                    estimated_exposures=None,
                    delivery_measurement=DeliveryMeasurement(provider="Xandr Reporting"),
                    product_card=None,
                    product_card_detailed=None,
                    placements=None,
                    reporting_capabilities=None,
                ),
            ]

            return products

        except Exception as e:
            logger.error(f"Error fetching Xandr products: {e}")
            return []

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create insertion order and line items in Xandr."""
        from adcp.types import Package as AdCPPackage

        if self._requires_manual_approval("create_media_buy"):
            task_id = self._create_human_task(
                "create_media_buy",
                {"request": request.dict(), "principal": self.principal.name, "advertiser_id": self.advertiser_id},
            )

            # Build package responses - Per AdCP spec, CreateMediaBuyResponse.Package only contains:
            # - buyer_ref (required)
            # - package_id (required)
            package_responses = []
            for idx, package in enumerate(packages):
                # Get matching request package for buyer_ref
                matching_req_package = None
                if request.packages and idx < len(request.packages):
                    matching_req_package = request.packages[idx]

                buyer_ref = "unknown"  # Default fallback
                if matching_req_package and hasattr(matching_req_package, "buyer_ref"):
                    buyer_ref = matching_req_package.buyer_ref or buyer_ref

                # Create minimal AdCP-compliant Package response
                package_responses.append(
                    AdCPPackage(
                        buyer_ref=buyer_ref,
                        package_id=package.package_id,
                        paused=False,
                    )
                )

            return CreateMediaBuySuccess(
                buyer_ref=request.buyer_ref or "",
                media_buy_id=f"xandr_pending_{task_id}",
                packages=package_responses,
                creative_deadline=None,
            )

        try:
            # Calculate total budget from package budgets (AdCP v2.2.0)
            total_budget = request.get_total_budget()
            days = (end_time.date() - start_time.date()).days
            if days == 0:
                days = 1

            # Create insertion order
            if not self.advertiser_id:
                raise ValueError("Advertiser ID is required for Xandr operations")

            # campaign_name is no longer on CreateMediaBuyRequest per AdCP spec
            # Use brand_manifest name or buyer_ref as fallback
            campaign_name = None
            if hasattr(request, "brand_manifest") and request.brand_manifest:
                manifest = request.brand_manifest
                if hasattr(manifest, "name"):
                    campaign_name = manifest.name
                elif isinstance(manifest, dict):
                    campaign_name = manifest.get("name")
            campaign_name = campaign_name or f"AdCP Campaign {request.buyer_ref}"

            io_data = {
                "insertion-order": {
                    "name": campaign_name,
                    "advertiser_id": int(self.advertiser_id),
                    "start_date": start_time.date().isoformat(),
                    "end_date": end_time.date().isoformat(),
                    "budget_intervals": [
                        {
                            "start_date": start_time.date().isoformat(),
                            "end_date": end_time.date().isoformat(),
                            "daily_budget": float(total_budget / days),
                            "lifetime_budget": float(total_budget),
                        }
                    ],
                    "currency": "USD",
                    "timezone": "UTC",
                }
            }

            io_response = self._make_request("POST", "/insertion-order", io_data)
            io_id = io_response["response"]["insertion-order"]["id"]

            package_responses = []

            # Create line items for each package
            for _idx, package in enumerate(packages):
                if not self.advertiser_id:
                    raise ValueError("Advertiser ID is required for creating line items")

                # Get pricing for this package
                pricing_info = package_pricing_info.get(package.package_id) if package_pricing_info else None
                if pricing_info:
                    rate = (
                        pricing_info["rate"] if pricing_info["is_fixed"] else pricing_info.get("bid_price", package.cpm)
                    )
                else:
                    rate = package.cpm

                li_data = {
                    "line-item": {
                        "name": package.name,
                        "insertion_order_id": io_id,
                        "advertiser_id": int(self.advertiser_id),
                        "start_date": start_time.date().isoformat(),
                        "end_date": end_time.date().isoformat(),
                        "revenue_type": "cpm",
                        "revenue_value": rate,  # Use pricing from pricing option or fallback
                        "lifetime_budget": float(rate * package.impressions / 1000),
                        "daily_budget": float(rate * package.impressions / 1000 / days),
                        "currency": "USD",
                        "state": "inactive",  # Start inactive
                        "inventory_type": "display",
                    }
                }

                # Apply targeting (from package-level targeting_overlay per AdCP spec)
                if package.targeting_overlay:
                    targeting_dict = (
                        package.targeting_overlay.model_dump()
                        if hasattr(package.targeting_overlay, "model_dump")
                        else dict(package.targeting_overlay)
                    )
                    li_data["line-item"]["profile_id"] = self._create_targeting_profile(targeting_dict)

                li_response = self._make_request("POST", "/line-item", li_data)
                li_id = li_response["response"]["line-item"]["id"]

                # Build package response - Per AdCP spec, CreateMediaBuyResponse.Package only contains:
                # - buyer_ref (required)
                # - package_id (required)
                # MediaPackage has buyer_ref populated from request
                package_responses.append(
                    AdCPPackage(
                        buyer_ref=package.buyer_ref or "unknown",
                        package_id=package.package_id,
                        paused=False,
                    )
                )

            return CreateMediaBuySuccess(
                buyer_ref=request.buyer_ref or "",
                media_buy_id=f"xandr_io_{io_id}",
                creative_deadline=datetime.now(UTC) + timedelta(days=2),
                packages=package_responses,
            )

        except Exception as e:
            logger.error(f"Failed to create Xandr media buy: {e}")
            raise

    def _map_inventory_type(self, product_id: str) -> str:
        """Map product ID to Xandr inventory type."""
        mapping = {
            "xandr_display_standard": "display",
            "xandr_video_instream": "video",
            "xandr_native": "native",
            "xandr_deals": "display",  # Deals can be various types
        }
        return mapping.get(product_id, "display")

    def _create_targeting_profile(self, targeting: dict[str, Any]) -> int:
        """Create targeting profile in Xandr."""
        profile_data = {
            "profile": {
                "description": "AdCP targeting profile",
                "country_targets": [],
                "region_targets": [],
                "city_targets": [],
                "device_type_targets": [],
            }
        }

        # Map targeting to Xandr format
        if "geo" in targeting:
            geo = targeting["geo"]
            if "countries" in geo:
                profile_data["profile"]["country_targets"] = geo["countries"]
            if "regions" in geo:
                profile_data["profile"]["region_targets"] = geo["regions"]
            if "cities" in geo:
                profile_data["profile"]["city_targets"] = geo["cities"]

        if "device_types" in targeting:
            # Map to Xandr device types - convert to strings for API
            device_map = {"desktop": "1", "mobile": "2", "tablet": "3", "ctv": "4"}
            profile_data["profile"]["device_type_targets"] = [device_map.get(d, "1") for d in targeting["device_types"]]

        response = self._make_request("POST", "/profile", profile_data)
        return response["response"]["profile"]["id"]

    def update_media_buy(
        self,
        media_buy_id: str,
        buyer_ref: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        """Update insertion order in Xandr."""
        # NOTE: This is a stub implementation - needs full refactor to match current API
        raise NotImplementedError("Xandr update_media_buy needs refactor to match current API")

    def get_media_buy_status(self, media_buy_id: str) -> MediaBuyStatus:
        """Get insertion order and line item status."""
        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Get IO status
            io_response = self._make_request("GET", f"/insertion-order?id={io_id}")
            io = io_response["response"]["insertion-order"]

            # Get line items
            li_response = self._make_request("GET", f"/line-item?insertion_order_id={io_id}")
            line_items = li_response["response"]["line-items"]

            # Calculate overall status
            total_budget = io["budget_intervals"][0]["lifetime_budget"]
            spent = sum(li.get("lifetime_budget_imps", 0) * li.get("revenue_value", 0) / 1000 for li in line_items)

            package_statuses = []
            for li in line_items:
                package_statuses.append(
                    PackageStatus(
                        state=li["state"],
                        is_editable=li["state"] != "active",
                        delivery_percentage=(
                            (li.get("lifetime_budget_imps", 0) / li.get("lifetime_pacing", 1)) * 100
                            if li.get("lifetime_pacing")
                            else 0
                        ),
                    )
                )

            return MediaBuyStatus(
                media_buy_id=media_buy_id,
                order_status=io["state"],
                package_statuses=package_statuses,
                total_budget=total_budget,
                total_spent=spent,
                start_date=datetime.fromisoformat(io["start_date"]),
                end_date=datetime.fromisoformat(io["end_date"]),
                approval_status="approved" if io["state"] == "active" else "pending",
            )

        except Exception as e:
            logger.error(f"Failed to get Xandr media buy status: {e}")
            raise

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Get delivery data from Xandr reporting."""
        # NOTE: This is a stub implementation - needs full refactor to match current API
        raise NotImplementedError("Xandr get_media_buy_delivery needs refactor to match current API")

    def add_creatives(self, media_buy_id: str, assets: list[CreativeAsset]) -> dict[str, str]:
        """Upload creatives to Xandr."""
        creative_mapping: dict[str, str] = {}

        try:
            if not self.advertiser_id:
                raise ValueError("Advertiser ID is required for creating creatives")

            for asset in assets:
                # Create creative
                creative_data = {
                    "creative": {
                        "name": asset.name,
                        "advertiser_id": int(self.advertiser_id),
                        "format": self._map_creative_format(asset.format),
                        "width": asset.width or 300,
                        "height": asset.height or 250,
                        "media_url": asset.media_url,
                        "click_url": asset.click_url,
                        "media_type": "image" if asset.format.startswith("display") else "video",
                    }
                }

                if asset.format.startswith("video"):
                    creative_data["creative"]["duration"] = asset.duration or 30

                response = self._make_request("POST", "/creative", creative_data)
                creative_id = response["response"]["creative"]["id"]
                creative_mapping[asset.creative_id] = str(creative_id)

                # Associate creative with line items
                for package_id in asset.package_assignments:
                    if package_id.startswith("xandr_li_"):
                        li_id = package_id.replace("xandr_li_", "")
                        self._make_request("POST", f"/line-item/{li_id}/creative/{creative_id}")

            return creative_mapping

        except Exception as e:
            logger.error(f"Failed to add creatives to Xandr: {e}")
            raise

    def _map_creative_format(self, format_id: str) -> str:
        """Map AdCP format to Xandr format."""
        format_map = {
            "display_728x90": "banner",
            "display_300x250": "banner",
            "display_320x50": "banner",
            "video_16x9": "video",
            "video_9x16": "video",
            "native_1x1": "native",
        }
        return format_map.get(format_id, "banner")

    def pause_media_buy(self, media_buy_id: str) -> bool:
        """Pause insertion order in Xandr."""
        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Update IO state to inactive
            update_data = {"insertion-order": {"state": "inactive"}}

            self._make_request("PUT", f"/insertion-order?id={io_id}", update_data)

            # Also pause all line items
            li_response = self._make_request("GET", f"/line-item?insertion_order_id={io_id}")
            for li in li_response["response"]["line-items"]:
                self._make_request("PUT", f"/line-item?id={li['id']}", {"line-item": {"state": "inactive"}})

            return True

        except Exception as e:
            logger.error(f"Failed to pause Xandr media buy: {e}")
            return False

    def get_all_media_buys(self) -> list[MediaBuy]:
        """Get all insertion orders for the advertiser."""
        try:
            # Get all IOs for advertiser
            response = self._make_request("GET", f"/insertion-order?advertiser_id={self.advertiser_id}")

            media_buys = []
            for io in response["response"]["insertion-orders"]:
                media_buy = MediaBuy(
                    media_buy_id=f"xandr_io_{io['id']}",
                    platform_id=str(io["id"]),
                    order_name=io["name"],
                    status=io["state"],
                    details=None,
                )
                media_buys.append(media_buy)

            return media_buys

        except Exception as e:
            logger.error(f"Failed to get Xandr media buys: {e}")
            return []

    def update_package(self, media_buy_id: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
        """Update package settings for line items."""
        if self._requires_manual_approval("update_package"):
            task_id = self._create_human_task(
                "update_package", {"media_buy_id": media_buy_id, "packages": packages, "principal": self.principal.name}
            )

            return {"status": "accepted", "task_id": task_id, "detail": "Package updates require manual approval"}

        try:
            updated_packages = []

            for package_update in packages:
                package_id = package_update.get("package_id")
                if not package_id or not package_id.startswith("xandr_li_"):
                    continue

                li_id = package_id.replace("xandr_li_", "")

                # Get current line item
                current = self._make_request("GET", f"/line-item?id={li_id}")
                li = current["response"]["line-item"]

                # Apply updates
                if "active" in package_update:
                    li["state"] = "active" if package_update["active"] else "inactive"

                if "budget" in package_update:
                    li["lifetime_budget"] = float(package_update["budget"])
                    # Recalculate daily budget
                    days = (datetime.fromisoformat(li["end_date"]) - datetime.fromisoformat(li["start_date"])).days
                    li["daily_budget"] = float(package_update["budget"]) / days if days > 0 else 0

                if "impressions" in package_update:
                    # Update revenue value based on new impression goal
                    if package_update.get("budget"):
                        li["revenue_value"] = package_update["budget"] / package_update["impressions"] * 1000

                if "pacing" in package_update:
                    # Map pacing to Xandr pacing type
                    pacing_map = {"even": "even", "asap": "aggressive", "front_loaded": "accelerated"}
                    li["pacing"] = pacing_map.get(package_update["pacing"], "even")

                # Update line item
                self._make_request("PUT", f"/line-item?id={li_id}", {"line-item": li})

                # Handle creative updates
                if "creative_ids" in package_update:
                    # Remove existing associations
                    current_creatives = self._make_request("GET", f"/line-item/{li_id}/creative")
                    for creative in current_creatives.get("response", {}).get("creatives", []):
                        self._make_request("DELETE", f"/line-item/{li_id}/creative/{creative['id']}")

                    # Add new associations
                    for creative_id in package_update["creative_ids"]:
                        if creative_id.startswith("xandr_creative_"):
                            xandr_creative_id = creative_id.replace("xandr_creative_", "")
                            self._make_request("POST", f"/line-item/{li_id}/creative/{xandr_creative_id}")

                updated_packages.append({"package_id": package_id, "status": "updated"})

            return {
                "status": "accepted",
                "implementation_date": datetime.now(UTC).isoformat(),
                "detail": f"Updated {len(updated_packages)} packages in Xandr",
                "affected_packages": [p["package_id"] for p in updated_packages],
            }

        except Exception as e:
            logger.error(f"Failed to update Xandr packages: {e}")
            raise

    def resume_media_buy(self, media_buy_id: str) -> bool:
        """Resume paused insertion order in Xandr."""
        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Update IO state to active
            update_data = {"insertion-order": {"state": "active"}}

            self._make_request("PUT", f"/insertion-order?id={io_id}", update_data)

            # Also resume all line items
            li_response = self._make_request("GET", f"/line-item?insertion_order_id={io_id}")
            for li in li_response["response"]["line-items"]:
                self._make_request("PUT", f"/line-item?id={li['id']}", {"line-item": {"state": "active"}})

            return True

        except Exception as e:
            logger.error(f"Failed to resume Xandr media buy: {e}")
            return False

    def get_reporting_data(self, start_date: datetime, end_date: datetime) -> dict[str, Any]:
        """Get comprehensive reporting data for the advertiser."""
        try:
            if not self.advertiser_id:
                raise ValueError("Advertiser ID is required for reporting")

            # Create advertiser-level report
            report_data = {
                "report": {
                    "report_type": "advertiser_analytics",
                    "columns": [
                        "day",
                        "insertion_order_id",
                        "insertion_order_name",
                        "line_item_id",
                        "line_item_name",
                        "creative_id",
                        "creative_name",
                        "imps",
                        "clicks",
                        "media_cost",
                        "booked_revenue",
                        "video_starts",
                        "video_completions",
                    ],
                    "filters": [{"advertiser_id": int(self.advertiser_id)}],
                    "start_date": start_date.date().isoformat(),
                    "end_date": end_date.date().isoformat(),
                    "timezone": "UTC",
                    "format": "json",
                }
            }

            # Request report
            report_response = self._make_request("POST", "/report", report_data)
            report_id = report_response["response"]["report_id"]

            # Poll for report completion
            import time

            max_wait = 60  # Max 60 seconds
            poll_interval = 5
            waited = 0

            while waited < max_wait:
                status_response = self._make_request("GET", f"/report?id={report_id}")
                if status_response["response"]["status"] == "ready":
                    break
                time.sleep(poll_interval)
                waited += poll_interval

            # Download report
            report_data = self._make_request("GET", f"/report-download?id={report_id}")

            # Process and aggregate data
            summary: dict[str, Any] = {
                "total_impressions": 0,
                "total_clicks": 0,
                "total_spend": 0.0,
                "total_revenue": 0.0,
                "video_starts": 0,
                "video_completions": 0,
                "by_insertion_order": {},
                "by_day": {},
            }

            for row in report_data.get("data", []):
                # Type guard: row should be dict[str, Any]
                if not isinstance(row, dict):
                    continue
                # Aggregate totals
                summary["total_impressions"] += row.get("imps", 0)
                summary["total_clicks"] += row.get("clicks", 0)
                summary["total_spend"] += row.get("media_cost", 0)
                summary["total_revenue"] += row.get("booked_revenue", 0)
                summary["video_starts"] += row.get("video_starts", 0)
                summary["video_completions"] += row.get("video_completions", 0)

                # Group by IO
                io_id = str(row.get("insertion_order_id"))
                if io_id not in summary["by_insertion_order"]:
                    summary["by_insertion_order"][io_id] = {
                        "name": row.get("insertion_order_name"),
                        "impressions": 0,
                        "clicks": 0,
                        "spend": 0,
                    }

                io_summary = summary["by_insertion_order"][io_id]
                io_summary["impressions"] += row.get("imps", 0)
                io_summary["clicks"] += row.get("clicks", 0)
                io_summary["spend"] += row.get("media_cost", 0)

                # Group by day
                day = row.get("day")
                if day not in summary["by_day"]:
                    summary["by_day"][day] = {"impressions": 0, "clicks": 0, "spend": 0}

                day_summary = summary["by_day"][day]
                day_summary["impressions"] += row.get("imps", 0)
                day_summary["clicks"] += row.get("clicks", 0)
                day_summary["spend"] += row.get("media_cost", 0)

            # Calculate metrics
            summary["ctr"] = (
                (summary["total_clicks"] / summary["total_impressions"]) if summary["total_impressions"] > 0 else 0
            )
            summary["cpm"] = (
                (summary["total_spend"] / summary["total_impressions"] * 1000)
                if summary["total_impressions"] > 0
                else 0
            )
            summary["completion_rate"] = (
                (summary["video_completions"] / summary["video_starts"]) if summary["video_starts"] > 0 else 0
            )

            return summary

        except Exception as e:
            logger.error(f"Failed to get Xandr reporting data: {e}")
            return {"error": str(e), "total_impressions": 0, "total_clicks": 0, "total_spend": 0}

    def get_creative_performance(
        self, media_buy_id: str, start_date: datetime, end_date: datetime
    ) -> list[dict[str, Any]]:
        """Get creative-level performance data."""
        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Create creative performance report
            report_data = {
                "report": {
                    "report_type": "creative_analytics",
                    "columns": [
                        "creative_id",
                        "creative_name",
                        "line_item_id",
                        "line_item_name",
                        "imps",
                        "clicks",
                        "media_cost",
                        "video_starts",
                        "video_completions",
                        "viewability_measurement_impressions",
                        "viewability_viewed_impressions",
                    ],
                    "filters": [{"insertion_order_id": int(io_id)}],
                    "start_date": start_date.date().isoformat(),
                    "end_date": end_date.date().isoformat(),
                    "timezone": "UTC",
                    "format": "json",
                }
            }

            # Request and wait for report
            report_response = self._make_request("POST", "/report", report_data)
            report_id = report_response["response"]["report_id"]

            import time

            time.sleep(5)  # Simple wait - production would poll properly

            # Download report
            report_data = self._make_request("GET", f"/report-download?id={report_id}")

            # Process creative data
            creative_performance: list[dict[str, Any]] = []

            for row in report_data.get("data", []):
                # Type guard: row should be dict[str, Any]
                if not isinstance(row, dict):
                    continue
                impressions = row.get("imps", 0)
                clicks = row.get("clicks", 0)
                spend = row.get("media_cost", 0)
                video_starts = row.get("video_starts", 0)
                video_completions = row.get("video_completions", 0)
                viewable_imps = row.get("viewability_viewed_impressions", 0)
                measured_imps = row.get("viewability_measurement_impressions", 0)

                creative_performance.append(
                    {
                        "creative_id": f"xandr_creative_{row['creative_id']}",
                        "creative_name": row["creative_name"],
                        "package_id": f"xandr_li_{row['line_item_id']}",
                        "package_name": row["line_item_name"],
                        "impressions": impressions,
                        "clicks": clicks,
                        "spend": spend,
                        "cpm": (spend / impressions * 1000) if impressions > 0 else 0,
                        "ctr": (clicks / impressions) if impressions > 0 else 0,
                        "video_starts": video_starts,
                        "video_completions": video_completions,
                        "completion_rate": (video_completions / video_starts) if video_starts > 0 else 0,
                        "viewability_rate": (viewable_imps / measured_imps) if measured_imps > 0 else 0,
                    }
                )

            return creative_performance

        except Exception as e:
            logger.error(f"Failed to get Xandr creative performance: {e}")
            return []

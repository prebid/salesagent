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
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    MediaPackage,
    Principal,
    Product,
)

# NOTE: Xandr adapter needs full refactor - it's using old schemas and patterns
# The other methods (get_media_buy_status, get_media_buy_delivery, etc.) still use old schemas
# that no longer exist. Only create_media_buy has been updated to match the current API.


# Temporary stubs for old schemas until Xandr adapter is properly refactored
class MediaBuy:
    """Temporary stub for MediaBuy until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyDetails:
    """Temporary stub for MediaBuyDetails until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyStatus:
    """Temporary stub for MediaBuyStatus until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class PackageStatus:
    """Temporary stub for PackageStatus until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MediaBuyDeliveryData:
    """Temporary stub for MediaBuyDeliveryData until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class ReportingPeriod:
    """Temporary stub for ReportingPeriod until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class HourlyDelivery:
    """Temporary stub for HourlyDelivery until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class CreativeDelivery:
    """Temporary stub for CreativeDelivery until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class PacingAnalysis:
    """Temporary stub for PacingAnalysis until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class PerformanceAlert:
    """Temporary stub for PerformanceAlert until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class DeliveryMetrics:
    """Temporary stub for DeliveryMetrics until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class CreativeAsset:
    """Temporary stub for CreativeAsset until xandr.py is properly refactored."""

    def __init__(self, **kwargs):
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
            # In Xandr, products map to placement groups or custom deals
            # For now, return standard IAB formats as products
            products = [
                Product(
                    product_id="xandr_display_standard",
                    name="Display - Standard Banners",
                    description="Standard display banner placements",
                    formats=["display_728x90", "display_300x250", "display_320x50"],
                    targeting_template={
                        "geo": ["country", "region", "city", "postal_code"],
                        "device": ["desktop", "mobile", "tablet"],
                        "os": ["windows", "mac", "ios", "android"],
                        "browser": ["chrome", "safari", "firefox", "edge"],
                    },
                    delivery_type="standard",
                    is_guaranteed=False,
                    min_spend=1000.0,
                    cpm_range={"min": 0.50, "max": 20.0},
                ),
                Product(
                    product_id="xandr_video_instream",
                    name="Video - In-Stream",
                    description="Pre-roll, mid-roll, and post-roll video",
                    formats=["video_16x9", "video_9x16"],
                    targeting_template={
                        "geo": ["country", "region", "city"],
                        "device": ["desktop", "mobile", "tablet", "ctv"],
                        "content": ["genre", "rating", "language"],
                    },
                    delivery_type="standard",
                    is_guaranteed=False,
                    min_spend=5000.0,
                    cpm_range={"min": 10.0, "max": 50.0},
                ),
                Product(
                    product_id="xandr_native",
                    name="Native Advertising",
                    description="Native ad placements",
                    formats=["native_1x1", "native_1.2x1"],
                    targeting_template={
                        "geo": ["country", "region", "city"],
                        "device": ["desktop", "mobile", "tablet"],
                        "context": ["category", "keywords"],
                    },
                    delivery_type="standard",
                    is_guaranteed=False,
                    min_spend=2000.0,
                    cpm_range={"min": 2.0, "max": 25.0},
                ),
                Product(
                    product_id="xandr_deals",
                    name="Private Marketplace Deals",
                    description="Access to premium inventory through deals",
                    formats=["display_300x250", "display_728x90", "video_16x9"],
                    targeting_template={
                        "geo": ["country", "region", "city"],
                        "device": ["desktop", "mobile", "tablet"],
                        "deals": ["deal_id"],
                    },
                    delivery_type="standard",
                    is_guaranteed=False,
                    min_spend=10000.0,
                    price_guidance={"model": "cpm_range", "details": "Varies by deal"},
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
        if self._requires_manual_approval("create_media_buy"):
            task_id = self._create_human_task(
                "create_media_buy",
                {"request": request.dict(), "principal": self.principal.name, "advertiser_id": self.advertiser_id},
            )

            # Build package responses
            package_responses = []
            for package in packages:
                package_responses.append(
                    {
                        "package_id": package.package_id,
                    }
                )

            return CreateMediaBuyResponse(
                buyer_ref=request.buyer_ref,
                media_buy_id=f"xandr_pending_{task_id}",
                packages=package_responses,
            )

        try:
            # Calculate total budget from package budgets (AdCP v2.2.0)
            total_budget = request.get_total_budget()
            days = (end_time.date() - start_time.date()).days
            if days == 0:
                days = 1

            # Create insertion order
            io_data = {
                "insertion-order": {
                    "name": request.campaign_name or f"AdCP Campaign {request.buyer_ref}",
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
            for package in packages:
                # Get pricing for this package
                pricing_info = package_pricing_info.get(package.package_id) if package_pricing_info else None
                if pricing_info:
                    rate = pricing_info["rate"] if pricing_info["is_fixed"] else pricing_info.get("bid_price", package.cpm)
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

                # Apply targeting
                if request.targeting_overlay:
                    li_data["line-item"]["profile_id"] = self._create_targeting_profile(request.targeting_overlay)

                li_response = self._make_request("POST", "/line-item", li_data)
                li_id = li_response["response"]["line-item"]["id"]

                # Build package response with package_id and platform_line_item_id
                package_responses.append(
                    {
                        "package_id": package.package_id,
                        "platform_line_item_id": str(li_id),
                    }
                )

            # Log the operation
            self._log_operation(
                "create_media_buy", True, {"insertion_order_id": io_id, "line_item_count": len(package_responses)}
            )

            return CreateMediaBuyResponse(
                buyer_ref=request.buyer_ref,
                media_buy_id=f"xandr_io_{io_id}",
                creative_deadline=datetime.now(UTC) + timedelta(days=2),
                packages=package_responses,
            )

        except Exception as e:
            logger.error(f"Failed to create Xandr media buy: {e}")
            self._log_operation("create_media_buy", False, {"error": str(e)})
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
                profile_data["profile"]["country_targets"] = [{"country": c} for c in geo["countries"]]
            if "regions" in geo:
                profile_data["profile"]["region_targets"] = [{"region": r} for r in geo["regions"]]
            if "cities" in geo:
                profile_data["profile"]["city_targets"] = [{"city": c} for c in geo["cities"]]

        if "device_types" in targeting:
            # Map to Xandr device types
            device_map = {"desktop": 1, "mobile": 2, "tablet": 3, "ctv": 4}
            profile_data["profile"]["device_type_targets"] = [device_map.get(d, 1) for d in targeting["device_types"]]

        response = self._make_request("POST", "/profile", profile_data)
        return response["response"]["profile"]["id"]

    def update_media_buy(self, media_buy_id: str, updates: MediaBuyDetails) -> MediaBuy:
        """Update insertion order in Xandr."""
        if self._requires_manual_approval("update_media_buy"):
            task_id = self._create_human_task(
                "update_media_buy",
                {"media_buy_id": media_buy_id, "updates": updates.dict(), "principal": self.principal.name},
            )

            # Return current state with pending status
            return MediaBuy(
                media_buy_id=media_buy_id,
                platform_id=media_buy_id.replace("xandr_io_", ""),
                order_name=f"Update pending - {task_id}",
                status="update_pending",
                details=None,
            )

        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Get current IO
            current = self._make_request("GET", f"/insertion-order?id={io_id}")
            io = current["response"]["insertion-order"]

            # Apply updates
            if updates.total_budget:
                io["budget_intervals"][0]["lifetime_budget"] = float(updates.total_budget)

            if updates.status:
                io["state"] = "active" if updates.status == "active" else "inactive"

            # Update IO
            self._make_request("PUT", f"/insertion-order?id={io_id}", {"insertion-order": io})

            return MediaBuy(
                media_buy_id=media_buy_id, platform_id=io_id, order_name=io["name"], status=io["state"], details=None
            )

        except Exception as e:
            logger.error(f"Failed to update Xandr media buy: {e}")
            raise

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

    def get_media_buy_delivery(self, media_buy_id: str, period: ReportingPeriod) -> MediaBuyDeliveryData:
        """Get delivery data from Xandr reporting."""
        try:
            io_id = media_buy_id.replace("xandr_io_", "")

            # Create report request
            report_data = {
                "report": {
                    "report_type": "network_analytics",
                    "columns": [
                        "hour",
                        "imps",
                        "clicks",
                        "media_cost",
                        "booked_revenue",
                        "line_item_id",
                        "line_item_name",
                        "creative_id",
                        "creative_name",
                    ],
                    "filters": [{"insertion_order_id": int(io_id)}],
                    "start_date": period.start.isoformat(),
                    "end_date": period.end.isoformat(),
                    "timezone": "UTC",
                    "format": "json",
                }
            }

            # Request report
            report_response = self._make_request("POST", "/report", report_data)
            report_id = report_response["response"]["report_id"]

            # Poll for report completion (simplified - in production would need proper polling)
            import time

            time.sleep(5)

            # Download report
            report_data = self._make_request("GET", f"/report-download?id={report_id}")

            # Process report data
            hourly_data = []
            creative_data = []
            total_impressions = 0
            total_spend = 0

            for row in report_data.get("data", []):
                hour_data = HourlyDelivery(
                    hour=datetime.fromisoformat(row["hour"]), impressions=row["imps"], spend=row["media_cost"]
                )
                hourly_data.append(hour_data)

                total_impressions += row["imps"]
                total_spend += row["media_cost"]

                # Aggregate by creative
                creative_key = f"{row['creative_id']}_{row['line_item_id']}"
                creative_found = False
                for cd in creative_data:
                    if cd.creative_id == creative_key:
                        cd.impressions += row["imps"]
                        cd.spend += row["media_cost"]
                        creative_found = True
                        break

                if not creative_found and row["creative_id"]:
                    creative_data.append(
                        CreativeDelivery(
                            creative_id=creative_key,
                            creative_name=row["creative_name"],
                            impressions=row["imps"],
                            clicks=row["clicks"],
                            spend=row["media_cost"],
                        )
                    )

            # Calculate pacing
            days_elapsed = (period.end - period.start).days
            days_total = 30  # Assume 30-day campaign for now
            expected_delivery = (days_elapsed / days_total) * 100
            actual_delivery = (total_spend / 50000) * 100  # Assume $50k budget

            pacing = PacingAnalysis(
                daily_target_spend=50000 / 30,
                actual_daily_spend=total_spend / days_elapsed if days_elapsed > 0 else 0,
                pacing_index=actual_delivery / expected_delivery if expected_delivery > 0 else 0,
                projected_delivery=actual_delivery * (days_total / days_elapsed) if days_elapsed > 0 else 0,
                recommendation="On track" if actual_delivery >= expected_delivery * 0.9 else "Under-pacing",
            )

            # Check for alerts
            alerts = []
            if actual_delivery < expected_delivery * 0.8:
                alerts.append(
                    PerformanceAlert(
                        level="warning",
                        metric="pacing",
                        message=f"Campaign pacing at {actual_delivery:.1f}% vs expected {expected_delivery:.1f}%",
                        recommendation="Consider increasing bids or expanding targeting",
                    )
                )

            return MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                reporting_period=period,
                totals=DeliveryMetrics(
                    impressions=total_impressions,
                    clicks=sum(cd.clicks for cd in creative_data),
                    spend=total_spend,
                    cpm=total_spend / total_impressions * 1000 if total_impressions > 0 else 0,
                    ctr=sum(cd.clicks for cd in creative_data) / total_impressions if total_impressions > 0 else 0,
                ),
                hourly_delivery=hourly_data,
                creative_delivery=creative_data,
                pacing=pacing,
                alerts=alerts,
            )

        except Exception as e:
            logger.error(f"Failed to get Xandr delivery data: {e}")
            # Return empty data on error
            return MediaBuyDeliveryData(
                media_buy_id=media_buy_id,
                reporting_period=period,
                totals=DeliveryMetrics(impressions=0, clicks=0, spend=0.0, cpm=0.0, ctr=0.0),
                hourly_delivery=[],
                creative_delivery=[],
                pacing=PacingAnalysis(
                    daily_target_spend=0,
                    actual_daily_spend=0,
                    pacing_index=0,
                    projected_delivery=0,
                    recommendation="No data available",
                ),
                alerts=[
                    PerformanceAlert(
                        level="error",
                        metric="data",
                        message="Failed to retrieve delivery data",
                        recommendation="Contact support",
                    )
                ],
            )

    def add_creatives(self, media_buy_id: str, assets: list[CreativeAsset]) -> dict[str, str]:
        """Upload creatives to Xandr."""
        creative_mapping = {}

        try:
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
            summary = {
                "total_impressions": 0,
                "total_clicks": 0,
                "total_spend": 0,
                "total_revenue": 0,
                "video_starts": 0,
                "video_completions": 0,
                "by_insertion_order": {},
                "by_day": {},
            }

            for row in report_data.get("data", []):
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
            creative_performance = []

            for row in report_data.get("data", []):
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

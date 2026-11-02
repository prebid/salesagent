import logging
import uuid
from datetime import datetime
from typing import Any

from pydantic import JsonValue

from src.adapters.base import (
    AdapterCreateRequest,
    AdapterCreateResult,
    AdapterUpdateResult,
    AdServerAdapter,
    CreativeEngineAdapter,
)
from src.adapters.constants import require_supported_update_action
from src.adapters.utils.pricing import resolve_package_rate
from src.adapters.vendor_http import VendorHttpClient, require_vendor
from src.core.errors.details import CapabilityRefusalDetails, EntityRefDetails
from src.core.exceptions import (
    AdCPCapabilityNotSupportedError,
    AdCPPackageNotFoundError,
)
from src.core.schemas import *
from src.core.security.outbound_http import OperatorEndpoint, OutboundError


class Kevel(AdServerAdapter):
    """
    Adapter for interacting with the Kevel Management API.
    """

    adapter_name = "kevel"

    # Kevel specializes in social and retail_media
    # V3 channel names: native → social, retail → retail_media
    default_channels = ["social", "retail_media"]

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, creative_engine, tenant_id)

        # Get Kevel-specific principal ID
        self.advertiser_id = self._require_config(self.principal.get_adapter_id("kevel"), field="advertiser_id")

        # Get Kevel configuration
        self.network_id = self.config.get("network_id")
        self.api_key = self.config.get("api_key")
        self.base_url = "https://api.kevel.co/v1"

        # Feature flags
        self.userdb_enabled = self.config.get("userdb_enabled", False)
        self.frequency_capping_enabled = self.config.get("frequency_capping_enabled", False)

        self.network_id = self._require_config(self.network_id, field="network_id")
        self.api_key = self._require_config(self.api_key, field="api_key")
        self._vendor: VendorHttpClient | None = VendorHttpClient(
            base_url=self.base_url,
            headers={"X-Adzerk-ApiKey": self.api_key, "Content-Type": "application/json"},
        )

    # Supported device types (Kevel doesn't support CTV)
    SUPPORTED_DEVICE_TYPES = {"mobile", "desktop", "tablet"}

    # Supported media types
    SUPPORTED_MEDIA_TYPES = {"display", "native"}

    def _validate_targeting(self, targeting_overlay):
        """Validate targeting and return unsupported features."""
        unsupported = []

        if not targeting_overlay:
            return unsupported

        # Check device types
        if targeting_overlay.device_form_factors:
            for device in targeting_overlay.device_form_factors:
                if device not in self.SUPPORTED_DEVICE_TYPES:
                    unsupported.append(
                        f"Device type '{device}' not supported (Kevel supports: {', '.join(self.SUPPORTED_DEVICE_TYPES)})"
                    )

        # Check media types
        if targeting_overlay.media_type_any_of:
            for media in targeting_overlay.media_type_any_of:
                if media not in self.SUPPORTED_MEDIA_TYPES:
                    unsupported.append(
                        f"Media type '{media}' not supported (Kevel supports: {', '.join(self.SUPPORTED_MEDIA_TYPES)})"
                    )

        # Audience targeting requires UserDB
        if targeting_overlay.audiences_any_of and not self.userdb_enabled:
            unsupported.append("Audience targeting requires UserDB to be enabled (set userdb_enabled=true in config)")

        # Frequency capping validation
        if targeting_overlay.frequency_cap:
            if not self.frequency_capping_enabled:
                unsupported.append(
                    "Frequency capping requires this feature to be enabled (set frequency_capping_enabled=true in config)"
                )
            elif targeting_overlay.frequency_cap.scope == "media_buy":
                # Kevel doesn't have campaign-level frequency capping, only flight-level
                unsupported.append(
                    "Media buy level frequency capping not supported (Kevel only supports package/flight level)"
                )

        return unsupported

    def _build_targeting(self, targeting_overlay):
        """Build Kevel targeting criteria from AdCP targeting."""
        if not targeting_overlay:
            return {}

        kevel_targeting = {}

        # Geographic targeting (v3 structured fields)
        geo = {}
        if targeting_overlay.geo_countries:
            geo["countries"] = [c.root for c in targeting_overlay.geo_countries]
        if targeting_overlay.geo_regions:
            geo["regions"] = [r.root for r in targeting_overlay.geo_regions]
        if targeting_overlay.geo_metros:
            # Extract metro values from structured objects and convert to integers
            metro_values = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            geo["metros"] = [int(m) for m in metro_values]

        if geo:
            kevel_targeting["geo"] = geo

        # Keywords
        if targeting_overlay.keywords_any_of:
            kevel_targeting["keywords"] = targeting_overlay.keywords_any_of

        # Device targeting (map to Kevel format)
        if targeting_overlay.device_form_factors:
            # Kevel uses strings for device targeting
            devices = []
            for device in targeting_overlay.device_form_factors:
                if device in self.SUPPORTED_DEVICE_TYPES:
                    devices.append(device)
            if devices:
                kevel_targeting["devices"] = devices

        # Audience/Interest targeting via UserDB
        if targeting_overlay.audiences_any_of and self.userdb_enabled:
            # Build custom targeting expressions for interests
            custom_targeting = []
            for segment in targeting_overlay.audiences_any_of:
                # Convert segment IDs to Kevel interest targeting format
                # Example: "3p:sports_fans" becomes "$user.interests CONTAINS \"Sports Fans\""
                if ":" in segment:
                    provider, interest = segment.split(":", 1)
                    # Convert snake_case to Title Case for Kevel
                    interest_name = interest.replace("_", " ").title()
                    custom_targeting.append(f'$user.interests CONTAINS "{interest_name}"')
                else:
                    custom_targeting.append(f'$user.interests CONTAINS "{segment}"')

            if custom_targeting:
                # Combine with OR logic
                kevel_targeting["CustomTargeting"] = " OR ".join(custom_targeting)

        # Custom targeting
        if targeting_overlay.custom and "kevel" in targeting_overlay.custom:
            kevel_custom = targeting_overlay.custom["kevel"]
            if "site_ids" in kevel_custom:
                kevel_targeting["siteIds"] = kevel_custom["site_ids"]
            if "zone_ids" in kevel_custom:
                kevel_targeting["zoneIds"] = kevel_custom["zone_ids"]
            # Allow direct CustomTargeting override
            if "custom_targeting" in kevel_custom:
                kevel_targeting["CustomTargeting"] = kevel_custom["custom_targeting"]

        self.log(f"Applying Kevel targeting: {list(kevel_targeting.keys())}")
        return kevel_targeting

    def create_media_buy(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict[str, Any]] | None = None,
    ) -> AdapterCreateResult:
        """Creates a new Campaign and associated Flights in Kevel."""
        # Log operation
        self.audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=self.advertiser_id or "unknown",
            success=True,
            details={"po_number": request.po_number, "flight_dates": f"{start_time.date()} to {end_time.date()}"},
        )

        self.log(
            f"Kevel.create_media_buy for principal '{self.principal.name}' (Kevel advertiser ID: {self.advertiser_id})",
        )

        # Validate targeting from MediaPackage objects (targeting_overlay is populated from request)
        unsupported_features = []
        for package in packages:
            if package.targeting_overlay:
                features = self._validate_targeting(package.targeting_overlay)
                if features:
                    unsupported_features.extend(features)

        if unsupported_features:
            error_msg = f"Unsupported targeting features for Kevel: {'; '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            raise AdCPCapabilityNotSupportedError(details=CapabilityRefusalDetails(rejected_value=unsupported_features))

        # Generate a media buy ID
        media_buy_id = f"kevel_{request.po_number}" if request.po_number else f"kevel_{uuid.uuid4().hex[:8]}"

        # Calculate total budget using pricing_info if available
        total_budget = 0.0
        for package in packages:
            total_budget += resolve_package_rate(package, package_pricing_info) * package.impressions / 1000

        # Create campaign in Kevel
        campaign_payload: dict[str, JsonValue] = {
            "AdvertiserId": int(self.advertiser_id) if self.advertiser_id else 0,
            "Name": f"AdCP Campaign {media_buy_id}",
            "StartDate": start_time.isoformat(),
            "EndDate": end_time.isoformat(),
            "DailyBudget": total_budget / ((end_time - start_time).days + 1),
            "IsActive": True,
        }

        response = require_vendor(self._vendor, vendor="Kevel").call("POST", "/campaign", json=campaign_payload)
        campaign_data = response.json()
        campaign_id = campaign_data["Id"]
        self.audit_logger.log_success(f"Created Kevel Campaign ID: {campaign_id}")

        # Create flights for each package
        for package in packages:
            rate = resolve_package_rate(package, package_pricing_info)

            flight_payload = {
                "Name": package.name,
                "CampaignId": campaign_id,
                "Priority": 5,  # Standard priority
                "GoalType": 2,  # Impressions goal
                "Impressions": package.impressions,
                "Price": rate,  # Use pricing from pricing option or fallback
                "StartDate": start_time.isoformat(),
                "EndDate": end_time.isoformat(),
                "IsActive": True,
            }

            # Add targeting if provided (from package-level targeting_overlay per AdCP spec)
            if package.targeting_overlay:
                targeting = self._build_targeting(package.targeting_overlay)
                if targeting:
                    flight_payload.update(targeting)

                # Add frequency capping if enabled (package level only)
                freq_cap = getattr(package.targeting_overlay, "frequency_cap", None)
                if freq_cap and self.frequency_capping_enabled:
                    if getattr(freq_cap, "scope", None) == "package":
                        # Kevel's FreqCap = 1 impression
                        # FreqCapDuration in hours, convert from minutes
                        flight_payload["FreqCap"] = 1
                        flight_payload["FreqCapDuration"] = int(
                            max(1, freq_cap.suppress_minutes // 60)
                        )  # Convert to hours, minimum 1 (int for Kevel API)
                        flight_payload["FreqCapType"] = 1  # 1 = per user (cookie-based)

            require_vendor(self._vendor, vendor="Kevel").call("POST", "/flight", json=flight_payload)

        # Use the actual campaign ID from Kevel
        media_buy_id = f"kevel_{campaign_id}"

        return self._build_create_success(media_buy_id, packages)

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Creates new Creatives in Kevel and associates them with Flights."""
        self.log(f"Kevel.add_creative_assets for media buy '{media_buy_id}'")
        created_asset_statuses = []

        try:
            # Get all flights for the campaign to map package names to flight IDs
            flights_response = require_vendor(self._vendor, vendor="Kevel").call(
                "GET", "/flight", params={"campaignId": media_buy_id}
            )
            flights = flights_response.json().get("items", [])
            flight_map = {flight["Name"]: flight["Id"] for flight in flights}

            for asset in assets:
                creative_payload = {
                    "Name": asset["name"],
                    "IsActive": True,
                }

                if asset["format"] == "custom" and asset.get("template_id"):
                    creative_payload["TemplateId"] = asset["template_id"]
                    creative_payload["Data"] = asset.get("template_data", {})
                elif asset["format"] == "image":
                    creative_payload["Body"] = (
                        f"<a href='{asset['click_url']}' target='_blank'><img src='{asset['media_url']}'/></a>"
                    )
                    creative_payload["Url"] = asset["click_url"]
                elif asset["format"] == "video":
                    creative_payload["ThirdPartyUrl"] = asset["media_url"]
                else:
                    self.log(
                        f"Skipping asset {asset['creative_id']} with unsupported format for Kevel: {asset['format']}"
                    )
                    continue

                # Create the creative
                creative_response = require_vendor(self._vendor, vendor="Kevel").call(
                    "POST", "/creative", json=creative_payload
                )
                creative_data = creative_response.json()
                creative_id = creative_data["Id"]

                # Associate the creative with the assigned flights
                flight_ids_to_associate = [
                    flight_map[pkg_id] for pkg_id in asset.get("package_assignments", []) if pkg_id in flight_map
                ]

                if flight_ids_to_associate:
                    for flight_id in flight_ids_to_associate:
                        ad_payload = {"CreativeId": creative_id, "FlightId": flight_id, "IsActive": True}
                        require_vendor(self._vendor, vendor="Kevel").call("POST", "/ad", json=ad_payload)

                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

        except OutboundError as e:
            # Narrowed, not widened: AdCPPackageNotFoundError is raised inside
            # this same try and is deliberately NOT caught here — widening to
            # AdCPSalesAgentError would swallow it and report a package-not-found as an
            # adapter failure.
            self.log(f"Error creating Kevel Creative or Ad: {e}")
            for asset in assets:
                if not any(s.creative_id == asset["creative_id"] for s in created_asset_statuses):
                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))

        return created_asset_statuses

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Associate already-uploaded creatives with flights (Kevel line items).

        Note: Kevel doesn't have a separate association step - creatives are
        associated during flight creation. This method is a no-op for Kevel.
        """
        self.log(
            "[yellow]Kevel: Creative association happens during flight creation (no separate step needed)[/yellow]"
        )

        return [
            {
                "line_item_id": line_item_id,
                "creative_id": creative_id,
                "status": "skipped",
                "message": "Kevel associates creatives during flight creation",
            }
            for line_item_id in line_item_ids
            for creative_id in platform_creative_ids
        ]

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Checks the status of a media buy on Kevel."""
        self.log(f"Kevel.check_media_buy_status for media buy '{media_buy_id}'")

        # In production, would query campaign status
        return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Gets delivery data for a media buy from Kevel reporting."""
        self.log(
            f"Kevel.get_media_buy_delivery for principal '{self.principal.name}' and media buy '{media_buy_id}'",
        )
        self.log(f"Date range: {date_range.start} to {date_range.end}")

        # Queue a report in Kevel
        report_request: dict[str, JsonValue] = {
            "StartDate": date_range.start.isoformat(),
            "EndDate": date_range.end.isoformat(),
            "GroupBy": ["day", "campaign", "flight"],
            "Filter": {"CampaignId": media_buy_id},
        }

        response = require_vendor(self._vendor, vendor="Kevel").call("POST", "/report/queue", json=report_request)
        report_id = response.json()["Id"]

        # Poll for report completion (simplified - in production would need proper polling)
        import time

        time.sleep(1)

        # Get report results
        results_response = require_vendor(self._vendor, vendor="Kevel").call("GET", f"/report/{report_id}/results")

        # Parse results and aggregate
        results = results_response.json()
        total_impressions = sum(row.get("Impressions", 0) for row in results.get("Records", []))
        total_revenue = sum(row.get("Revenue", 0) for row in results.get("Records", []))

        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            totals=DeliveryTotals(
                impressions=total_impressions,
                spend=total_revenue,
                clicks=0,
                ctr=0.0,
                completed_views=0,
                completion_rate=0.0,
            ),
            by_package=[],
            currency="USD",
        )

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> AdapterUpdateResult:
        """Updates a media buy in Kevel using standardized actions."""

        self.log(f"Kevel.update_media_buy for {media_buy_id} with action {action}")

        require_supported_update_action(action)

        try:
            # Extract campaign ID
            campaign_id = media_buy_id.replace("kevel_", "")

            if action in ["pause_media_buy", "resume_media_buy"]:
                # Update campaign status
                update_payload: dict[str, JsonValue] = {"IsActive": action == "resume_media_buy"}
                require_vendor(self._vendor, vendor="Kevel").call(
                    "PUT", f"/campaign/{campaign_id}", json=update_payload
                )

            elif action in ["pause_package", "resume_package"] and package_id:
                # Get flight ID by name
                flights_response = require_vendor(self._vendor, vendor="Kevel").call(
                    "GET", "/flight", params={"campaignId": campaign_id}
                )
                flights = flights_response.json().get("items", [])

                flight = next((f for f in flights if f["Name"] == package_id), None)
                if not flight:
                    raise AdCPPackageNotFoundError(details=EntityRefDetails(package_id=package_id))

                # Update flight status
                is_resume = action == "resume_package"
                update_payload = {"IsActive": is_resume}
                require_vendor(self._vendor, vendor="Kevel").call("PUT", f"/flight/{flight['Id']}", json=update_payload)

                # Return affected package with paused state
                return AdapterUpdateResult(
                    media_buy_id=media_buy_id,
                    affected_packages=[
                        AffectedPackage(
                            package_id=package_id,
                            paused=not is_resume,
                            changes_applied=None,
                            buyer_package_ref=None,
                        )
                    ],
                )

            elif (
                action in ["update_package_budget", "update_package_impressions"] and package_id and budget is not None
            ):
                # Get flight ID by name
                flights_response = require_vendor(self._vendor, vendor="Kevel").call(
                    "GET", "/flight", params={"campaignId": campaign_id}
                )
                flights = flights_response.json().get("items", [])

                flight = next((f for f in flights if f["Name"] == package_id), None)
                if not flight:
                    raise AdCPPackageNotFoundError(details=EntityRefDetails(package_id=package_id))

                # Calculate impressions based on action
                if action == "update_package_budget":
                    # Get current CPM from flight
                    cpm = flight.get("Price", 10.0)  # Default to $10 CPM
                    new_impressions = int((budget / cpm) * 1000)
                else:  # update_package_impressions
                    new_impressions = budget  # budget param contains impressions

                # Update flight impressions
                impressions_payload: dict[str, JsonValue] = {"Impressions": new_impressions}
                require_vendor(self._vendor, vendor="Kevel").call(
                    "PUT", f"/flight/{flight['Id']}", json=impressions_payload
                )

            return AdapterUpdateResult(
                media_buy_id=media_buy_id,
                affected_packages=[],
            )

        except OutboundError as e:
            # Delegates rather than rewrapping: raise_mapped_outbound_error already
            # produces the right AdCP class per failure (refusal -> configuration,
            # 429 -> rate limited, terminal 4xx -> adapter, 5xx re-raised as the
            # service-unavailable it already is). The old
            # `raise AdCPAdapterError(str(e))` would now wrap an AdCP error in an
            # AdCP error. Note the message text changes: str(e) used to carry the
            # requests detail, and the seam's is a fixed constant by design.
            # Imported here, not at module level: src.core.helpers.__init__ pulls
            # in adapter_helpers, which imports the adapters — including this one.
            from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error

            self.log(f"Error updating Kevel flight: {e}")
            # The same obligation the AdCPAdapterError(internal_detail=e) branch
            # carried is met here, not dropped: ``requests``' text carried the ad
            # server's URL/response body, which AdCP 3.1.1 transport-errors.mdx
            # § Security Considerations keeps off the buyer wire. The mapper's
            # messages are fixed first-party sentences naming a role, never an
            # address or a vendor body, and the detail stays in the log line above.
            raise_mapped_outbound_error(e, provenance=OperatorEndpoint("Kevel"), logger=logging.getLogger(__name__))

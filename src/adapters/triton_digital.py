import logging
from datetime import UTC, datetime
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
    AdCPAdapterError,
    AdCPCapabilityNotSupportedError,
    AdCPPackageNotFoundError,
)
from src.core.schemas import *
from src.core.security.outbound_http import OperatorEndpoint, OutboundError, send


class TritonDigital(AdServerAdapter):
    """
    Adapter for interacting with the Triton Digital TAP API.
    """

    adapter_name = "triton"

    # Triton Digital specializes in streaming_audio and podcast advertising
    # V3 channel names: audio → streaming_audio
    default_channels = ["streaming_audio", "podcast"]

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, creative_engine, tenant_id)

        # Get Triton-specific principal ID
        self.advertiser_id = self._require_config(self.principal.get_adapter_id("triton"), field="advertiser_id")

        # Get Triton configuration
        self.base_url = self.config.get("base_url", "https://tap-api.tritondigital.com/v1")
        self.auth_token = self.config.get("auth_token")

        self.auth_token = self._require_config(self.auth_token, field="auth_token")
        self._vendor: VendorHttpClient | None = VendorHttpClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"},
        )

    # Only audio device types supported
    SUPPORTED_DEVICE_TYPES = {"mobile", "desktop", "audio"}

    # Only audio media type supported
    SUPPORTED_MEDIA_TYPES = {"audio"}

    def _validate_targeting(self, targeting_overlay):
        """Validate targeting and return unsupported features."""
        unsupported = []

        if not targeting_overlay:
            return unsupported

        # Check device types - only audio-capable devices
        if targeting_overlay.device_form_factors:
            for device in targeting_overlay.device_form_factors:
                if device not in self.SUPPORTED_DEVICE_TYPES:
                    unsupported.append(
                        f"Device type '{device}' not supported (Triton supports audio-capable devices only)"
                    )

        # Check media types - only audio
        if targeting_overlay.media_type_any_of:
            non_audio = [m for m in targeting_overlay.media_type_any_of if m != "audio"]
            if non_audio:
                unsupported.append(f"Media types {non_audio} not supported (Triton is audio-only)")

        # Video/display targeting makes no sense for audio
        if targeting_overlay.content_cat_any_of:
            unsupported.append("IAB content categories not supported (use custom genres for audio)")

        # Browser targeting not relevant for audio
        if targeting_overlay.browser_any_of:
            unsupported.append("Browser targeting not supported for audio platform")

        return unsupported

    def _build_targeting(self, targeting_overlay):
        """Build Triton targeting criteria from AdCP targeting."""
        if not targeting_overlay:
            return {}

        triton_targeting = {}

        # Geographic targeting (v3 structured fields, audio market focused)
        targeting_obj = {}
        if targeting_overlay.geo_countries:
            targeting_obj["countries"] = [c.root for c in targeting_overlay.geo_countries]
        if targeting_overlay.geo_regions:
            targeting_obj["states"] = [r.root for r in targeting_overlay.geo_regions]
        if targeting_overlay.geo_metros:
            # Map to audio market names if possible
            targeting_obj["markets"] = []  # Would need metro-to-market mapping

        if targeting_obj:
            triton_targeting["targeting"] = targeting_obj

        # Audio-specific targeting from custom field
        if targeting_overlay.custom and "triton" in targeting_overlay.custom:
            triton_custom = targeting_overlay.custom["triton"]
            if "station_ids" in triton_custom:
                triton_targeting["stationIds"] = triton_custom["station_ids"]
            if "genres" in triton_custom:
                triton_targeting["genres"] = triton_custom["genres"]
            if "stream_types" in triton_custom:
                triton_targeting["streamTypes"] = triton_custom["stream_types"]

        self.log(f"Applying Triton targeting: {list(triton_targeting.keys())}")
        return triton_targeting

    def create_media_buy(
        self,
        request: AdapterCreateRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict[str, Any]] | None = None,
    ) -> AdapterCreateResult:
        """Creates a new Campaign and Flights in the Triton TAP API."""
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
            f"TritonDigital.create_media_buy for principal '{self.principal.name}' (Triton advertiser ID: {self.advertiser_id})",
        )

        # Validate targeting from MediaPackage objects (targeting_overlay is populated from request)
        unsupported_features = []
        for package in packages:
            if package.targeting_overlay:
                features = self._validate_targeting(package.targeting_overlay)
                if features:
                    unsupported_features.extend(features)

        if unsupported_features:
            error_msg = f"Unsupported targeting features for Triton Digital: {'; '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            raise AdCPCapabilityNotSupportedError(details=CapabilityRefusalDetails(rejected_value=unsupported_features))

        # Generate a media buy ID
        media_buy_id = (
            f"triton_{request.po_number}" if request.po_number else f"triton_{int(datetime.now(UTC).timestamp())}"
        )

        # Calculate total budget using pricing_info if available
        total_budget = 0.0
        for p in packages:
            total_budget += resolve_package_rate(p, package_pricing_info) * p.impressions / 1000

        # Create campaign in Triton
        campaign_payload: dict[str, JsonValue] = {
            "advertiserId": self.advertiser_id,
            "name": f"AdCP Campaign {media_buy_id}",
            "startDate": start_time.date().isoformat(),
            "endDate": end_time.date().isoformat(),
            "totalBudget": total_budget,
            "active": True,
        }

        response = require_vendor(self._vendor, vendor="Triton Digital").call(
            "POST", "/campaigns", json=campaign_payload
        )
        campaign_data = response.json()
        campaign_id = campaign_data["id"]

        # Create flights for each package
        for package in packages:
            rate = resolve_package_rate(package, package_pricing_info)

            flight_payload = {
                "name": package.name,
                "campaignId": campaign_id,
                "type": "STANDARD",
                "goal": {"type": "IMPRESSIONS", "value": package.impressions},
                "rate": rate,  # Use pricing from pricing option or fallback
                "rateType": "CPM",
                "startDate": start_time.date().isoformat(),
                "endDate": end_time.date().isoformat(),
            }

            # Add targeting if provided (from package-level targeting_overlay per AdCP spec)
            if package.targeting_overlay:
                targeting = self._build_targeting(package.targeting_overlay)
                if targeting and "targeting" in targeting:
                    flight_payload["targeting"] = targeting["targeting"]
                if targeting and "stationIds" in targeting:
                    flight_payload["stationIds"] = targeting["stationIds"]

            require_vendor(self._vendor, vendor="Triton Digital").call("POST", "/flights", json=flight_payload)

        # Use the actual campaign ID from Triton
        media_buy_id = f"triton_{campaign_id}"

        return self._build_create_success(media_buy_id, packages)

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Uploads creatives and associates them with flights in a campaign."""
        self.log(f"TritonDigital.add_creative_assets for media buy '{media_buy_id}'")
        created_asset_statuses = []

        try:
            # Extract campaign ID from media_buy_id (format: triton_{campaign_id})
            campaign_id = media_buy_id.replace("triton_", "")

            # Get all flights for the campaign to map package names to flight IDs
            flights_response = require_vendor(self._vendor, vendor="Triton Digital").call(
                "GET", "/flights", params={"campaignId": campaign_id}
            )
            flights = flights_response.json()
            flight_map = {flight["name"]: flight["id"] for flight in flights}

            for asset in assets:
                if asset["format"] != "audio":
                    self.log(
                        f"Skipping asset {asset['creative_id']} with unsupported format for Triton: {asset['format']}"
                    )
                    continue

                creative_payload = {"name": asset["name"], "type": "AUDIO", "url": asset["media_url"]}

                creative_response = require_vendor(self._vendor, vendor="Triton Digital").call(
                    "POST", "/creatives", json=creative_payload
                )
                creative_data = creative_response.json()
                creative_id: JsonValue = creative_data["id"]

                # Associate the creative with the assigned flights
                flight_ids_to_associate = [
                    flight_map[pkg_id] for pkg_id in asset.get("package_assignments", []) if pkg_id in flight_map
                ]

                if flight_ids_to_associate:
                    for flight_id in flight_ids_to_associate:
                        association_payload: dict[str, JsonValue] = {"creativeIds": [creative_id]}
                        require_vendor(self._vendor, vendor="Triton Digital").call(
                            "PUT", f"/flights/{flight_id}", json=association_payload
                        )

                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

        except OutboundError as e:
            self.log(f"Error creating Triton Creative: {e}")
            for asset in assets:
                if not any(s.creative_id == asset["creative_id"] for s in created_asset_statuses):
                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))

        return created_asset_statuses

    def associate_creatives(self, line_item_ids: list[str], platform_creative_ids: list[str]) -> list[dict[str, Any]]:
        """Associate already-uploaded creatives with flights.

        Note: Triton typically associates creatives during campaign creation.
        This method is a no-op for Triton.
        """
        self.log(
            "[yellow]Triton: Creative association happens during campaign creation (no separate step needed)[/yellow]"
        )

        return [
            {
                "line_item_id": line_item_id,
                "creative_id": creative_id,
                "status": "skipped",
                "message": "Triton associates creatives during campaign creation",
            }
            for line_item_id in line_item_ids
            for creative_id in platform_creative_ids
        ]

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Checks the status of a Campaign in the Triton TAP API."""
        self.log(f"TritonDigital.check_media_buy_status for media buy '{media_buy_id}'")

        try:
            # Extract campaign ID from media_buy_id
            campaign_id = media_buy_id.replace("triton_", "")

            response = require_vendor(self._vendor, vendor="Triton Digital").call("GET", f"/campaigns/{campaign_id}")
            campaign_data = response.json()

            # Map Triton status to our status
            status = "active" if campaign_data.get("active", False) else "paused"

            # Check if campaign is completed based on end date
            end_date = datetime.fromisoformat(campaign_data["endDate"])
            if end_date < today:
                status = "completed"

            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)

        except OutboundError as e:
            self.log(f"Error checking Triton Campaign status: {e}")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Runs and parses a delivery report from the Triton TAP API."""
        self.log(
            f"TritonDigital.get_media_buy_delivery for principal '{self.principal.name}' and media buy '{media_buy_id}'",
        )
        self.log(f"Date range: {date_range.start} to {date_range.end}")

        report_payload: dict[str, JsonValue] = {
            "reportType": "FLIGHT",
            "startDate": date_range.start.isoformat(),
            "endDate": date_range.end.isoformat(),
            "filters": {"campaigns": [media_buy_id]},
            "columns": ["flightName", "impressions", "totalRevenue"],
        }

        try:
            response = require_vendor(self._vendor, vendor="Triton Digital").call(
                "POST", "/reports", json=report_payload
            )
            report_job = response.json()
            job_id = report_job["id"]

            import time

            for _ in range(10):  # Poll for up to 5 seconds
                status_response = require_vendor(self._vendor, vendor="Triton Digital").call(
                    "GET", f"/reports/{job_id}"
                )
                status_data = status_response.json()
                if status_data["status"] == "COMPLETED":
                    report_url = status_data["url"]
                    break
                time.sleep(0.5)
            else:
                raise AdCPAdapterError()

            # A VENDOR-RETURNED url with no auth — one of the two sites this
            # migration genuinely secures rather than merely tidies.
            report_response = send(report_url, method="GET", timeout=30.0, max_attempts=1)

            import csv
            import io

            report_reader = csv.reader(io.StringIO(report_response.text))
            header = next(report_reader)
            col_map = {col: i for i, col in enumerate(header)}

            total_impressions = 0
            total_spend = 0.0
            by_package = []

            for row in report_reader:
                impressions = int(row[col_map["impressions"]])
                spend = float(row[col_map["totalRevenue"]])
                package_name = row[col_map["flightName"]]

                total_impressions += impressions
                total_spend += spend

                by_package.append(AdapterPackageDelivery(package_id=package_name, impressions=impressions, spend=spend))

            return AdapterGetMediaBuyDeliveryResponse(
                media_buy_id=media_buy_id,
                reporting_period=date_range,
                totals=DeliveryTotals(
                    impressions=total_impressions,
                    spend=total_spend,
                    clicks=0,
                    ctr=0.0,
                    completed_views=0,
                    completion_rate=0.0,
                ),
                by_package=by_package,
                currency="USD",
            )

        except OutboundError as e:
            self.log(f"Error getting delivery report from Triton: {e}")
            raise

    def update_media_buy(
        self,
        media_buy_id: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> AdapterUpdateResult:
        """Updates a media buy in Triton Digital using standardized actions."""
        self.log(f"TritonDigital.update_media_buy for {media_buy_id} with action {action}")

        require_supported_update_action(action)

        try:
            campaign_id = media_buy_id.replace("triton_", "")

            if action in ["pause_media_buy", "resume_media_buy"]:
                # Update campaign status
                update_payload: dict[str, Any] = {"active": action == "resume_media_buy"}
                require_vendor(self._vendor, vendor="Triton Digital").call(
                    "PUT", f"/campaigns/{campaign_id}", json=update_payload
                )

            elif action in ["pause_package", "resume_package"] and package_id:
                # Get flight ID by name
                flights_response = require_vendor(self._vendor, vendor="Triton Digital").call(
                    "GET", "/flights", params={"campaignId": campaign_id}
                )
                flights = flights_response.json()

                flight = next((f for f in flights if f["name"] == package_id), None)
                if not flight:
                    raise AdCPPackageNotFoundError(details=EntityRefDetails(package_id=package_id))

                # Update flight status
                is_resume = action == "resume_package"
                flight_update_payload: dict[str, Any] = {"active": is_resume}
                require_vendor(self._vendor, vendor="Triton Digital").call(
                    "PUT", f"/flights/{flight['id']}", json=flight_update_payload
                )

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
                # Get flight and update goal
                flights_response = require_vendor(self._vendor, vendor="Triton Digital").call(
                    "GET", "/flights", params={"campaignId": campaign_id}
                )
                flights = flights_response.json()

                flight = next((f for f in flights if f["name"] == package_id), None)
                if not flight:
                    raise AdCPPackageNotFoundError(details=EntityRefDetails(package_id=package_id))

                # Calculate impressions based on action
                if action == "update_package_budget":
                    # Get current CPM from flight
                    cpm = flight.get("rate", 25.0)  # Default to $25 CPM
                    new_impressions = int((budget / cpm) * 1000)
                else:  # update_package_impressions
                    new_impressions = budget  # budget param contains impressions

                goal_update_payload: dict[str, Any] = {"goal": {"type": "IMPRESSIONS", "value": new_impressions}}
                require_vendor(self._vendor, vendor="Triton Digital").call(
                    "PUT", f"/flights/{flight['id']}", json=goal_update_payload
                )

            return AdapterUpdateResult(
                media_buy_id=media_buy_id,
                affected_packages=[],  # List of package_ids affected by update
            )

        except OutboundError as e:
            self.log(f"Error updating Triton campaign/flight: {e}")
            from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error

            # The vendor's URL/response body is an upstream API response from a
            # seller-internal integration; AdCP 3.1.1 transport-errors.mdx
            # § Security Considerations forbids it on the buyer wire. The mapper
            # keeps it off: it names only the OperatorEndpoint role and chains the
            # OutboundError as ``__cause__`` for the log, never the buyer envelope.
            raise_mapped_outbound_error(
                e, provenance=OperatorEndpoint("Triton Digital"), logger=logging.getLogger(__name__)
            )

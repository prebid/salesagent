import json
from datetime import datetime, timedelta
from typing import Any

import requests

from src.adapters.base import AdServerAdapter, CreativeEngineAdapter
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.core.schemas import *


class TritonDigital(AdServerAdapter):
    """
    Adapter for interacting with the Triton Digital TAP API.
    """

    adapter_name = "triton"

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # Get Triton-specific principal ID
        self.advertiser_id = self.principal.get_adapter_id("triton")
        if not self.advertiser_id:
            raise ValueError(f"Principal {principal.principal_id} does not have a Triton advertiser ID")

        # Get Triton configuration
        self.base_url = self.config.get("base_url", "https://tap-api.tritondigital.com/v1")
        self.auth_token = self.config.get("auth_token")

        if self.dry_run:
            self.log("Running in dry-run mode - Triton API calls will be simulated", dry_run_prefix=False)
        elif not self.auth_token:
            raise ValueError("Triton Digital config is missing 'auth_token'")
        else:
            self.headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}

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
        if targeting_overlay.device_type_any_of:
            for device in targeting_overlay.device_type_any_of:
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

        # Geographic targeting (audio market focused)
        targeting_obj = {}
        if targeting_overlay.geo_country_any_of:
            targeting_obj["countries"] = targeting_overlay.geo_country_any_of
        if targeting_overlay.geo_region_any_of:
            targeting_obj["states"] = targeting_overlay.geo_region_any_of
        if targeting_overlay.geo_metro_any_of:
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
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict[str, Any]] | None = None,
    ) -> CreateMediaBuyResponse:
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
            dry_run_prefix=False,
        )

        # Validate targeting
        unsupported_features = self._validate_targeting(request.targeting_overlay)
        if unsupported_features:
            from src.core.schemas import Error

            error_msg = f"Unsupported targeting features for Triton Digital: {'; '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            return CreateMediaBuyResponse(
                buyer_ref=request.buyer_ref or "unknown",
                errors=[Error(code="unsupported_targeting", message=error_msg)],
            )

        # Generate a media buy ID
        media_buy_id = (
            f"triton_{request.po_number}" if request.po_number else f"triton_{int(datetime.now().timestamp())}"
        )

        # Calculate total budget
        total_budget = sum((p.cpm * p.impressions / 1000) for p in packages)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/campaigns")
            self.log("  Campaign Payload: {")
            self.log(f"    'advertiserId': '{self.advertiser_id}',")
            self.log(f"    'name': 'AdCP Campaign {media_buy_id}',")
            self.log(f"    'startDate': '{start_time.date().isoformat()}',")
            self.log(f"    'endDate': '{end_time.date().isoformat()}',")
            self.log(f"    'totalBudget': {total_budget:.2f},")
            self.log("    'active': true")
            self.log("  }")

            # Log flight creation for each package
            for package in packages:
                self.log(f"Would call: POST {self.base_url}/flights")
                self.log("  Flight Payload: {")
                self.log(f"    'name': '{package.name}',")
                self.log(f"    'campaignId': '{media_buy_id}',")
                self.log("    'type': 'STANDARD',")
                self.log("    'goal': {")
                self.log("      'type': 'IMPRESSIONS',")
                self.log(f"      'value': {package.impressions}")
                self.log("    },")
                self.log(f"    'rate': {package.cpm},")
                self.log("    'rateType': 'CPM',")
                self.log(f"    'startDate': '{start_time.date().isoformat()}',")
                self.log(f"    'endDate': '{end_time.date().isoformat()}'")

                # Add targeting if provided
                if request.targeting_overlay:
                    targeting = self._build_targeting(request.targeting_overlay)
                    if targeting:
                        self.log(f"    'targeting': {json.dumps(targeting, indent=6)}")

                self.log("  }")
        else:
            # Create campaign in Triton
            campaign_payload = {
                "advertiserId": self.advertiser_id,
                "name": f"AdCP Campaign {media_buy_id}",
                "startDate": start_time.date().isoformat(),
                "endDate": end_time.date().isoformat(),
                "totalBudget": total_budget,
                "active": True,
            }

            response = requests.post(f"{self.base_url}/campaigns", headers=self.headers, json=campaign_payload)
            response.raise_for_status()
            campaign_data = response.json()
            campaign_id = campaign_data["id"]

            # Create flights for each package and track flight IDs
            package_responses = []
            for package in packages:
                flight_payload = {
                    "name": package.name,
                    "campaignId": campaign_id,
                    "type": "STANDARD",
                    "goal": {"type": "IMPRESSIONS", "value": package.impressions},
                    "rate": package.cpm,
                    "rateType": "CPM",
                    "startDate": start_time.date().isoformat(),
                    "endDate": end_time.date().isoformat(),
                }

                # Add targeting if provided
                if request.targeting_overlay:
                    targeting = self._build_targeting(request.targeting_overlay)
                    if targeting and "targeting" in targeting:
                        flight_payload["targeting"] = targeting["targeting"]
                    if targeting and "stationIds" in targeting:
                        flight_payload["stationIds"] = targeting["stationIds"]

                flight_response = requests.post(f"{self.base_url}/flights", headers=self.headers, json=flight_payload)
                flight_response.raise_for_status()
                flight_data = flight_response.json()
                flight_id = flight_data.get("id")

                # Build package response with package_id and platform_flight_id
                package_responses.append(
                    {
                        "package_id": package.package_id,
                        "platform_line_item_id": str(flight_id) if flight_id else None,
                    }
                )

            # Use the actual campaign ID from Triton
            media_buy_id = f"triton_{campaign_id}"

        # For dry_run, build package responses without flight IDs
        if self.dry_run:
            package_responses = []
            for package in packages:
                package_responses.append(
                    {
                        "package_id": package.package_id,
                    }
                )

        return CreateMediaBuyResponse(
            buyer_ref=request.buyer_ref or "unknown",
            media_buy_id=media_buy_id,
            creative_deadline=datetime.now() + timedelta(days=2),
            packages=package_responses,
            errors=[],
        )

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Uploads creatives and associates them with flights in a campaign."""
        self.log(f"TritonDigital.add_creative_assets for media buy '{media_buy_id}'", dry_run_prefix=False)
        created_asset_statuses = []

        if self.dry_run:
            for asset in assets:
                if asset["format"] != "audio":
                    self.log(f"Skipping asset {asset['creative_id']} - Triton only supports audio formats")
                    continue

                self.log(f"Would create creative: {asset['name']}")
                self.log(f"Would call: POST {self.base_url}/creatives")
                self.log("  Creative Payload: {")
                self.log(f"    'name': '{asset['name']}',")
                self.log("    'type': 'AUDIO',")
                self.log(f"    'url': '{asset['media_url']}'")
                self.log("  }")
                self.log(f"Would associate creative with flights for packages: {asset.get('package_assignments', [])}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))
        else:
            try:
                # Extract campaign ID from media_buy_id (format: triton_{campaign_id})
                campaign_id = media_buy_id.replace("triton_", "")

                # Get all flights for the campaign to map package names to flight IDs
                flights_response = requests.get(
                    f"{self.base_url}/flights", headers=self.headers, params={"campaignId": campaign_id}
                )
                flights_response.raise_for_status()
                flights = flights_response.json()
                flight_map = {flight["name"]: flight["id"] for flight in flights}

                for asset in assets:
                    if asset["format"] != "audio":
                        self.log(
                            f"Skipping asset {asset['creative_id']} with unsupported format for Triton: {asset['format']}"
                        )
                        continue

                    creative_payload = {"name": asset["name"], "type": "AUDIO", "url": asset["media_url"]}

                    creative_response = requests.post(
                        f"{self.base_url}/creatives", headers=self.headers, json=creative_payload
                    )
                    creative_response.raise_for_status()
                    creative_data = creative_response.json()
                    creative_id = creative_data["id"]

                    # Associate the creative with the assigned flights
                    flight_ids_to_associate = [
                        flight_map[pkg_id] for pkg_id in asset.get("package_assignments", []) if pkg_id in flight_map
                    ]

                    if flight_ids_to_associate:
                        for flight_id in flight_ids_to_associate:
                            association_payload = {"creativeIds": [creative_id]}
                            assoc_response = requests.put(
                                f"{self.base_url}/flights/{flight_id}", headers=self.headers, json=association_payload
                            )
                            assoc_response.raise_for_status()

                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

            except requests.exceptions.RequestException as e:
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
        self.log(f"TritonDigital.check_media_buy_status for media buy '{media_buy_id}'", dry_run_prefix=False)

        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaigns/{media_buy_id}")
            self.log("Would check campaign active status and dates")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        else:
            try:
                # Extract campaign ID from media_buy_id
                campaign_id = media_buy_id.replace("triton_", "")

                response = requests.get(f"{self.base_url}/campaigns/{campaign_id}", headers=self.headers)
                response.raise_for_status()
                campaign_data = response.json()

                # Map Triton status to our status
                status = "active" if campaign_data.get("active", False) else "paused"

                # Check if campaign is completed based on end date
                end_date = datetime.fromisoformat(campaign_data["endDate"])
                if end_date < today:
                    status = "completed"

                return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status=status)

            except requests.exceptions.RequestException as e:
                self.log(f"Error checking Triton Campaign status: {e}")
                return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="unknown")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Runs and parses a delivery report from the Triton TAP API."""
        self.log(
            f"TritonDigital.get_media_buy_delivery for principal '{self.principal.name}' and media buy '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log(f"Date range: {date_range.start} to {date_range.end}", dry_run_prefix=False)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/reports")
            self.log("  Report Request: {")
            self.log("    'reportType': 'FLIGHT',")
            self.log(f"    'startDate': '{date_range.start.isoformat()}',")
            self.log(f"    'endDate': '{date_range.end.isoformat()}',")
            self.log(f"    'filters': {{'campaigns': ['{media_buy_id}']}},")
            self.log("    'columns': ['flightName', 'impressions', 'totalRevenue']")
            self.log("  }")
            self.log("Would poll for report completion and download results")

            # Simulate response based on campaign progress
            days_elapsed = (today.date() - date_range.start).days
            progress_factor = min(days_elapsed / 14, 1.0)  # Assume 14-day campaigns

            # Calculate simulated delivery for audio campaigns
            impressions = int(300000 * progress_factor * 0.92)  # 92% delivery rate for audio
            spend = impressions * 25 / 1000  # $25 CPM for audio

            self.log(f"Would return: {impressions:,} impressions, ${spend:,.2f} spend")

            return AdapterGetMediaBuyDeliveryResponse(
                media_buy_id=media_buy_id,
                reporting_period=date_range,
                totals=DeliveryTotals(
                    impressions=impressions, spend=spend, clicks=0, ctr=0.0, video_completions=0, completion_rate=0.0
                ),
                by_package=[],
                currency="USD",
            )
        else:
            report_payload = {
                "reportType": "FLIGHT",
                "startDate": date_range.start.isoformat(),
                "endDate": date_range.end.isoformat(),
                "filters": {"campaigns": [media_buy_id]},
                "columns": ["flightName", "impressions", "totalRevenue"],
            }

            try:
                response = requests.post(f"{self.base_url}/reports", headers=self.headers, json=report_payload)
                response.raise_for_status()
                report_job = response.json()
                job_id = report_job["id"]

                import time

                for _ in range(10):  # Poll for up to 5 seconds
                    status_response = requests.get(f"{self.base_url}/reports/{job_id}", headers=self.headers)
                    status_response.raise_for_status()
                    status_data = status_response.json()
                    if status_data["status"] == "COMPLETED":
                        report_url = status_data["url"]
                        break
                    time.sleep(0.5)
                else:
                    raise Exception("Triton report did not complete in time.")

                report_response = requests.get(report_url)
                report_response.raise_for_status()

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

                    by_package.append(PackageDelivery(package_id=package_name, impressions=impressions, spend=spend))

                return AdapterGetMediaBuyDeliveryResponse(
                    media_buy_id=media_buy_id,
                    reporting_period=date_range,
                    totals=DeliveryTotals(
                        impressions=total_impressions,
                        spend=total_spend,
                        clicks=0,
                        ctr=0.0,
                        video_completions=0,
                        completion_rate=0.0,
                    ),
                    by_package=by_package,
                    currency="USD",
                )

            except requests.exceptions.RequestException as e:
                self.log(f"Error getting delivery report from Triton: {e}")
                raise

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        """Updates performance indices for packages in Triton."""
        self.log(
            f"TritonDigital.update_media_buy_performance_index for media buy '{media_buy_id}'", dry_run_prefix=False
        )

        if self.dry_run:
            self.log("Performance index updates:")
            for perf in package_performance:
                self.log(f"  Package {perf.package_id}: index={perf.performance_index:.2f}")
            self.log("Would adjust flight targeting or budget allocation based on performance")
            self.log("Note: Triton TAP API may not directly support performance index updates")
            return True
        else:
            # Triton doesn't have a direct performance index API
            # In production, might update flight budgets or pause poor performers
            self.log("Triton does not directly support performance index updates. Custom implementation needed.")
            return True

    def update_media_buy(
        self,
        media_buy_id: str,
        buyer_ref: str,
        action: str,
        package_id: str | None,
        budget: int | None,
        today: datetime,
    ) -> UpdateMediaBuyResponse:
        """Updates a media buy in Triton Digital using standardized actions."""
        from src.core.schemas import Error

        self.log(f"TritonDigital.update_media_buy for {media_buy_id} with action {action}", dry_run_prefix=False)

        if action not in REQUIRED_UPDATE_ACTIONS:
            return UpdateMediaBuyResponse(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                errors=[
                    Error(
                        code="unsupported_action",
                        message=f"Action '{action}' not supported. Supported actions: {REQUIRED_UPDATE_ACTIONS}",
                    )
                ],
            )

        if self.dry_run:
            campaign_id = media_buy_id.replace("triton_", "")

            if action == "pause_media_buy":
                self.log(f"Would pause campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/campaigns/{campaign_id}")
                self.log("  Payload: {'active': false}")
            elif action == "resume_media_buy":
                self.log(f"Would resume campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/campaigns/{campaign_id}")
                self.log("  Payload: {'active': true}")
            elif action == "pause_package" and package_id:
                self.log(f"Would pause flight '{package_id}' in campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/flights/{package_id}")
                self.log("  Payload: {'active': false}")
            elif action == "resume_package" and package_id:
                self.log(f"Would resume flight '{package_id}' in campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/flights/{package_id}")
                self.log("  Payload: {'active': true}")
            elif (
                action in ["update_package_budget", "update_package_impressions"] and package_id and budget is not None
            ):
                if action == "update_package_budget":
                    self.log(f"Would update budget for flight '{package_id}' to ${budget}")
                    new_impressions = int((budget / 25.0) * 1000)  # Assuming $25 CPM for audio
                else:
                    self.log(f"Would update impressions for flight '{package_id}' to {budget}")
                    new_impressions = budget
                self.log(f"Would call: PUT {self.base_url}/flights/{package_id}")
                self.log(f"  Payload: {{'goal': {{'type': 'IMPRESSIONS', 'value': {new_impressions}}}}}")

            return UpdateMediaBuyResponse(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                implementation_date=today,
            )
        else:
            try:
                campaign_id = media_buy_id.replace("triton_", "")

                if action in ["pause_media_buy", "resume_media_buy"]:
                    # Update campaign status
                    update_payload = {"active": action == "resume_media_buy"}
                    response = requests.put(
                        f"{self.base_url}/campaigns/{campaign_id}", headers=self.headers, json=update_payload
                    )
                    response.raise_for_status()

                elif action in ["pause_package", "resume_package"] and package_id:
                    # Get flight ID by name
                    flights_response = requests.get(
                        f"{self.base_url}/flights", headers=self.headers, params={"campaignId": campaign_id}
                    )
                    flights_response.raise_for_status()
                    flights = flights_response.json()

                    flight = next((f for f in flights if f["name"] == package_id), None)
                    if not flight:
                        return UpdateMediaBuyResponse(
                            media_buy_id=media_buy_id,
                            buyer_ref=buyer_ref,
                            errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found")],
                        )

                    # Update flight status
                    update_payload = {"active": action == "resume_package"}
                    response = requests.put(
                        f"{self.base_url}/flights/{flight['id']}", headers=self.headers, json=update_payload
                    )
                    response.raise_for_status()

                elif (
                    action in ["update_package_budget", "update_package_impressions"]
                    and package_id
                    and budget is not None
                ):
                    # Get flight and update goal
                    flights_response = requests.get(
                        f"{self.base_url}/flights", headers=self.headers, params={"campaignId": campaign_id}
                    )
                    flights_response.raise_for_status()
                    flights = flights_response.json()

                    flight = next((f for f in flights if f["name"] == package_id), None)
                    if not flight:
                        return UpdateMediaBuyResponse(
                            media_buy_id=media_buy_id,
                            buyer_ref=buyer_ref,
                            errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found")],
                        )

                    # Calculate impressions based on action
                    if action == "update_package_budget":
                        # Get current CPM from flight
                        cpm = flight.get("rate", 25.0)  # Default to $25 CPM
                        new_impressions = int((budget / cpm) * 1000)
                    else:  # update_package_impressions
                        new_impressions = budget  # budget param contains impressions

                    update_payload = {"goal": {"type": "IMPRESSIONS", "value": new_impressions}}
                    response = requests.put(
                        f"{self.base_url}/flights/{flight['id']}", headers=self.headers, json=update_payload
                    )
                    response.raise_for_status()

                return UpdateMediaBuyResponse(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    implementation_date=today,
                )

            except requests.exceptions.RequestException as e:
                self.log(f"Error updating Triton campaign/flight: {e}")
                return UpdateMediaBuyResponse(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    errors=[Error(code="api_error", message=str(e))],
                )

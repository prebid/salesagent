import json
from datetime import datetime, timedelta
from typing import Any

import requests

from src.adapters.base import AdServerAdapter, CreativeEngineAdapter
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.core.schemas import *


class Kevel(AdServerAdapter):
    """
    Adapter for interacting with the Kevel Management API.
    """

    adapter_name = "kevel"

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # Get Kevel-specific principal ID
        self.advertiser_id = self.principal.get_adapter_id("kevel")
        if not self.advertiser_id:
            raise ValueError(f"Principal {principal.principal_id} does not have a Kevel advertiser ID")

        # Get Kevel configuration
        self.network_id = self.config.get("network_id")
        self.api_key = self.config.get("api_key")
        self.base_url = "https://api.kevel.co/v1"

        # Feature flags
        self.userdb_enabled = self.config.get("userdb_enabled", False)
        self.frequency_capping_enabled = self.config.get("frequency_capping_enabled", False)

        if self.dry_run:
            self.log("Running in dry-run mode - Kevel API calls will be simulated", dry_run_prefix=False)
        elif not self.network_id or not self.api_key:
            raise ValueError("Kevel config is missing 'network_id' or 'api_key'")
        else:
            self.headers = {"X-Adzerk-ApiKey": self.api_key, "Content-Type": "application/json"}

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
        if targeting_overlay.device_type_any_of:
            for device in targeting_overlay.device_type_any_of:
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

        # Geographic targeting
        geo = {}
        if targeting_overlay.geo_country_any_of:
            geo["countries"] = targeting_overlay.geo_country_any_of
        if targeting_overlay.geo_region_any_of:
            geo["regions"] = targeting_overlay.geo_region_any_of
        if targeting_overlay.geo_metro_any_of:
            # Convert string metro codes to integers
            geo["metros"] = [int(m) for m in targeting_overlay.geo_metro_any_of]
        if targeting_overlay.geo_city_any_of:
            geo["cities"] = targeting_overlay.geo_city_any_of

        if geo:
            kevel_targeting["geo"] = geo

        # Keywords
        if targeting_overlay.keywords_any_of:
            kevel_targeting["keywords"] = targeting_overlay.keywords_any_of

        # Device targeting (map to Kevel format)
        if targeting_overlay.device_type_any_of:
            # Kevel uses strings for device targeting
            devices = []
            for device in targeting_overlay.device_type_any_of:
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

        # AEE signal integration via CustomTargeting (managed-only)
        if targeting_overlay.key_value_pairs:
            self.log("[bold cyan]Adding AEE signals to Kevel CustomTargeting[/bold cyan]")
            aee_expressions = []
            for key, value in targeting_overlay.key_value_pairs.items():
                # Convert key-value pairs to Kevel CustomTargeting expressions
                # e.g., {"aee_segment": "high_value"} becomes "$user.aee_segment CONTAINS \"high_value\""
                expression = f'$user.{key} CONTAINS "{value}"'
                aee_expressions.append(expression)
                self.log(f"  {expression}")

            # Combine with existing CustomTargeting if any
            if "CustomTargeting" in kevel_targeting:
                kevel_targeting["CustomTargeting"] = (
                    f"{kevel_targeting['CustomTargeting']} AND ({' AND '.join(aee_expressions)})"
                )
            else:
                kevel_targeting["CustomTargeting"] = " AND ".join(aee_expressions)

        self.log(f"Applying Kevel targeting: {list(kevel_targeting.keys())}")
        return kevel_targeting

    def create_media_buy(
        self, request: CreateMediaBuyRequest, packages: list[MediaPackage], start_time: datetime, end_time: datetime
    ) -> CreateMediaBuyResponse:
        """Creates a new Campaign and associated Flights in Kevel."""
        # Log operation
        self.audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=self.advertiser_id,
            success=True,
            details={"po_number": request.po_number, "flight_dates": f"{start_time.date()} to {end_time.date()}"},
        )

        self.log(
            f"Kevel.create_media_buy for principal '{self.principal.name}' (Kevel advertiser ID: {self.advertiser_id})",
            dry_run_prefix=False,
        )

        # Validate targeting
        unsupported_features = self._validate_targeting(request.targeting_overlay)
        if unsupported_features:
            from src.core.schemas import Error

            error_msg = f"Unsupported targeting features for Kevel: {'; '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            return CreateMediaBuyResponse(
                buyer_ref=request.buyer_ref, errors=[Error(code="unsupported_targeting", message=error_msg)]
            )

        # Generate a media buy ID
        media_buy_id = f"kevel_{request.po_number}" if request.po_number else f"kevel_{int(datetime.now().timestamp())}"

        # Calculate total budget
        total_budget = sum((p.cpm * p.impressions / 1000) for p in packages)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/campaign")
            self.log("  Campaign Payload: {")
            self.log(f"    'AdvertiserId': {self.advertiser_id},")
            self.log(f"    'Name': 'AdCP Campaign {media_buy_id}',")
            self.log(f"    'StartDate': '{start_time.isoformat()}',")
            self.log(f"    'EndDate': '{end_time.isoformat()}',")
            self.log(f"    'DailyBudget': {total_budget / ((end_time - start_time).days + 1):.2f},")
            self.log("    'IsActive': true")
            self.log("  }")

            # Log flight creation for each package
            for package in packages:
                self.log(f"Would call: POST {self.base_url}/flight")
                self.log("  Flight Payload: {")
                self.log(f"    'Name': '{package.name}',")
                self.log(f"    'CampaignId': '{media_buy_id}',")
                self.log("    'Priority': 5,")
                self.log("    'GoalType': 2,")  # Impressions goal
                self.log(f"    'Impressions': {package.impressions},")
                self.log(f"    'Price': {package.cpm},")  # Price is CPM in Kevel
                self.log(f"    'StartDate': '{start_time.isoformat()}',")
                self.log(f"    'EndDate': '{end_time.isoformat()}'")

                # Add targeting if provided
                if request.targeting_overlay:
                    targeting = self._build_targeting(request.targeting_overlay)
                    if targeting:
                        self.log(f"    'Targeting': {json.dumps(targeting, indent=6)}")

                    # Log frequency capping if enabled
                    if request.targeting_overlay.frequency_cap and self.frequency_capping_enabled:
                        freq_cap = request.targeting_overlay.frequency_cap
                        if freq_cap.scope == "package":
                            self.log("    'FreqCap': 1,  # Suppress after 1 impression")
                            self.log(
                                f"    'FreqCapDuration': {max(1, freq_cap.suppress_minutes // 60)},  # {freq_cap.suppress_minutes} minutes"
                            )
                            self.log("    'FreqCapType': 1  # per user")

                self.log("  }")
        else:
            # Create campaign in Kevel
            campaign_payload = {
                "AdvertiserId": int(self.advertiser_id),
                "Name": f"AdCP Campaign {media_buy_id}",
                "StartDate": start_time.isoformat(),
                "EndDate": end_time.isoformat(),
                "DailyBudget": total_budget / ((end_time - start_time).days + 1),
                "IsActive": True,
            }

            response = requests.post(f"{self.base_url}/campaign", headers=self.headers, json=campaign_payload)
            response.raise_for_status()
            campaign_data = response.json()
            campaign_id = campaign_data["Id"]
            self.audit_logger.log_success(f"Created Kevel Campaign ID: {campaign_id}")

            # Create flights for each package
            for package in packages:
                flight_payload = {
                    "Name": package.name,
                    "CampaignId": campaign_id,
                    "Priority": 5,  # Standard priority
                    "GoalType": 2,  # Impressions goal
                    "Impressions": package.impressions,
                    "Price": package.cpm,
                    "StartDate": start_time.isoformat(),
                    "EndDate": end_time.isoformat(),
                    "IsActive": True,
                }

                # Add targeting if provided
                if request.targeting_overlay:
                    targeting = self._build_targeting(request.targeting_overlay)
                    if targeting:
                        flight_payload.update(targeting)

                    # Add frequency capping if enabled (package level only)
                    if request.targeting_overlay.frequency_cap and self.frequency_capping_enabled:
                        freq_cap = request.targeting_overlay.frequency_cap
                        if freq_cap.scope == "package":
                            # Kevel's FreqCap = 1 impression
                            # FreqCapDuration in hours, convert from minutes
                            flight_payload["FreqCap"] = 1
                            flight_payload["FreqCapDuration"] = max(
                                1, freq_cap.suppress_minutes // 60
                            )  # Convert to hours, minimum 1
                            flight_payload["FreqCapType"] = 1  # 1 = per user (cookie-based)

                flight_response = requests.post(f"{self.base_url}/flight", headers=self.headers, json=flight_payload)
                flight_response.raise_for_status()

            # Use the actual campaign ID from Kevel
            media_buy_id = f"kevel_{campaign_id}"

        return CreateMediaBuyResponse(
            media_buy_id=media_buy_id,
            status="pending_activation",
            detail=f"Created Kevel campaign with {len(packages)} flight(s)",
            creative_deadline=datetime.now() + timedelta(days=2),
        )

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Creates new Creatives in Kevel and associates them with Flights."""
        self.log(f"Kevel.add_creative_assets for media buy '{media_buy_id}'", dry_run_prefix=False)
        created_asset_statuses = []

        if self.dry_run:
            for asset in assets:
                self.log(f"Would create creative: {asset['name']}")

                if asset["format"] == "custom" and asset.get("template_id"):
                    self.log(f"Would call: POST {self.base_url}/creative")
                    self.log("  Creative Payload: {")
                    self.log(f"    'Name': '{asset['name']}',")
                    self.log(f"    'TemplateId': {asset['template_id']},")
                    self.log(f"    'Data': {json.dumps(asset.get('template_data', {}))}")
                    self.log("  }")
                elif asset["format"] == "image":
                    self.log(f"Would call: POST {self.base_url}/creative")
                    self.log("  Creative Payload: {")
                    self.log(f"    'Name': '{asset['name']}',")
                    self.log(
                        f"    'Body': '<a href=\"{asset['click_url']}\" target=\"_blank\"><img src=\"{asset['media_url']}\"/></a>',"
                    )
                    self.log(f"    'Url': '{asset['click_url']}'")
                    self.log("  }")
                elif asset["format"] == "video":
                    self.log(f"Would call: POST {self.base_url}/creative")
                    self.log("  Creative Payload: {")
                    self.log(f"    'Name': '{asset['name']}',")
                    self.log(f"    'ThirdPartyUrl': '{asset['media_url']}'")
                    self.log("  }")

                self.log(f"Would associate creative with flights for packages: {asset.get('package_assignments', [])}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))
        else:
            try:
                # Get all flights for the campaign to map package names to flight IDs
                flights_response = requests.get(
                    f"{self.base_url}/flight", headers=self.headers, params={"campaignId": media_buy_id}
                )
                flights_response.raise_for_status()
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
                    creative_response = requests.post(
                        f"{self.base_url}/creative", headers=self.headers, json=creative_payload
                    )
                    creative_response.raise_for_status()
                    creative_data = creative_response.json()
                    creative_id = creative_data["Id"]

                    # Associate the creative with the assigned flights
                    flight_ids_to_associate = [
                        flight_map[pkg_id] for pkg_id in asset.get("package_assignments", []) if pkg_id in flight_map
                    ]

                    if flight_ids_to_associate:
                        for flight_id in flight_ids_to_associate:
                            ad_payload = {"CreativeId": creative_id, "FlightId": flight_id, "IsActive": True}
                            ad_response = requests.post(f"{self.base_url}/ad", headers=self.headers, json=ad_payload)
                            ad_response.raise_for_status()

                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

            except requests.exceptions.RequestException as e:
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
        self.log(f"Kevel.check_media_buy_status for media buy '{media_buy_id}'", dry_run_prefix=False)

        if self.dry_run:
            self.log(f"Would call: GET {self.base_url}/campaign/{media_buy_id}")
            self.log("Would check campaign IsActive status and flight statuses")
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")
        else:
            # In production, would query campaign status
            return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="active")

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Gets delivery data for a media buy from Kevel reporting."""
        self.log(
            f"Kevel.get_media_buy_delivery for principal '{self.principal.name}' and media buy '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log(f"Date range: {date_range.start_date} to {date_range.end_date}", dry_run_prefix=False)

        if self.dry_run:
            self.log(f"Would call: POST {self.base_url}/report/queue")
            self.log("  Report Request: {")
            self.log(f"    'StartDate': '{date_range.start_date.isoformat()}',")
            self.log(f"    'EndDate': '{date_range.end_date.isoformat()}',")
            self.log("    'GroupBy': ['day', 'campaign', 'flight'],")
            self.log(f"    'Filter': {{'CampaignId': '{media_buy_id}'}}")
            self.log("  }")

            # Simulate response based on campaign progress
            days_elapsed = (today.date() - date_range.start_date).days
            progress_factor = min(days_elapsed / 14, 1.0)  # Assume 14-day campaigns

            # Calculate simulated delivery
            impressions = int(500000 * progress_factor * 0.95)  # 95% delivery rate
            spend = impressions * 10 / 1000  # $10 CPM

            self.log(f"Would return: {impressions:,} impressions, ${spend:,.2f} spend")

            return AdapterGetMediaBuyDeliveryResponse(
                totals=DeliveryTotals(impressions=impressions, spend=spend), by_package=[]
            )
        else:
            # Queue a report in Kevel
            report_request = {
                "StartDate": date_range.start_date.isoformat(),
                "EndDate": date_range.end_date.isoformat(),
                "GroupBy": ["day", "campaign", "flight"],
                "Filter": {"CampaignId": media_buy_id},
            }

            response = requests.post(f"{self.base_url}/report/queue", headers=self.headers, json=report_request)
            response.raise_for_status()
            report_id = response.json()["Id"]

            # Poll for report completion (simplified - in production would need proper polling)
            import time

            time.sleep(1)

            # Get report results
            results_response = requests.get(f"{self.base_url}/report/{report_id}/results", headers=self.headers)
            results_response.raise_for_status()

            # Parse results and aggregate
            results = results_response.json()
            total_impressions = sum(row.get("Impressions", 0) for row in results.get("Records", []))
            total_revenue = sum(row.get("Revenue", 0) for row in results.get("Records", []))

            return AdapterGetMediaBuyDeliveryResponse(
                totals=DeliveryTotals(impressions=total_impressions, spend=total_revenue), by_package=[]
            )

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        """Updates performance indices for packages in Kevel."""
        self.log(f"Kevel.update_media_buy_performance_index for media buy '{media_buy_id}'", dry_run_prefix=False)

        if self.dry_run:
            self.log("Performance index updates:")
            for perf in package_performance:
                self.log(f"  Package {perf.package_id}: index={perf.performance_index:.2f}")
            self.log("Would adjust flight priorities based on performance:")
            for perf in package_performance:
                if perf.performance_index > 1.1:
                    self.log(f"  Would increase priority for {perf.package_id} (good performance)")
                elif perf.performance_index < 0.9:
                    self.log(f"  Would decrease priority for {perf.package_id} (poor performance)")
            return True
        else:
            # In production, would update flight priorities based on performance
            self.log("Kevel does not directly support performance index updates. Would need custom implementation.")
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
        """Updates a media buy in Kevel using standardized actions."""
        from src.core.schemas import Error

        self.log(f"Kevel.update_media_buy for {media_buy_id} with action {action}", dry_run_prefix=False)

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
            campaign_id = media_buy_id.replace("kevel_", "")

            if action == "pause_media_buy":
                self.log(f"Would pause campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/campaign/{campaign_id}")
                self.log("  Payload: {'IsActive': false}")
            elif action == "resume_media_buy":
                self.log(f"Would resume campaign {campaign_id}")
                self.log(f"Would call: PUT {self.base_url}/campaign/{campaign_id}")
                self.log("  Payload: {'IsActive': true}")
            elif action == "pause_package" and package_id:
                self.log(f"Would pause flight '{package_id}' in campaign {media_buy_id}")
                self.log(f"Would call: PUT {self.base_url}/flight/{package_id}")
                self.log("  Payload: {'IsActive': false}")
            elif action == "resume_package" and package_id:
                self.log(f"Would resume flight '{package_id}' in campaign {media_buy_id}")
                self.log(f"Would call: PUT {self.base_url}/flight/{package_id}")
                self.log("  Payload: {'IsActive': true}")
            elif (
                action in ["update_package_budget", "update_package_impressions"] and package_id and budget is not None
            ):
                if action == "update_package_budget":
                    self.log(f"Would update budget for flight '{package_id}' to ${budget}")
                    new_impressions = int((budget / 10.0) * 1000)  # Assuming $10 CPM
                else:
                    self.log(f"Would update impressions for flight '{package_id}' to {budget}")
                    new_impressions = budget
                self.log(f"Would call: PUT {self.base_url}/flight/{package_id}")
                self.log(f"  Payload: {{'Impressions': {new_impressions}}}")

            return UpdateMediaBuyResponse(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                implementation_date=today,
            )
        else:
            try:
                # Extract campaign ID
                campaign_id = media_buy_id.replace("kevel_", "")

                if action in ["pause_media_buy", "resume_media_buy"]:
                    # Update campaign status
                    update_payload = {"IsActive": action == "resume_media_buy"}
                    update_response = requests.put(
                        f"{self.base_url}/campaign/{campaign_id}", headers=self.headers, json=update_payload
                    )
                    update_response.raise_for_status()

                elif action in ["pause_package", "resume_package"] and package_id:
                    # Get flight ID by name
                    flights_response = requests.get(
                        f"{self.base_url}/flight", headers=self.headers, params={"campaignId": campaign_id}
                    )
                    flights_response.raise_for_status()
                    flights = flights_response.json().get("items", [])

                    flight = next((f for f in flights if f["Name"] == package_id), None)
                    if not flight:
                        return UpdateMediaBuyResponse(
                            media_buy_id=media_buy_id,
                            buyer_ref=buyer_ref,
                            errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found")],
                        )

                    # Update flight status
                    update_payload = {"IsActive": action == "resume_package"}
                    update_response = requests.put(
                        f"{self.base_url}/flight/{flight['Id']}", headers=self.headers, json=update_payload
                    )
                    update_response.raise_for_status()

                elif (
                    action in ["update_package_budget", "update_package_impressions"]
                    and package_id
                    and budget is not None
                ):
                    # Get flight ID by name
                    flights_response = requests.get(
                        f"{self.base_url}/flight", headers=self.headers, params={"campaignId": campaign_id}
                    )
                    flights_response.raise_for_status()
                    flights = flights_response.json().get("items", [])

                    flight = next((f for f in flights if f["Name"] == package_id), None)
                    if not flight:
                        return UpdateMediaBuyResponse(
                            media_buy_id=media_buy_id,
                            buyer_ref=buyer_ref,
                            errors=[Error(code="flight_not_found", message=f"Flight '{package_id}' not found")],
                        )

                    # Calculate impressions based on action
                    if action == "update_package_budget":
                        # Get current CPM from flight
                        cpm = flight.get("Price", 10.0)  # Default to $10 CPM
                        new_impressions = int((budget / cpm) * 1000)
                    else:  # update_package_impressions
                        new_impressions = budget  # budget param contains impressions

                    # Update flight impressions
                    update_payload = {"Impressions": new_impressions}
                    update_response = requests.put(
                        f"{self.base_url}/flight/{flight['Id']}", headers=self.headers, json=update_payload
                    )
                    update_response.raise_for_status()

                return UpdateMediaBuyResponse(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    implementation_date=today,
                )

            except requests.exceptions.RequestException as e:
                self.log(f"Error updating Kevel flight: {e}")
                return UpdateMediaBuyResponse(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    errors=[Error(code="api_error", message=str(e))],
                )

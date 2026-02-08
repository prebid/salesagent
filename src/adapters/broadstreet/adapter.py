"""Broadstreet Ads Adapter.

Full-featured adapter for Broadstreet Ads supporting:
- CPM and FLAT_RATE pricing
- HTML, static image, and text ad formats
- HITL workflows
- Inventory sync

Entity Mapping:
- AdCP Media Buy → Broadstreet Campaign
- AdCP Package → Broadstreet Placement (linked to Zone)
- AdCP Creative → Broadstreet Advertisement
- AdCP Product → Zone configuration (via implementation_config)
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from adcp.types.aliases import Package as ResponsePackage

from src.adapters.base import (
    AdapterCapabilities,
    AdServerAdapter,
    CreativeEngineAdapter,
    TargetingCapabilities,
)
from src.adapters.broadstreet.client import BroadstreetClient
from src.adapters.broadstreet.config_schema import parse_implementation_config
from src.adapters.broadstreet.managers import (
    BroadstreetAdvertisementManager,
    BroadstreetCampaignManager,
    BroadstreetInventoryManager,
    BroadstreetPlacementManager,
    BroadstreetWorkflowManager,
)
from src.adapters.broadstreet.schemas import BroadstreetConnectionConfig, BroadstreetProductConfig
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AffectedPackage,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    CreateMediaBuySuccess,
    DeliveryTotals,
    Error,
    MediaPackage,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyError,
    UpdateMediaBuyResponse,
    UpdateMediaBuySuccess,
)

logger = logging.getLogger(__name__)


class BroadstreetAdapter(AdServerAdapter):
    """Adapter for interacting with the Broadstreet Ads API.

    Broadstreet is a simple ad server focused on local and B2B publishers.
    It operates synchronously (no webhooks) with zone-based targeting.
    """

    adapter_name = "broadstreet"

    # Broadstreet specializes in display advertising
    default_channels = ["display"]

    # Schema and capabilities
    connection_config_class = BroadstreetConnectionConfig
    product_config_class = BroadstreetProductConfig
    capabilities = AdapterCapabilities(
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        inventory_entity_label="Zones",
        supports_custom_targeting=False,
        supports_geo_targeting=True,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=True,
    )

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        """Initialize the Broadstreet adapter.

        Args:
            config: Adapter configuration containing api_key, network_id, etc.
            principal: Principal (advertiser) making the request
            dry_run: Whether to simulate operations without making API calls
            creative_engine: Optional creative processing engine
            tenant_id: Tenant ID for multi-tenant context
        """
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)

        # Get Broadstreet-specific principal ID
        self.advertiser_id = self.principal.get_adapter_id("broadstreet")
        if not self.advertiser_id:
            # Fall back to default advertiser from config
            self.advertiser_id = self.config.get("default_advertiser_id")
            if not self.advertiser_id and not self.dry_run:
                raise ValueError(
                    f"Principal {principal.principal_id} does not have a Broadstreet advertiser ID "
                    "and no default_advertiser_id configured"
                )

        # Get Broadstreet configuration
        self.network_id = self.config.get("network_id")
        self.api_key = self.config.get("api_key")

        # Initialize client
        if self.dry_run:
            self.log("Running in dry-run mode - Broadstreet API calls will be simulated", dry_run_prefix=False)
            self.client = None
        elif not self.network_id or not self.api_key:
            raise ValueError("Broadstreet config is missing 'network_id' or 'api_key'")
        else:
            self.client = BroadstreetClient(
                access_token=self.api_key,
                network_id=self.network_id,
            )

        # Initialize managers
        self.campaign_manager = BroadstreetCampaignManager(
            client=self.client,
            advertiser_id=self.advertiser_id or "",
            dry_run=self.dry_run,
            log_func=self.log,
        )
        self.placement_manager = BroadstreetPlacementManager(
            client=self.client,
            advertiser_id=self.advertiser_id or "",
            dry_run=self.dry_run,
            log_func=self.log,
        )
        self.advertisement_manager = BroadstreetAdvertisementManager(
            client=self.client,
            advertiser_id=self.advertiser_id or "",
            dry_run=self.dry_run,
            log_func=self.log,
        )
        self.workflow_manager = BroadstreetWorkflowManager(
            tenant_id=self.tenant_id or "",
            principal=self.principal,
            audit_logger=self.audit_logger,
            log_func=self.log,
        )
        self.inventory_manager = BroadstreetInventoryManager(
            client=self.client,
            network_id=self.network_id or "",
            dry_run=self.dry_run,
            log_func=self.log,
        )

    def _extract_campaign_id(self, media_buy_id: str) -> str:
        """Extract Broadstreet campaign ID from media_buy_id.

        Args:
            media_buy_id: Media buy ID (format: "bs_<campaign_id>" or "mb_<id>")

        Returns:
            The extracted campaign ID

        Raises:
            ValueError: If media_buy_id format is invalid
        """
        if not media_buy_id:
            raise ValueError("media_buy_id cannot be empty")

        if media_buy_id.startswith("bs_"):
            return media_buy_id[3:]  # Remove "bs_" prefix
        elif media_buy_id.startswith("mb_"):
            # Legacy format or dry-run generated ID
            return media_buy_id[3:]
        else:
            # Assume it's already a raw campaign ID
            logger.warning(f"Unexpected media_buy_id format: {media_buy_id}, using as-is")
            return media_buy_id

    def _persist_advertisement_ids(self, media_buy_id: str, advertisement_ids: list[str]) -> None:
        """Persist Broadstreet advertisement IDs to package_config in the database.

        Stores the IDs so that update_media_buy can toggle their active state
        for pause/resume operations across requests.

        Args:
            media_buy_id: Media buy ID
            advertisement_ids: List of Broadstreet advertisement IDs
        """
        from sqlalchemy import select
        from sqlalchemy.orm import attributes

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaPackage as DBMediaPackage

        try:
            with get_db_session() as session:
                stmt = select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)
                packages = session.scalars(stmt).all()

                for pkg in packages:
                    existing_ids = pkg.package_config.get("broadstreet_advertisement_ids", [])
                    # Merge without duplicates
                    merged = list(set(existing_ids + advertisement_ids))
                    pkg.package_config["broadstreet_advertisement_ids"] = merged
                    attributes.flag_modified(pkg, "package_config")

                session.commit()
                self.log(f"Persisted {len(advertisement_ids)} Broadstreet ad IDs to {len(packages)} packages")
        except Exception as e:
            logger.error(f"Failed to persist advertisement IDs: {e}", exc_info=True)

    def _toggle_advertisements(self, advertisement_ids: list[str], active: bool) -> list[str]:
        """Toggle active state on Broadstreet advertisements.

        Args:
            advertisement_ids: Broadstreet advertisement IDs to toggle
            active: True to activate, False to deactivate

        Returns:
            List of advertisement IDs that failed to update
        """
        if not self.client or not self.advertiser_id:
            return []

        failed: list[str] = []
        active_value = 1 if active else 0
        action_verb = "Activating" if active else "Deactivating"
        advertiser_id = self.advertiser_id

        for ad_id in advertisement_ids:
            try:
                self.client.update_advertisement(
                    advertiser_id=advertiser_id,
                    advertisement_id=ad_id,
                    params={"active": active_value},
                )
                self.log(f"{action_verb} advertisement {ad_id}")
            except Exception as e:
                logger.error(f"Failed to update advertisement {ad_id}: {e}", exc_info=True)
                failed.append(ad_id)

        return failed

    def get_supported_pricing_models(self) -> set[str]:
        """Return supported pricing models.

        Broadstreet supports CPM and flat rate pricing.
        """
        return {"cpm", "flat_rate"}

    def get_targeting_capabilities(self) -> TargetingCapabilities:
        """Return targeting capabilities.

        Broadstreet has limited targeting - primarily zone-based.
        Geographic targeting may be available depending on configuration.
        """
        return TargetingCapabilities(
            geo_countries=True,
            geo_regions=False,
            nielsen_dma=False,
            eurostat_nuts2=False,
            us_zip=False,
            us_zip_plus_four=False,
            ca_fsa=False,
            ca_full=False,
            gb_outward=False,
            gb_full=False,
            de_plz=False,
            fr_code_postal=False,
            au_postcode=False,
        )

    def validate_product_config(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate product implementation config.

        Args:
            config: Product implementation_config

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            parsed = parse_implementation_config(config)

            # Check that at least one zone is configured
            if not parsed.get_zone_ids():
                return False, "No zones configured. Set targeted_zone_ids or zone_targeting."

            return True, None
        except Exception as e:
            return False, f"Invalid configuration: {e}"

    def create_media_buy(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        package_pricing_info: dict[str, dict] | None = None,
    ) -> CreateMediaBuyResponse:
        """Create a new media buy (campaign) in Broadstreet.

        Args:
            request: Full create media buy request
            packages: Simplified package models
            start_time: Campaign start time
            end_time: Campaign end time
            package_pricing_info: Optional validated pricing per package

        Returns:
            CreateMediaBuyResponse with media buy details
        """
        # Log operation
        self.audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=self.principal.name,
            principal_id=self.principal.principal_id,
            adapter_id=self.advertiser_id or "unknown",
            success=True,
            details={
                "po_number": request.po_number,
                "flight_dates": f"{start_time.date()} to {end_time.date()}",
            },
        )

        self.log(
            f"Broadstreet.create_media_buy for principal '{self.principal.name}' "
            f"(Broadstreet advertiser ID: {self.advertiser_id})",
            dry_run_prefix=False,
        )

        # Build products map from packages
        products_map: dict[str, dict[str, Any]] = {}
        for package in packages:
            if package.product_id:
                products_map[package.product_id] = {
                    "product_id": package.product_id,
                    "name": package.name,
                    "implementation_config": getattr(package, "implementation_config", None) or {},
                }

        # Validate zones are configured
        all_zone_ids: set[str] = set()
        for product_id, product_info in products_map.items():
            impl_config = parse_implementation_config(product_info.get("implementation_config"))
            zone_ids = impl_config.get_zone_ids()
            if not zone_ids:
                return CreateMediaBuyError(
                    errors=[
                        Error(
                            code="no_zones_configured",
                            message=f"Product {product_id} has no zones configured",
                            details={"product_id": product_id},
                        )
                    ],
                )
            all_zone_ids.update(zone_ids)

        self.log(f"Targeting zones: {sorted(all_zone_ids)}")

        # Determine automation mode from first product's config
        first_impl_config = parse_implementation_config(
            next(iter(products_map.values()), {}).get("implementation_config")
        )
        automation_mode = first_impl_config.automation_mode.lower()
        self.log(f"Automation mode: {automation_mode}")

        # Generate media buy ID
        media_buy_id = f"bs_{request.po_number or int(datetime.now().timestamp())}"

        # Handle manual mode - create workflow step instead of campaign
        if automation_mode == "manual":
            self.log("Manual mode - creating workflow step for human intervention")

            workflow_step_id = self.workflow_manager.create_manual_campaign_workflow_step(
                request=request,
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                media_buy_id=media_buy_id,
            )

            # Build package responses
            package_responses: list[ResponsePackage] = []
            for pkg in packages:
                package_responses.append(
                    ResponsePackage(
                        buyer_ref=pkg.buyer_ref or "unknown",
                        package_id=pkg.package_id,
                        paused=True,  # Paused until manual creation
                    )
                )

            # Calculate creative deadline (2 days from now)
            creative_deadline = datetime.now(UTC) + timedelta(days=2)

            return CreateMediaBuySuccess(
                buyer_ref=request.buyer_ref or "unknown",
                media_buy_id=media_buy_id,
                creative_deadline=creative_deadline,
                packages=package_responses,
                workflow_step_id=workflow_step_id,
            )

        # Build campaign name
        first_product_name = next(iter(products_map.values()), {}).get("name", "Campaign")
        campaign_name = self.campaign_manager.build_campaign_name(
            template="AdCP-{po_number}-{product_name}",
            po_number=request.po_number,
            product_name=first_product_name,
            advertiser_name=self.principal.name,
        )

        # Create campaign
        campaign_data = self.campaign_manager.create_campaign(
            name=campaign_name,
            start_date=start_time,
            end_date=end_time,
        )

        # Update media_buy_id with actual campaign ID
        if not self.dry_run:
            media_buy_id = f"bs_{campaign_data.get('id', media_buy_id)}"

        # Register packages with placement manager for tracking
        # Full placement creation happens when creatives are added
        for pkg in packages:
            self.placement_manager.register_package(
                media_buy_id=media_buy_id,
                package_id=pkg.package_id,
                product_id=pkg.product_id,
                impl_config=products_map.get(pkg.product_id or "", {}).get("implementation_config"),
            )

        # Build package responses
        package_responses = []
        for pkg in packages:
            package_responses.append(
                ResponsePackage(
                    buyer_ref=pkg.buyer_ref or "unknown",
                    package_id=pkg.package_id,
                    paused=False,
                )
            )

        # Calculate creative deadline (2 days from now)
        creative_deadline = datetime.now(UTC) + timedelta(days=2)

        # Handle confirmation_required mode - create campaign but require activation approval
        workflow_step_id = None
        if automation_mode == "confirmation_required":
            self.log("Confirmation required mode - creating activation workflow step")
            workflow_step_id = self.workflow_manager.create_activation_workflow_step(
                media_buy_id=media_buy_id,
                packages=packages,
            )

        response = CreateMediaBuySuccess(
            buyer_ref=request.buyer_ref or "unknown",
            media_buy_id=media_buy_id,
            creative_deadline=creative_deadline,
            packages=package_responses,
            workflow_step_id=workflow_step_id,
        )

        # Persist campaign ID so update_media_buy can reconstruct state from DB
        # Core layer (media_buy_create.py) stores this as package_config["platform_line_item_id"]
        campaign_id = self._extract_campaign_id(media_buy_id)
        object.__setattr__(response, "_platform_line_item_ids", {pkg.package_id: campaign_id for pkg in packages})

        return response

    def add_creative_assets(
        self,
        media_buy_id: str,
        assets: list[dict[str, Any]],
        today: datetime,
    ) -> list[AssetStatus]:
        """Add creative assets to a media buy.

        Creates advertisements in Broadstreet and links them to zones.

        Args:
            media_buy_id: Media buy (campaign) ID
            assets: List of creative asset data
            today: Current date for validation

        Returns:
            List of asset statuses
        """
        self.log(f"Broadstreet.add_creative_assets for media buy '{media_buy_id}'", dry_run_prefix=False)

        results: list[AssetStatus] = []

        # Use advertisement manager to create ads
        ad_infos = self.advertisement_manager.create_advertisements(media_buy_id, assets)

        for info in ad_infos:
            if info.status == "failed":
                results.append(AssetStatus(creative_id=info.creative_id, status="failed"))
            else:
                results.append(AssetStatus(creative_id=info.creative_id, status="approved"))

        # Get Broadstreet advertisement IDs for placement creation
        broadstreet_ad_ids = self.advertisement_manager.get_broadstreet_ids(media_buy_id)

        # Create placements linking ads to zones for each registered package
        campaign_id = self._extract_campaign_id(media_buy_id)
        for pkg_info in self.placement_manager.get_all_packages(media_buy_id):
            if broadstreet_ad_ids:
                self.placement_manager.create_placements(
                    campaign_id=campaign_id,
                    media_buy_id=media_buy_id,
                    package_id=pkg_info.package_id,
                    advertisement_ids=broadstreet_ad_ids,
                )

        # Persist Broadstreet advertisement IDs to package_config for cross-request access
        # (update_media_buy needs these IDs to toggle active state for pause/resume)
        if broadstreet_ad_ids and not self.dry_run:
            self._persist_advertisement_ids(media_buy_id, broadstreet_ad_ids)

        return results

    def associate_creatives(
        self,
        line_item_ids: list[str],
        platform_creative_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Associate already-uploaded creatives with placements.

        In Broadstreet, this creates placements linking ads to zones.

        Args:
            line_item_ids: Zone IDs (Broadstreet doesn't have line items)
            platform_creative_ids: Advertisement IDs

        Returns:
            List of association results
        """
        self.log(
            f"Broadstreet.associate_creatives: {len(platform_creative_ids)} creatives to {len(line_item_ids)} zones",
            dry_run_prefix=False,
        )

        results = []

        for zone_id in line_item_ids:
            for creative_id in platform_creative_ids:
                if self.dry_run:
                    self.log(f"Would associate creative {creative_id} with zone {zone_id}")
                    results.append(
                        {
                            "line_item_id": zone_id,
                            "creative_id": creative_id,
                            "status": "success",
                        }
                    )
                else:
                    # Note: Broadstreet placements require a campaign context
                    # This method may need to be called with campaign ID
                    self.log("[yellow]Broadstreet: Association requires campaign context[/yellow]")
                    results.append(
                        {
                            "line_item_id": zone_id,
                            "creative_id": creative_id,
                            "status": "skipped",
                            "message": "Broadstreet requires campaign context for placements",
                        }
                    )

        return results

    def check_media_buy_status(
        self,
        media_buy_id: str,
        today: datetime,
    ) -> CheckMediaBuyStatusResponse:
        """Check the status of a media buy.

        Args:
            media_buy_id: Media buy (campaign) ID
            today: Current date

        Returns:
            Status response
        """
        self.log(f"Broadstreet.check_media_buy_status for '{media_buy_id}'", dry_run_prefix=False)

        # Extract campaign ID from media_buy_id
        campaign_id = self._extract_campaign_id(media_buy_id)

        if self.dry_run:
            self.log(f"Would check status for campaign: {campaign_id}")
            return CheckMediaBuyStatusResponse(
                media_buy_id=media_buy_id,
                buyer_ref=media_buy_id,
                status="active",
            )

        # In production, would query campaign status from Broadstreet
        # For now, return active
        return CheckMediaBuyStatusResponse(
            media_buy_id=media_buy_id,
            buyer_ref=media_buy_id,
            status="active",
        )

    def get_media_buy_delivery(
        self,
        media_buy_id: str,
        date_range: ReportingPeriod,
        today: datetime,
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Get delivery data for a media buy.

        Broadstreet reporting is synchronous - no polling required.

        Args:
            media_buy_id: Media buy (campaign) ID
            date_range: Reporting date range
            today: Current date

        Returns:
            Delivery data response
        """
        self.log(
            f"Broadstreet.get_media_buy_delivery for '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log(f"Date range: {date_range.start} to {date_range.end}", dry_run_prefix=False)

        if self.dry_run:
            # Simulate delivery data
            from datetime import datetime as dt

            start_date = (
                dt.fromisoformat(date_range.start.replace("Z", "+00:00")).date()
                if isinstance(date_range.start, str)
                else date_range.start
            )
            days_elapsed = (today.date() - start_date).days
            progress_factor = min(days_elapsed / 14, 1.0)

            impressions = int(100000 * progress_factor * 0.95)
            spend = impressions * 10 / 1000  # $10 CPM

            self.log(f"Simulated delivery: {impressions:,} impressions, ${spend:,.2f} spend")

            return AdapterGetMediaBuyDeliveryResponse(
                media_buy_id=media_buy_id,
                reporting_period=date_range,
                totals=DeliveryTotals(
                    impressions=impressions,
                    spend=spend,
                    clicks=int(impressions * 0.002),  # 0.2% CTR
                    ctr=0.2,
                    video_completions=0,
                    completion_rate=0.0,
                ),
                by_package=[],
                currency="USD",
            )

        # In production, would query advertisement records
        # and aggregate across all ads in the campaign
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=date_range,
            totals=DeliveryTotals(
                impressions=0,
                spend=0,
                clicks=0,
                ctr=0.0,
                video_completions=0,
                completion_rate=0.0,
            ),
            by_package=[],
            currency="USD",
        )

    def update_media_buy_performance_index(
        self,
        media_buy_id: str,
        package_performance: list[PackagePerformance],
    ) -> bool:
        """Update performance index for packages.

        Broadstreet doesn't have a direct performance index feature.
        This is a no-op for now.

        Args:
            media_buy_id: Media buy ID
            package_performance: Performance data per package

        Returns:
            True (always succeeds as no-op)
        """
        self.log(
            f"Broadstreet.update_media_buy_performance_index for '{media_buy_id}'",
            dry_run_prefix=False,
        )
        self.log("[yellow]Broadstreet does not support performance index updates[/yellow]")
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
        """Update a media buy with a specific action.

        Reconstructs state from database (not in-memory caches) so operations
        work across requests. For pause/resume, toggles the active flag on
        Broadstreet advertisements via their API.

        Args:
            media_buy_id: Media buy (campaign) ID
            buyer_ref: Buyer reference
            action: Action to perform
            package_id: Package ID (for package-level actions)
            budget: New budget or impressions (for budget/impression updates)
            today: Current date

        Returns:
            Update response
        """
        from sqlalchemy import select
        from sqlalchemy.orm import attributes

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaPackage as DBMediaPackage

        self.log(
            f"Broadstreet.update_media_buy for '{media_buy_id}' with action '{action}'",
            dry_run_prefix=False,
        )

        if action not in REQUIRED_UPDATE_ACTIONS:
            return UpdateMediaBuyError(
                errors=[
                    Error(
                        code="unsupported_action",
                        message=f"Action '{action}' not supported. Supported: {REQUIRED_UPDATE_ACTIONS}",
                        details=None,
                    )
                ],
            )

        is_pause = action in ("pause_media_buy", "pause_package")
        is_resume = action in ("resume_media_buy", "resume_package")

        # Campaign-level pause/resume: toggle all advertisements across all packages
        if action in ("pause_media_buy", "resume_media_buy"):
            action_verb = "Pausing" if is_pause else "Resuming"

            with get_db_session() as session:
                stmt = select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)
                db_packages = session.scalars(stmt).all()

                if not db_packages:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="no_packages_found",
                                message=f"No packages found for media buy {media_buy_id}",
                                details=None,
                            )
                        ],
                    )

                # Collect all advertisement IDs across packages
                all_ad_ids: list[str] = []
                for pkg in db_packages:
                    ad_ids = pkg.package_config.get("broadstreet_advertisement_ids", [])
                    all_ad_ids.extend(ad_ids)

                # Deduplicate (ads are shared across packages)
                unique_ad_ids = list(set(all_ad_ids))

                if unique_ad_ids:
                    self.log(f"{action_verb} {len(unique_ad_ids)} advertisements")
                    if self.dry_run:
                        self.log(f"Would toggle active={'1' if is_resume else '0'} on {len(unique_ad_ids)} ads")
                    else:
                        failed = self._toggle_advertisements(unique_ad_ids, active=is_resume)
                        if failed:
                            return UpdateMediaBuyError(
                                errors=[
                                    Error(
                                        code="partial_failure",
                                        message=f"Failed to update {len(failed)} advertisements",
                                        details={"failed_advertisement_ids": failed},
                                    )
                                ],
                            )
                else:
                    self.log(f"[yellow]No Broadstreet advertisement IDs found for {media_buy_id}[/yellow]")

                affected = [
                    AffectedPackage(
                        package_id=pkg.package_id,
                        buyer_ref=buyer_ref or pkg.package_id,
                        paused=is_pause,
                        changes_applied=None,
                        buyer_package_ref=None,
                    )
                    for pkg in db_packages
                ]

                return UpdateMediaBuySuccess(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    affected_packages=affected,
                    implementation_date=today,
                )

        # Package-level pause/resume
        if action in ("pause_package", "resume_package"):
            if not package_id:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="missing_package_id",
                            message=f"package_id is required for {action} action",
                            details=None,
                        )
                    ],
                )

            action_verb = "Pausing" if is_pause else "Resuming"

            with get_db_session() as session:
                stmt = select(DBMediaPackage).where(
                    DBMediaPackage.package_id == package_id,
                    DBMediaPackage.media_buy_id == media_buy_id,
                )
                db_package = session.scalars(stmt).first()

                if not db_package:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="package_not_found",
                                message=f"Package {package_id} not found in media buy {media_buy_id}",
                                details=None,
                            )
                        ],
                    )

                ad_ids = db_package.package_config.get("broadstreet_advertisement_ids", [])

                if ad_ids:
                    self.log(f"{action_verb} {len(ad_ids)} advertisements for package {package_id}")
                    if self.dry_run:
                        self.log(f"Would toggle active={'1' if is_resume else '0'} on {len(ad_ids)} ads")
                    else:
                        failed = self._toggle_advertisements(ad_ids, active=is_resume)
                        if failed:
                            return UpdateMediaBuyError(
                                errors=[
                                    Error(
                                        code="api_update_failed",
                                        message=f"Failed to update {len(failed)} advertisements",
                                        details={"failed_advertisement_ids": failed},
                                    )
                                ],
                            )
                else:
                    self.log(f"[yellow]No Broadstreet advertisement IDs for package {package_id}[/yellow]")

                return UpdateMediaBuySuccess(
                    media_buy_id=media_buy_id,
                    buyer_ref=buyer_ref,
                    affected_packages=[
                        AffectedPackage(
                            package_id=package_id,
                            buyer_ref=buyer_ref,
                            paused=is_pause,
                            changes_applied=None,
                            buyer_package_ref=None,
                        )
                    ],
                    implementation_date=today,
                )

        # Budget update: persist to database (Broadstreet has no budget API)
        if action == "update_package_budget":
            if not package_id:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="missing_package_id",
                            message="package_id is required for update_package_budget action",
                            details=None,
                        )
                    ],
                )
            if budget is None:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="missing_budget",
                            message="budget is required for update_package_budget action",
                            details=None,
                        )
                    ],
                )

            with get_db_session() as session:
                stmt = select(DBMediaPackage).where(
                    DBMediaPackage.package_id == package_id,
                    DBMediaPackage.media_buy_id == media_buy_id,
                )
                db_package = session.scalars(stmt).first()

                if not db_package:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="package_not_found",
                                message=f"Package {package_id} not found",
                                details=None,
                            )
                        ],
                    )

                db_package.package_config["budget"] = float(budget)
                attributes.flag_modified(db_package, "package_config")
                session.commit()
                self.log(f"Updated budget for package {package_id} to ${budget / 100:.2f}")

            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[
                    AffectedPackage(
                        package_id=package_id,
                        buyer_ref=buyer_ref,
                        paused=False,
                        changes_applied={"budget": budget},
                        buyer_package_ref=None,
                    )
                ],
                implementation_date=today,
            )
        # Impressions update: persist to database (Broadstreet has no impressions API)
        if action == "update_package_impressions":
            if not package_id:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="missing_package_id",
                            message="package_id is required for update_package_impressions action",
                            details=None,
                        )
                    ],
                )
            if budget is None:
                return UpdateMediaBuyError(
                    errors=[
                        Error(
                            code="missing_impressions",
                            message="budget (impressions) is required for update_package_impressions action",
                            details=None,
                        )
                    ],
                )

            with get_db_session() as session:
                stmt = select(DBMediaPackage).where(
                    DBMediaPackage.package_id == package_id,
                    DBMediaPackage.media_buy_id == media_buy_id,
                )
                db_package = session.scalars(stmt).first()

                if not db_package:
                    return UpdateMediaBuyError(
                        errors=[
                            Error(
                                code="package_not_found",
                                message=f"Package {package_id} not found",
                                details=None,
                            )
                        ],
                    )

                db_package.package_config["impressions"] = budget
                attributes.flag_modified(db_package, "package_config")
                session.commit()
                self.log(f"Updated impressions for package {package_id} to {budget:,}")

            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[
                    AffectedPackage(
                        package_id=package_id,
                        buyer_ref=buyer_ref,
                        paused=False,
                        changes_applied={"impressions": budget},
                        buyer_package_ref=None,
                    )
                ],
                implementation_date=today,
            )

        # Should not reach here - all actions are handled above
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            buyer_ref=buyer_ref,
            affected_packages=[],
            implementation_date=today,
        )

    async def get_available_inventory(self) -> dict[str, Any]:
        """Fetch available inventory (zones) from Broadstreet.

        Returns:
            Dictionary with zones and their properties
        """
        self.log("Fetching available inventory from Broadstreet", dry_run_prefix=False)
        return self.inventory_manager.build_inventory_response()

    def get_creative_formats(self) -> list[dict[str, Any]]:
        """Return Broadstreet templates as AdCP creative formats.

        Converts Broadstreet template definitions to AdCP Format schema.
        These formats are included in list_creative_formats responses when
        Broadstreet is acting as both sales and creative agent.

        Returns:
            List of format dictionaries matching AdCP Format schema
        """
        from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES

        formats = []
        # Use tenant subdomain or adapter name for agent URL
        agent_url = f"broadstreet://{self.tenant_id or 'default'}"

        for template_id, template in BROADSTREET_TEMPLATES.items():
            # Build assets list from required and optional assets
            assets = []

            # Required assets
            for asset_id in template.get("required_assets", []):
                asset_type = self._infer_asset_type(asset_id)
                assets.append(
                    {
                        "item_type": "individual",
                        "asset_id": asset_id,
                        "asset_type": asset_type,
                        "required": True,
                        "name": asset_id.replace("_", " ").title(),
                    }
                )

            # Optional assets
            for asset_id in template.get("optional_assets", []):
                asset_type = self._infer_asset_type(asset_id)
                assets.append(
                    {
                        "item_type": "individual",
                        "asset_id": asset_id,
                        "asset_type": asset_type,
                        "required": False,
                        "name": asset_id.replace("_", " ").title(),
                    }
                )

            formats.append(
                {
                    "format_id": {"id": f"broadstreet_{template_id}", "agent_url": agent_url},
                    "name": template["name"],
                    "type": "display",  # All Broadstreet formats are display
                    "description": template.get("description", ""),
                    "assets": assets,
                    "is_standard": False,  # These are Broadstreet-specific formats
                }
            )

        return formats

    def _infer_asset_type(self, asset_id: str) -> str:
        """Infer asset type from asset ID naming convention.

        Args:
            asset_id: Asset identifier (e.g., "front_image", "youtube_url", "headline")

        Returns:
            Asset type string (image, video, text, url)
        """
        asset_lower = asset_id.lower()
        if "image" in asset_lower or "logo" in asset_lower:
            return "image"
        elif "video" in asset_lower or "youtube" in asset_lower:
            return "video"
        elif "url" in asset_lower or "click" in asset_lower:
            return "url"
        elif "html" in asset_lower:
            return "html"
        else:
            return "text"  # Default to text for headlines, body, captions, etc.

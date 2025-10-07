import csv
import gzip
import io
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import google.oauth2.service_account
import requests
from flask import Flask, flash, redirect, render_template, request, url_for
from googleads import ad_manager

from src.adapters.base import AdServerAdapter, CreativeEngineAdapter
from src.adapters.constants import REQUIRED_UPDATE_ACTIONS
from src.adapters.gam.utils.validation import GAMValidator
from src.adapters.gam_implementation_config_schema import GAMImplementationConfig
from src.adapters.gam_reporting_service import ReportingConfig
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AssetStatus,
    CheckMediaBuyStatusResponse,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    DeliveryTotals,
    MediaPackage,
    PackageDelivery,
    PackagePerformance,
    Principal,
    ReportingPeriod,
    UpdateMediaBuyResponse,
)

# Set up logger
logger = logging.getLogger(__name__)

# Line item type constants for automation logic
GUARANTEED_LINE_ITEM_TYPES = {"STANDARD", "SPONSORSHIP"}
NON_GUARANTEED_LINE_ITEM_TYPES = {"NETWORK", "BULK", "PRICE_PRIORITY", "HOUSE"}


class GoogleAdManager(AdServerAdapter):
    """
    Adapter for interacting with the Google Ad Manager API.
    """

    adapter_name = "gam"

    def __init__(
        self,
        config: dict[str, Any],
        principal: Principal,
        dry_run: bool = False,
        creative_engine: CreativeEngineAdapter | None = None,
        tenant_id: str | None = None,
    ):
        super().__init__(config, principal, dry_run, creative_engine, tenant_id)
        self.network_code = self.config.get("network_code")
        self.key_file = self.config.get("service_account_key_file")
        self.refresh_token = self.config.get("refresh_token")
        self.trafficker_id = self.config.get("trafficker_id", None)

        # Use the principal's advertiser_id from platform_mappings
        self.advertiser_id = self.adapter_principal_id
        # For backward compatibility, fall back to company_id if advertiser_id is not set
        if not self.advertiser_id:
            self.advertiser_id = self.config.get("company_id")

        # Store company_id (advertiser_id) for use in API calls
        self.company_id = self.advertiser_id

        # Check for either service account or OAuth credentials
        if not self.dry_run:
            if not self.network_code:
                raise ValueError("GAM config is missing 'network_code'")
            if not self.advertiser_id:
                raise ValueError("Principal is missing 'gam_advertiser_id' in platform_mappings")
            if not self.trafficker_id:
                raise ValueError("GAM config is missing 'trafficker_id'")
            if not self.key_file and not self.refresh_token:
                raise ValueError("GAM config requires either 'service_account_key_file' or 'refresh_token'")

        if not self.dry_run:
            self.client = self._init_client()
        else:
            self.client = None
            self.log("[yellow]Running in dry-run mode - GAM client not initialized[/yellow]")

        # Load geo mappings
        self._load_geo_mappings()

        # Initialize GAM validator for creative validation
        self.validator = GAMValidator()

    def _create_order_statement(self, order_id: int):
        """Helper method to create a GAM statement for order filtering."""
        statement_builder = ad_manager.StatementBuilder()
        statement_builder.Where("ORDER_ID = :orderId")
        statement_builder.WithBindVariable("orderId", order_id)
        return statement_builder.ToStatement()

    def _init_client(self):
        """Initializes the Ad Manager client."""
        try:
            # Use the new helper function if we have a tenant_id
            if self.tenant_id:
                pass

            from googleads import ad_manager

            if self.refresh_token:
                # Use OAuth with refresh token
                oauth2_client = self._get_oauth_credentials()

                # Create AdManager client
                ad_manager_client = ad_manager.AdManagerClient(
                    oauth2_client, "AdCP Sales Agent", network_code=self.network_code
                )
                return ad_manager_client

            elif self.key_file:
                # Use service account (legacy)
                credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                    self.key_file, scopes=["https://www.googleapis.com/auth/dfp"]
                )

                # Create AdManager client
                ad_manager_client = ad_manager.AdManagerClient(
                    credentials, "AdCP Sales Agent", network_code=self.network_code
                )
                return ad_manager_client
            else:
                raise ValueError("GAM config requires either 'service_account_key_file' or 'refresh_token'")

        except Exception as e:
            logger.error(f"Error initializing GAM client: {e}")
            raise

    def _get_oauth_credentials(self):
        """Get OAuth credentials using refresh token and Pydantic configuration."""
        from googleads import oauth2

        try:
            from src.core.config import get_gam_oauth_config

            # Get validated configuration
            gam_config = get_gam_oauth_config()
            client_id = gam_config.client_id
            client_secret = gam_config.client_secret

        except Exception as e:
            raise ValueError(f"GAM OAuth configuration error: {str(e)}") from e

        # Create GoogleAds OAuth2 client
        oauth2_client = oauth2.GoogleRefreshTokenClient(
            client_id=client_id, client_secret=client_secret, refresh_token=self.refresh_token
        )

        return oauth2_client

    # Supported device types and their GAM numeric device category IDs
    # These are GAM's standard device category IDs that work across networks
    DEVICE_TYPE_MAP = {
        "mobile": 30000,  # Mobile devices
        "desktop": 30001,  # Desktop computers
        "tablet": 30002,  # Tablet devices
        "ctv": 30003,  # Connected TV / Streaming devices
        "dooh": 30004,  # Digital out-of-home / Set-top box
    }

    def _load_geo_mappings(self):
        """Load geo mappings from JSON file."""
        try:
            mapping_file = os.path.join(os.path.dirname(__file__), "gam_geo_mappings.json")
            with open(mapping_file) as f:
                geo_data = json.load(f)

            self.GEO_COUNTRY_MAP = geo_data.get("countries", {})
            self.GEO_REGION_MAP = geo_data.get("regions", {})
            self.GEO_METRO_MAP = geo_data.get("metros", {}).get("US", {})  # Currently only US metros

            self.log(
                f"Loaded GAM geo mappings: {len(self.GEO_COUNTRY_MAP)} countries, "
                f"{sum(len(v) for v in self.GEO_REGION_MAP.values())} regions, "
                f"{len(self.GEO_METRO_MAP)} metros"
            )
        except Exception as e:
            self.log(f"[yellow]Warning: Could not load geo mappings file: {e}[/yellow]")
            self.log("[yellow]Using empty geo mappings - geo targeting will not work properly[/yellow]")
            self.GEO_COUNTRY_MAP = {}
            self.GEO_REGION_MAP = {}
            self.GEO_METRO_MAP = {}

    def _lookup_region_id(self, region_code):
        """Look up region ID across all countries."""
        # First check if we have country context (not implemented yet)
        # For now, search across all countries
        for _country, regions in self.GEO_REGION_MAP.items():
            if region_code in regions:
                return regions[region_code]
        return None

    # Supported media types
    SUPPORTED_MEDIA_TYPES = {"video", "display", "native"}

    def _validate_targeting(self, targeting_overlay):
        """Validate targeting and return unsupported features."""
        unsupported = []

        if not targeting_overlay:
            return unsupported

        # Check device types
        if targeting_overlay.device_type_any_of:
            for device in targeting_overlay.device_type_any_of:
                if device not in self.DEVICE_TYPE_MAP:
                    unsupported.append(f"Device type '{device}' not supported")

        # Check media types
        if targeting_overlay.media_type_any_of:
            for media in targeting_overlay.media_type_any_of:
                if media not in self.SUPPORTED_MEDIA_TYPES:
                    unsupported.append(f"Media type '{media}' not supported")

        # Audio-specific targeting not supported
        if targeting_overlay.media_type_any_of and "audio" in targeting_overlay.media_type_any_of:
            unsupported.append("Audio media type not supported by Google Ad Manager")

        # City and postal targeting require GAM API lookups (not implemented)
        if targeting_overlay.geo_city_any_of or targeting_overlay.geo_city_none_of:
            unsupported.append("City targeting requires GAM geo service integration (not implemented)")
        if targeting_overlay.geo_zip_any_of or targeting_overlay.geo_zip_none_of:
            unsupported.append("Postal code targeting requires GAM geo service integration (not implemented)")

        # GAM supports all other standard targeting dimensions

        return unsupported

    def _build_targeting(self, targeting_overlay):
        """Build GAM targeting criteria from AdCP targeting."""
        if not targeting_overlay:
            return {}

        gam_targeting = {}

        # Geographic targeting
        geo_targeting = {}

        # Build targeted locations
        if any(
            [
                targeting_overlay.geo_country_any_of,
                targeting_overlay.geo_region_any_of,
                targeting_overlay.geo_metro_any_of,
                targeting_overlay.geo_city_any_of,
                targeting_overlay.geo_zip_any_of,
            ]
        ):
            geo_targeting["targetedLocations"] = []

            # Map countries
            if targeting_overlay.geo_country_any_of:
                for country in targeting_overlay.geo_country_any_of:
                    if country in self.GEO_COUNTRY_MAP:
                        geo_targeting["targetedLocations"].append({"id": self.GEO_COUNTRY_MAP[country]})
                    else:
                        self.log(f"[yellow]Warning: Country code '{country}' not in GAM mapping[/yellow]")

            # Map regions
            if targeting_overlay.geo_region_any_of:
                for region in targeting_overlay.geo_region_any_of:
                    region_id = self._lookup_region_id(region)
                    if region_id:
                        geo_targeting["targetedLocations"].append({"id": region_id})
                    else:
                        self.log(f"[yellow]Warning: Region code '{region}' not in GAM mapping[/yellow]")

            # Map metros (DMAs)
            if targeting_overlay.geo_metro_any_of:
                for metro in targeting_overlay.geo_metro_any_of:
                    if metro in self.GEO_METRO_MAP:
                        geo_targeting["targetedLocations"].append({"id": self.GEO_METRO_MAP[metro]})
                    else:
                        self.log(f"[yellow]Warning: Metro code '{metro}' not in GAM mapping[/yellow]")

            # City and postal require real GAM API lookup - for now we log a warning
            if targeting_overlay.geo_city_any_of:
                self.log("[yellow]Warning: City targeting requires GAM geo service lookup (not implemented)[/yellow]")
            if targeting_overlay.geo_zip_any_of:
                self.log(
                    "[yellow]Warning: Postal code targeting requires GAM geo service lookup (not implemented)[/yellow]"
                )

        # Build excluded locations
        if any(
            [
                targeting_overlay.geo_country_none_of,
                targeting_overlay.geo_region_none_of,
                targeting_overlay.geo_metro_none_of,
                targeting_overlay.geo_city_none_of,
                targeting_overlay.geo_zip_none_of,
            ]
        ):
            geo_targeting["excludedLocations"] = []

            # Map excluded countries
            if targeting_overlay.geo_country_none_of:
                for country in targeting_overlay.geo_country_none_of:
                    if country in self.GEO_COUNTRY_MAP:
                        geo_targeting["excludedLocations"].append({"id": self.GEO_COUNTRY_MAP[country]})

            # Map excluded regions
            if targeting_overlay.geo_region_none_of:
                for region in targeting_overlay.geo_region_none_of:
                    region_id = self._lookup_region_id(region)
                    if region_id:
                        geo_targeting["excludedLocations"].append({"id": region_id})

            # Map excluded metros
            if targeting_overlay.geo_metro_none_of:
                for metro in targeting_overlay.geo_metro_none_of:
                    if metro in self.GEO_METRO_MAP:
                        geo_targeting["excludedLocations"].append({"id": self.GEO_METRO_MAP[metro]})

            # City and postal exclusions
            if targeting_overlay.geo_city_none_of:
                self.log("[yellow]Warning: City exclusion requires GAM geo service lookup (not implemented)[/yellow]")
            if targeting_overlay.geo_zip_none_of:
                self.log(
                    "[yellow]Warning: Postal code exclusion requires GAM geo service lookup (not implemented)[/yellow]"
                )

        if geo_targeting:
            gam_targeting["geoTargeting"] = geo_targeting

        # Technology/Device targeting - NOT SUPPORTED, MUST FAIL LOUDLY
        if targeting_overlay.device_type_any_of:
            raise ValueError(
                f"Device targeting requested but not supported. "
                f"Cannot fulfill buyer contract for device types: {targeting_overlay.device_type_any_of}."
            )

        if targeting_overlay.os_any_of:
            raise ValueError(
                f"OS targeting requested but not supported. "
                f"Cannot fulfill buyer contract for OS types: {targeting_overlay.os_any_of}."
            )

        if targeting_overlay.browser_any_of:
            raise ValueError(
                f"Browser targeting requested but not supported. "
                f"Cannot fulfill buyer contract for browsers: {targeting_overlay.browser_any_of}."
            )

        # Content targeting - NOT SUPPORTED, MUST FAIL LOUDLY
        if targeting_overlay.content_cat_any_of:
            raise ValueError(
                f"Content category targeting requested but not supported. "
                f"Cannot fulfill buyer contract for categories: {targeting_overlay.content_cat_any_of}."
            )

        if targeting_overlay.keywords_any_of:
            raise ValueError(
                f"Keyword targeting requested but not supported. "
                f"Cannot fulfill buyer contract for keywords: {targeting_overlay.keywords_any_of}."
            )

        # Custom key-value targeting
        custom_targeting = {}

        # Platform-specific custom targeting
        if targeting_overlay.custom and "gam" in targeting_overlay.custom:
            custom_targeting.update(targeting_overlay.custom["gam"].get("key_values", {}))

        # AEE signal integration via key-value pairs (managed-only)
        if targeting_overlay.key_value_pairs:
            self.log("[bold cyan]Adding AEE signals to GAM key-value targeting[/bold cyan]")
            for key, value in targeting_overlay.key_value_pairs.items():
                custom_targeting[key] = value
                self.log(f"  {key}: {value}")

        if custom_targeting:
            gam_targeting["customTargeting"] = custom_targeting

        self.log(f"Applying GAM targeting: {list(gam_targeting.keys())}")
        return gam_targeting

    def create_media_buy(
        self, request: CreateMediaBuyRequest, packages: list[MediaPackage], start_time: datetime, end_time: datetime
    ) -> CreateMediaBuyResponse:
        """Creates a new Order and associated LineItems in Google Ad Manager."""
        # Get products to access implementation_config
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Product

        # Create a map of package_id to product for easy lookup
        products_map = {}
        with get_db_session() as db_session:
            for package in packages:
                stmt = select(Product).filter_by(
                    tenant_id=self.tenant_id, product_id=package.package_id  # package_id is actually product_id
                )
                product = db_session.scalars(stmt).first()
                if product:
                    products_map[package.package_id] = {
                        "product_id": product.product_id,
                        "implementation_config": (
                            json.loads(product.implementation_config) if product.implementation_config else {}
                        ),
                    }

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
            f"[bold]GoogleAdManager.create_media_buy[/bold] for principal '{self.principal.name}' (GAM advertiser ID: {self.advertiser_id})",
            dry_run_prefix=False,
        )

        # Validate targeting
        unsupported_features = self._validate_targeting(request.targeting_overlay)
        if unsupported_features:
            error_msg = f"Unsupported targeting features for Google Ad Manager: {', '.join(unsupported_features)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            return CreateMediaBuyResponse(media_buy_id="", status="failed", detail=error_msg)

        media_buy_id = f"gam_{int(datetime.now().timestamp())}"

        # Determine automation behavior BEFORE creating orders
        has_non_guaranteed = False
        automation_mode = "manual"  # Default

        for package in packages:
            product = products_map.get(package.package_id)
            impl_config = product.get("implementation_config", {}) if product else {}
            line_item_type = impl_config.get("line_item_type", "STANDARD")

            if line_item_type in NON_GUARANTEED_LINE_ITEM_TYPES:
                has_non_guaranteed = True
                automation_mode = impl_config.get("non_guaranteed_automation", "manual")
                break  # Use first non-guaranteed product's automation setting

        # Handle manual mode - don't create orders, just create workflow
        if has_non_guaranteed and automation_mode == "manual":
            self.log("[bold blue]Manual mode: Creating human workflow step instead of GAM order[/bold blue]")
            self._create_manual_order_workflow_step(request, packages, start_time, end_time, media_buy_id)
            return CreateMediaBuyResponse(
                media_buy_id=media_buy_id,
                status="pending_manual_creation",
                detail="Awaiting manual creation of GAM order by human operator",
                creative_deadline=datetime.now() + timedelta(days=2),
            )

        # Continue with order creation for automatic and confirmation_required modes
        # Get order name template from first product's config (they should all be the same)
        order_name_template = "AdCP-{po_number}-{timestamp}"
        applied_team_ids = []
        if products_map:
            first_product = next(iter(products_map.values()))
            if first_product.get("implementation_config"):
                order_name_template = first_product["implementation_config"].get(
                    "order_name_template", order_name_template
                )
                applied_team_ids = first_product["implementation_config"].get("applied_team_ids", [])

        # Format order name
        order_name = order_name_template.format(
            po_number=request.po_number or media_buy_id,
            product_name=packages[0].name if packages else "Unknown",
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            principal_name=self.principal.name,
        )

        # Create Order object
        order = {
            "name": order_name,
            "advertiserId": self.advertiser_id,
            "traffickerId": self.trafficker_id,
            "totalBudget": {"currencyCode": "USD", "microAmount": int(request.total_budget * 1_000_000)},
            "startDateTime": {
                "date": {"year": start_time.year, "month": start_time.month, "day": start_time.day},
                "hour": start_time.hour,
                "minute": start_time.minute,
                "second": start_time.second,
            },
            "endDateTime": {
                "date": {"year": end_time.year, "month": end_time.month, "day": end_time.day},
                "hour": end_time.hour,
                "minute": end_time.minute,
                "second": end_time.second,
            },
        }

        # Add team IDs if configured
        if applied_team_ids:
            order["appliedTeamIds"] = applied_team_ids

        if self.dry_run:
            self.log(f"Would call: order_service.createOrders([{order['name']}])")
            self.log(f"  Advertiser ID: {self.advertiser_id}")
            self.log(f"  Total Budget: ${request.total_budget:,.2f}")
            self.log(f"  Flight Dates: {start_time.date()} to {end_time.date()}")
        else:
            order_service = self.client.GetService("OrderService")
            created_orders = order_service.createOrders([order])
            if created_orders:
                media_buy_id = str(created_orders[0]["id"])
                self.log(f"✓ Created GAM Order ID: {media_buy_id}")
                self.audit_logger.log_success(f"Created GAM Order ID: {media_buy_id}")

        # Create LineItems for each package
        for package in packages:
            # Get product-specific configuration
            product = products_map.get(package.package_id)
            impl_config = product.get("implementation_config", {}) if product else {}

            # Build targeting - merge product targeting with request overlay
            targeting = self._build_targeting(request.targeting_overlay)

            # Add ad unit/placement targeting from product config
            if impl_config.get("targeted_ad_unit_ids"):
                if "inventoryTargeting" not in targeting:
                    targeting["inventoryTargeting"] = {}
                targeting["inventoryTargeting"]["targetedAdUnits"] = [
                    {"adUnitId": ad_unit_id, "includeDescendants": impl_config.get("include_descendants", True)}
                    for ad_unit_id in impl_config["targeted_ad_unit_ids"]
                ]

            if impl_config.get("targeted_placement_ids"):
                if "inventoryTargeting" not in targeting:
                    targeting["inventoryTargeting"] = {}
                targeting["inventoryTargeting"]["targetedPlacements"] = [
                    {"placementId": placement_id} for placement_id in impl_config["targeted_placement_ids"]
                ]

            # Fallback: If no inventory targeting specified, use root ad unit from network config (GAM requires inventory targeting)
            if "inventoryTargeting" not in targeting or not targeting["inventoryTargeting"]:
                self.log(
                    "[yellow]Warning: No inventory targeting specified in product config. Using network root ad unit as fallback.[/yellow]"
                )

                # Get root ad unit ID from GAM network info (fallback only)
                # This should be rare - products should specify their own targeted_ad_unit_ids
                network_service = self.client.GetService("NetworkService")
                current_network = network_service.getCurrentNetwork()
                root_ad_unit_id = current_network["effectiveRootAdUnitId"]

                targeting["inventoryTargeting"] = {
                    "targetedAdUnits": [{"adUnitId": root_ad_unit_id, "includeDescendants": True}]
                }

            # Add custom targeting from product config
            if impl_config.get("custom_targeting_keys"):
                if "customTargeting" not in targeting:
                    targeting["customTargeting"] = {}
                targeting["customTargeting"].update(impl_config["custom_targeting_keys"])

            # Build creative placeholders from config
            creative_placeholders = []
            if impl_config.get("creative_placeholders"):
                for placeholder in impl_config["creative_placeholders"]:
                    creative_placeholders.append(
                        {
                            "size": {"width": placeholder["width"], "height": placeholder["height"]},
                            "expectedCreativeCount": placeholder.get("expected_creative_count", 1),
                            "creativeSizeType": "NATIVE" if placeholder.get("is_native") else "PIXEL",
                        }
                    )
            else:
                # Default placeholder if none configured
                creative_placeholders = [
                    {"size": {"width": 300, "height": 250}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"}
                ]

            # Determine goal type based on flight duration
            # GAM doesn't allow DAILY for flights < 3 days
            flight_duration_days = (end_time - start_time).days
            if flight_duration_days < 3:
                goal_type = "LIFETIME"
                goal_units = package.impressions  # Use full impression count for lifetime
            else:
                goal_type = impl_config.get("primary_goal_type", "DAILY")
                goal_units = min(package.impressions, 100)  # Cap daily impressions for test accounts

            line_item = {
                "name": package.name,
                "orderId": media_buy_id,
                "targeting": targeting,
                "creativePlaceholders": creative_placeholders,
                "lineItemType": impl_config.get("line_item_type", "STANDARD"),
                "priority": impl_config.get("priority", 8),
                "costType": impl_config.get("cost_type", "CPM"),
                "costPerUnit": {"currencyCode": "USD", "microAmount": int(package.cpm * 1_000_000)},
                "primaryGoal": {
                    "goalType": goal_type,
                    "unitType": impl_config.get("primary_goal_unit_type", "IMPRESSIONS"),
                    "units": goal_units,
                },
                "creativeRotationType": impl_config.get("creative_rotation_type", "EVEN"),
                "deliveryRateType": impl_config.get("delivery_rate_type", "EVENLY"),
                # Add line item dates (required by GAM) - inherit from order
                "startDateTime": {
                    "date": {"year": start_time.year, "month": start_time.month, "day": start_time.day},
                    "hour": start_time.hour,
                    "minute": start_time.minute,
                    "second": start_time.second,
                    "timeZoneId": "America/New_York",  # Line items require timezone (orders don't) - Note: lowercase 'd'
                },
                "endDateTime": {
                    "date": {"year": end_time.year, "month": end_time.month, "day": end_time.day},
                    "hour": end_time.hour,
                    "minute": end_time.minute,
                    "second": end_time.second,
                    "timeZoneId": "America/New_York",  # Line items require timezone (orders don't) - Note: lowercase 'd'
                },
            }

            # Add frequency caps if configured
            if impl_config.get("frequency_caps"):
                frequency_caps = []
                for cap in impl_config["frequency_caps"]:
                    frequency_caps.append(
                        {
                            "maxImpressions": cap["max_impressions"],
                            "numTimeUnits": cap["time_range"],
                            "timeUnit": cap["time_unit"],
                        }
                    )
                line_item["frequencyCaps"] = frequency_caps

            # Add competitive exclusion labels
            if impl_config.get("competitive_exclusion_labels"):
                line_item["effectiveAppliedLabels"] = [
                    {"labelId": label} for label in impl_config["competitive_exclusion_labels"]
                ]

            # Add discount if configured
            if impl_config.get("discount_type") and impl_config.get("discount_value"):
                line_item["discount"] = impl_config["discount_value"]
                line_item["discountType"] = impl_config["discount_type"]

            # Add video-specific settings
            if impl_config.get("environment_type") == "VIDEO_PLAYER":
                line_item["environmentType"] = "VIDEO_PLAYER"
                if impl_config.get("companion_delivery_option"):
                    line_item["companionDeliveryOption"] = impl_config["companion_delivery_option"]
                if impl_config.get("video_max_duration"):
                    line_item["videoMaxDuration"] = impl_config["video_max_duration"]
                if impl_config.get("skip_offset"):
                    line_item["videoSkippableAdType"] = "ENABLED"
                    line_item["videoSkipOffset"] = impl_config["skip_offset"]
            else:
                line_item["environmentType"] = impl_config.get("environment_type", "BROWSER")

            # Advanced settings
            if impl_config.get("allow_overbook"):
                line_item["allowOverbook"] = True
            if impl_config.get("skip_inventory_check"):
                line_item["skipInventoryCheck"] = True
            if impl_config.get("disable_viewability_avg_revenue_optimization"):
                line_item["disableViewabilityAvgRevenueOptimization"] = True

            if self.dry_run:
                self.log(f"Would call: line_item_service.createLineItems(['{package.name}'])")
                self.log(f"  Package: {package.name}")
                self.log(f"  Line Item Type: {impl_config.get('line_item_type', 'STANDARD')}")
                self.log(f"  Priority: {impl_config.get('priority', 8)}")
                self.log(f"  CPM: ${package.cpm}")
                self.log(f"  Impressions Goal: {package.impressions:,}")
                self.log(f"  Creative Placeholders: {len(creative_placeholders)} sizes")
                for cp in creative_placeholders[:3]:  # Show first 3
                    self.log(
                        f"    - {cp['size']['width']}x{cp['size']['height']} ({'Native' if cp.get('creativeSizeType') == 'NATIVE' else 'Display'})"
                    )
                if len(creative_placeholders) > 3:
                    self.log(f"    - ... and {len(creative_placeholders) - 3} more")
                if impl_config.get("frequency_caps"):
                    self.log(f"  Frequency Caps: {len(impl_config['frequency_caps'])} configured")
                # Log key-value pairs for AEE signals
                if "customTargeting" in targeting and targeting["customTargeting"]:
                    self.log("  Custom Targeting (Key-Value Pairs):")
                    for key, value in targeting["customTargeting"].items():
                        self.log(f"    - {key}: {value}")
                if impl_config.get("targeted_ad_unit_ids"):
                    self.log(f"  Targeted Ad Units: {len(impl_config['targeted_ad_unit_ids'])} units")
                if impl_config.get("environment_type") == "VIDEO_PLAYER":
                    self.log(
                        f"  Video Settings: max duration {impl_config.get('video_max_duration', 'N/A')}ms, skip after {impl_config.get('skip_offset', 'N/A')}ms"
                    )
            else:
                try:
                    line_item_service = self.client.GetService("LineItemService")
                    created_line_items = line_item_service.createLineItems([line_item])
                    if created_line_items:
                        self.log(f"✓ Created LineItem ID: {created_line_items[0]['id']} for {package.name}")
                        self.audit_logger.log_success(f"Created GAM LineItem ID: {created_line_items[0]['id']}")
                except Exception as e:
                    error_msg = f"Failed to create LineItem for {package.name}: {str(e)}"
                    self.log(f"[red]Error: {error_msg}[/red]")
                    self.audit_logger.log_warning(error_msg)
                    # Log the targeting structure for debugging
                    self.log(f"[red]Targeting structure that caused error: {targeting}[/red]")
                    raise

        # Apply automation logic for orders that were created (automatic and confirmation_required)
        status = "pending_activation"
        detail = "Media buy created in Google Ad Manager"

        if has_non_guaranteed:
            if automation_mode == "automatic":
                self.log("[bold green]Non-guaranteed order with automatic activation enabled[/bold green]")
                if self._activate_order_automatically(media_buy_id):
                    status = "active"
                    detail = "Media buy created and automatically activated in Google Ad Manager"
                else:
                    status = "failed"
                    detail = "Media buy created but automatic activation failed"

            elif automation_mode == "confirmation_required":
                self.log("[bold yellow]Non-guaranteed order requiring confirmation before activation[/bold yellow]")
                # Create workflow step for human approval
                self._create_activation_workflow_step(media_buy_id, packages)
                status = "pending_confirmation"
                detail = "Media buy created, awaiting approval for automatic activation"

            # Note: manual mode is handled earlier and returns before this point

        else:
            self.log("[bold blue]Guaranteed order types always require manual activation[/bold blue]")
            # Guaranteed orders always stay pending_activation regardless of config

        return CreateMediaBuyResponse(
            media_buy_id=media_buy_id,
            status=status,
            detail=detail,
            creative_deadline=datetime.now() + timedelta(days=2),
        )

    def _activate_order_automatically(self, media_buy_id: str) -> bool:
        """Activates a GAM order and its line items automatically.

        Uses performOrderAction with ResumeOrders to activate the order,
        then performLineItemAction with ActivateLineItems for line items.

        Args:
            media_buy_id: The GAM order ID to activate

        Returns:
            bool: True if activation succeeded, False otherwise
        """
        self.log(f"[bold cyan]Automatically activating GAM Order {media_buy_id}[/bold cyan]")

        if self.dry_run:
            self.log(f"Would call: order_service.performOrderAction(ResumeOrders, {media_buy_id})")
            self.log(
                f"Would call: line_item_service.performLineItemAction(ActivateLineItems, WHERE orderId={media_buy_id})"
            )
            return True

        try:
            # Get services
            order_service = self.client.GetService("OrderService")
            line_item_service = self.client.GetService("LineItemService")

            # Activate the order using ResumeOrders action
            from googleads import ad_manager

            order_action = {"xsi_type": "ResumeOrders"}
            order_statement_builder = ad_manager.StatementBuilder()
            order_statement_builder.Where("id = :orderId")
            order_statement_builder.WithBindVariable("orderId", int(media_buy_id))
            order_statement = order_statement_builder.ToStatement()

            order_result = order_service.performOrderAction(order_action, order_statement)

            if order_result and order_result.get("numChanges", 0) > 0:
                self.log(f"✓ Successfully activated GAM Order {media_buy_id}")
                self.audit_logger.log_success(f"Auto-activated GAM Order {media_buy_id}")
            else:
                self.log(f"[yellow]Warning: Order {media_buy_id} may already be active or no changes made[/yellow]")

            # Activate line items using ActivateLineItems action
            line_item_action = {"xsi_type": "ActivateLineItems"}
            line_item_statement_builder = ad_manager.StatementBuilder()
            line_item_statement_builder.Where("orderId = :orderId")
            line_item_statement_builder.WithBindVariable("orderId", int(media_buy_id))
            line_item_statement = line_item_statement_builder.ToStatement()

            line_item_result = line_item_service.performLineItemAction(line_item_action, line_item_statement)

            if line_item_result and line_item_result.get("numChanges", 0) > 0:
                self.log(
                    f"✓ Successfully activated {line_item_result['numChanges']} line items in Order {media_buy_id}"
                )
                self.audit_logger.log_success(
                    f"Auto-activated {line_item_result['numChanges']} line items in Order {media_buy_id}"
                )
            else:
                self.log(
                    f"[yellow]Warning: No line items activated in Order {media_buy_id} (may already be active)[/yellow]"
                )

            return True

        except Exception as e:
            error_msg = f"Failed to activate GAM Order {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            self.audit_logger.log_warning(error_msg)
            return False

    def _create_activation_workflow_step(self, media_buy_id: str, packages: list) -> None:
        """Creates a workflow step for human approval of order activation.

        Args:
            media_buy_id: The GAM order ID awaiting activation
            packages: List of packages in the media buy for context
        """
        import uuid

        from src.core.database.database_session import get_db_session
        from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

        step_id = f"a{uuid.uuid4().hex[:5]}"  # 6 chars total

        # Build detailed action list for humans
        action_details = {
            "action_type": "activate_gam_order",
            "order_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "confirmation_required",
            "instructions": [
                f"Review GAM Order {media_buy_id} in your GAM account",
                "Verify line item settings, targeting, and creative placeholders are correct",
                "Confirm budget, flight dates, and delivery settings are acceptable",
                "Check that ad units and placements are properly targeted",
                "Once verified, approve this task to automatically activate the order and line items",
            ],
            "gam_order_url": f"https://admanager.google.com/orders/{media_buy_id}",
            "packages": [{"name": pkg.name, "impressions": pkg.impressions, "cpm": pkg.cpm} for pkg in packages],
            "next_action_after_approval": "automatic_activation",
        }

        try:
            with get_db_session() as db_session:
                # Create a context for this workflow if needed
                import uuid

                context_id = f"ctx_{uuid.uuid4().hex[:12]}"

                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="approval",
                    tool_name="activate_gam_order",
                    request_data=action_details,
                    status="approval",  # Shortened to fit database field
                    owner="publisher",  # Publisher needs to approve GAM order activation
                    assigned_to=None,  # Will be assigned by admin
                    transaction_details={"gam_order_id": media_buy_id},
                )

                db_session.add(workflow_step)

                # Create object mapping to link this step with the media buy
                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy", object_id=media_buy_id, step_id=step_id, action="activate"
                )

                db_session.add(object_mapping)
                db_session.commit()

                self.log(f"✓ Created workflow step {step_id} for GAM order activation approval")
                self.audit_logger.log_success(f"Created activation approval workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

        except Exception as e:
            error_msg = f"Failed to create activation workflow step for order {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            self.audit_logger.log_warning(error_msg)

    def _create_manual_order_workflow_step(
        self,
        request: CreateMediaBuyRequest,
        packages: list[MediaPackage],
        start_time: datetime,
        end_time: datetime,
        media_buy_id: str,
    ) -> None:
        """Creates a workflow step for manual creation of GAM order (manual mode).

        Args:
            request: The original media buy request
            packages: List of packages to be created
            start_time: Campaign start time
            end_time: Campaign end time
            media_buy_id: Generated media buy ID for tracking
        """
        import uuid

        from src.core.database.database_session import get_db_session
        from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

        step_id = f"c{uuid.uuid4().hex[:5]}"  # 6 chars total

        # Build detailed action list for humans to manually create the order
        action_details = {
            "action_type": "create_gam_order",
            "media_buy_id": media_buy_id,
            "platform": "Google Ad Manager",
            "automation_mode": "manual",
            "instructions": [
                "Manually create a new order in Google Ad Manager with the following details:",
                f"Order Name: {request.po_number or media_buy_id}",
                f"Advertiser: {self.advertiser_id}",
                f"Total Budget: ${request.total_budget:,.2f}",
                f"Flight Dates: {start_time.date()} to {end_time.date()}",
                "Create line items for each package listed below",
                "Set up targeting, creative placeholders, and delivery settings",
                "Once order is created, update this task with the GAM Order ID",
            ],
            "order_details": {
                "po_number": request.po_number,
                "total_budget": request.total_budget,
                "flight_start": start_time.isoformat(),
                "flight_end": end_time.isoformat(),
                "advertiser_id": self.advertiser_id,
                "trafficker_id": self.trafficker_id,
            },
            "packages": [
                {
                    "name": pkg.name,
                    "impressions": pkg.impressions,
                    "cpm": pkg.cpm,
                    "delivery_type": pkg.delivery_type,
                    "format_ids": pkg.format_ids,
                }
                for pkg in packages
            ],
            "targeting": request.targeting_overlay.model_dump() if request.targeting_overlay else {},
            "next_action_after_completion": "order_created",
            "gam_network_url": f"https://admanager.google.com/{self.network_code}",
        }

        try:
            with get_db_session() as db_session:
                # Create a context for this workflow if needed
                import uuid

                context_id = f"ctx_{uuid.uuid4().hex[:12]}"

                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=step_id,
                    context_id=context_id,
                    step_type="manual_task",
                    tool_name="create_gam_order",
                    request_data=action_details,
                    status="pending",  # Shortened to fit database field
                    owner="publisher",  # Publisher needs to manually create the order
                    assigned_to=None,  # Will be assigned by admin
                    transaction_details={"media_buy_id": media_buy_id, "expected_gam_order_id": None},
                )
                db_session.add(workflow_step)

                # Create object mapping to link this step with the media buy
                object_mapping = ObjectWorkflowMapping(
                    object_type="media_buy", object_id=media_buy_id, step_id=step_id, action="create"
                )
                db_session.add(object_mapping)

                db_session.commit()

                self.log(f"✓ Created manual workflow step {step_id} for GAM order creation")
                self.audit_logger.log_success(f"Created manual order creation workflow step: {step_id}")

                # Send Slack notification if configured
                self._send_workflow_notification(step_id, action_details)

        except Exception as e:
            error_msg = f"Failed to create manual order workflow step for {media_buy_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            self.audit_logger.log_warning(error_msg)

    def _send_workflow_notification(self, step_id: str, action_details: dict) -> None:
        """Send Slack notification for workflow step if configured.

        Args:
            step_id: The workflow step ID
            action_details: Details about the workflow step
        """
        try:
            from src.core.config_loader import get_tenant_config

            tenant_config = get_tenant_config(self.tenant_id)
            slack_webhook_url = tenant_config.get("slack", {}).get("webhook_url")

            if not slack_webhook_url:
                self.log("[yellow]No Slack webhook configured - skipping notification[/yellow]")
                return

            import requests

            action_type = action_details.get("action_type", "workflow_step")
            automation_mode = action_details.get("automation_mode", "unknown")

            if action_type == "create_gam_order":
                title = "🔨 Manual GAM Order Creation Required"
                color = "#FF9500"  # Orange
                description = "Manual mode activated - human intervention needed to create GAM order"
            elif action_type == "activate_gam_order":
                title = "✅ GAM Order Activation Approval Required"
                color = "#FFD700"  # Gold
                description = "Order created successfully - approval needed for activation"
            else:
                title = "🔔 Workflow Step Requires Attention"
                color = "#36A2EB"  # Blue
                description = f"Workflow step {step_id} needs human intervention"

            # Build Slack message
            slack_payload = {
                "attachments": [
                    {
                        "color": color,
                        "title": title,
                        "text": description,
                        "fields": [
                            {"title": "Step ID", "value": step_id, "short": True},
                            {
                                "title": "Automation Mode",
                                "value": automation_mode.replace("_", " ").title(),
                                "short": True,
                            },
                            {
                                "title": "Action Required",
                                "value": action_details.get("instructions", ["Check admin dashboard"])[0],
                                "short": False,
                            },
                        ],
                        "footer": "AdCP Sales Agent",
                        "ts": int(datetime.now().timestamp()),
                    }
                ]
            }

            # Send notification
            response = requests.post(
                slack_webhook_url, json=slack_payload, timeout=10, headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                self.log(f"✓ Sent Slack notification for workflow step {step_id}")
                self.audit_logger.log_success(f"Sent Slack notification for workflow step: {step_id}")
            else:
                self.log(f"[yellow]Slack notification failed with status {response.status_code}[/yellow]")

        except Exception as e:
            self.log(f"[yellow]Failed to send Slack notification: {str(e)}[/yellow]")
            # Don't fail the workflow creation if notification fails

    def archive_order(self, order_id: str) -> bool:
        """Archive a GAM order for cleanup purposes.

        Args:
            order_id: The GAM order ID to archive

        Returns:
            bool: True if archival succeeded, False otherwise
        """
        self.log(f"[bold yellow]Archiving GAM Order {order_id} for cleanup[/bold yellow]")

        if self.dry_run:
            self.log(f"Would call: order_service.performOrderAction(ArchiveOrders, {order_id})")
            return True

        try:
            from googleads import ad_manager

            order_service = self.client.GetService("OrderService")

            # Use ArchiveOrders action
            archive_action = {"xsi_type": "ArchiveOrders"}

            order_statement_builder = ad_manager.StatementBuilder()
            order_statement_builder.Where("id = :orderId")
            order_statement_builder.WithBindVariable("orderId", int(order_id))
            order_statement = order_statement_builder.ToStatement()

            result = order_service.performOrderAction(archive_action, order_statement)

            if result and result.get("numChanges", 0) > 0:
                self.log(f"✓ Successfully archived GAM Order {order_id}")
                self.audit_logger.log_success(f"Archived GAM Order {order_id}")
                return True
            else:
                self.log(
                    f"[yellow]Warning: No changes made when archiving Order {order_id} (may already be archived)[/yellow]"
                )
                return True  # Consider this successful

        except Exception as e:
            error_msg = f"Failed to archive GAM Order {order_id}: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            self.audit_logger.log_warning(error_msg)
            return False

    def get_advertisers(self) -> list[dict[str, Any]]:
        """Get list of advertisers (companies) from GAM for advertiser selection.

        Returns:
            List of advertisers with id, name, and type for dropdown selection
        """
        self.log("[bold]GoogleAdManager.get_advertisers[/bold] - Loading GAM advertisers")

        if self.dry_run:
            self.log("Would call: company_service.getCompaniesByStatement(WHERE type='ADVERTISER')")
            # Return mock data for dry-run
            return [
                {"id": "123456789", "name": "Test Advertiser 1", "type": "ADVERTISER"},
                {"id": "987654321", "name": "Test Advertiser 2", "type": "ADVERTISER"},
                {"id": "456789123", "name": "Test Advertiser 3", "type": "ADVERTISER"},
            ]

        try:
            from googleads import ad_manager

            company_service = self.client.GetService("CompanyService")

            # Create statement to get only advertisers
            statement_builder = ad_manager.StatementBuilder()
            statement_builder.Where("type = :type")
            statement_builder.WithBindVariable("type", "ADVERTISER")
            statement_builder.OrderBy("name", ascending=True)
            statement = statement_builder.ToStatement()

            # Get companies from GAM
            response = company_service.getCompaniesByStatement(statement)

            advertisers = []
            if response and "results" in response:
                for company in response["results"]:
                    advertisers.append({"id": str(company["id"]), "name": company["name"], "type": company["type"]})

            self.log(f"✓ Retrieved {len(advertisers)} advertisers from GAM")
            return advertisers

        except Exception as e:
            error_msg = f"Failed to retrieve GAM advertisers: {str(e)}"
            self.log(f"[red]Error: {error_msg}[/red]")
            self.audit_logger.log_warning(error_msg)
            raise Exception(error_msg)

    def add_creative_assets(
        self, media_buy_id: str, assets: list[dict[str, Any]], today: datetime
    ) -> list[AssetStatus]:
        """Creates a new Creative in GAM and associates it with LineItems."""
        self.log(f"[bold]GoogleAdManager.add_creative_assets[/bold] for order '{media_buy_id}'")
        self.log(f"Adding {len(assets)} creative assets")

        if not self.dry_run:
            creative_service = self.client.GetService("CreativeService")
            lica_service = self.client.GetService("LineItemCreativeAssociationService")
            line_item_service = self.client.GetService("LineItemService")

        created_asset_statuses = []

        # Create a mapping from package_id (which is the line item name) to line_item_id
        # Also collect creative placeholders from all line items
        if not self.dry_run:
            statement = (
                self.client.new_statement_builder()
                .where("orderId = :orderId")
                .with_bind_variable("orderId", int(media_buy_id))
            )
            response = line_item_service.getLineItemsByStatement(statement.ToStatement())
            line_items = response.get("results", [])
            line_item_map = {item["name"]: item["id"] for item in line_items}

            # Collect all creative placeholders from line items for size validation
            creative_placeholders = {}
            for line_item in line_items:
                package_name = line_item["name"]
                placeholders = line_item.get("creativePlaceholders", [])
                creative_placeholders[package_name] = placeholders

        else:
            # In dry-run mode, create a mock line item map and placeholders
            line_item_map = {"mock_package": "mock_line_item_123"}
            creative_placeholders = {
                "mock_package": [
                    {"size": {"width": 300, "height": 250}, "creativeSizeType": "PIXEL"},
                    {"size": {"width": 728, "height": 90}, "creativeSizeType": "PIXEL"},
                ]
            }

        for asset in assets:
            # Validate creative asset against GAM requirements
            validation_issues = self._validate_creative_for_gam(asset)

            # Add creative size validation against placeholders
            size_validation_issues = self._validate_creative_size_against_placeholders(asset, creative_placeholders)
            validation_issues.extend(size_validation_issues)

            if validation_issues:
                self.log(f"[red]Creative {asset['creative_id']} failed GAM validation:[/red]")
                for issue in validation_issues:
                    self.log(f"  - {issue}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                continue

            # Determine creative type using AdCP v1.3+ logic
            creative_type = self._get_creative_type(asset)

            if creative_type == "vast":
                # VAST is handled at line item level, not creative level
                self.log(f"VAST creative {asset['creative_id']} - configuring at line item level")
                self._configure_vast_for_line_items(media_buy_id, asset, line_item_map)
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))
                continue

            # Get placeholders for this asset's package assignments
            asset_placeholders = []
            for pkg_id in asset.get("package_assignments", []):
                if pkg_id in creative_placeholders:
                    asset_placeholders.extend(creative_placeholders[pkg_id])

            # Create GAM creative object
            try:
                creative = self._create_gam_creative(asset, creative_type, asset_placeholders)
                if not creative:
                    self.log(f"Skipping unsupported creative {asset['creative_id']} with type: {creative_type}")
                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                    continue
            except ValueError as e:
                self.log(f"[red]Creative {asset['creative_id']} failed dimension validation: {e}[/red]")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))
                continue

            if self.dry_run:
                self.log(f"Would call: creative_service.createCreatives(['{creative['name']}'])")
                self.log(f"  Type: {creative.get('xsi_type', 'Unknown')}")
                self.log(f"  Size: {creative['size']['width']}x{creative['size']['height']}")
                self.log(f"  Destination URL: {creative['destinationUrl']}")
                created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))
            else:
                try:
                    created_creatives = creative_service.createCreatives([creative])
                    if not created_creatives:
                        raise Exception(f"Failed to create creative for asset {asset['creative_id']}")

                    creative_id = created_creatives[0]["id"]
                    self.log(f"✓ Created GAM Creative with ID: {creative_id}")

                    # Associate the creative with the assigned line items
                    line_item_ids_to_associate = [
                        line_item_map[pkg_id] for pkg_id in asset["package_assignments"] if pkg_id in line_item_map
                    ]

                    if line_item_ids_to_associate:
                        licas = [
                            {"lineItemId": line_item_id, "creativeId": creative_id}
                            for line_item_id in line_item_ids_to_associate
                        ]
                        lica_service.createLineItemCreativeAssociations(licas)
                        self.log(
                            f"✓ Associated creative {creative_id} with {len(line_item_ids_to_associate)} line items."
                        )
                    else:
                        self.log(
                            f"[yellow]Warning: No matching line items found for creative {creative_id} package assignments.[/yellow]"
                        )

                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="approved"))

                except Exception as e:
                    self.log(f"[red]Error creating GAM Creative or LICA for asset {asset['creative_id']}: {e}[/red]")
                    created_asset_statuses.append(AssetStatus(creative_id=asset["creative_id"], status="failed"))

        return created_asset_statuses

    def _get_creative_type(self, asset: dict[str, Any]) -> str:
        """Determine the creative type based on AdCP v1.3+ fields."""
        # Check AdCP v1.3+ fields first
        if asset.get("snippet") and asset.get("snippet_type"):
            if asset["snippet_type"] in ["vast_xml", "vast_url"]:
                return "vast"
            else:
                return "third_party_tag"
        elif asset.get("template_variables"):
            return "native"
        elif asset.get("media_url") or asset.get("media_data"):
            # Check if HTML5 based on file extension or format
            media_url = asset.get("media_url", "")
            format_str = asset.get("format", "")
            if (
                media_url.lower().endswith((".html", ".htm", ".html5", ".zip"))
                or "html5" in format_str.lower()
                or "rich_media" in format_str.lower()
            ):
                return "html5"
            else:
                return "hosted_asset"
        else:
            # Auto-detect from legacy patterns for backward compatibility
            url = asset.get("url", "")
            format_str = asset.get("format", "")

            if self._is_html_snippet(url):
                return "third_party_tag"
            elif "native" in format_str:
                return "native"
            elif url and (".xml" in url.lower() or "vast" in url.lower()):
                return "vast"
            elif (
                url.lower().endswith((".html", ".htm", ".html5", ".zip"))
                or "html5" in format_str.lower()
                or "rich_media" in format_str.lower()
            ):
                return "html5"
            else:
                return "hosted_asset"  # Default

    def _validate_creative_for_gam(self, asset: dict[str, Any]) -> list[str]:
        """
        Validate creative asset against GAM requirements before API submission.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        return self.validator.validate_creative_asset(asset)

    def _validate_creative_size_against_placeholders(
        self, asset: dict[str, Any], creative_placeholders: dict[str, list]
    ) -> list[str]:
        """
        Validate that creative format and asset requirements match available LineItem placeholders.

        Args:
            asset: Creative asset dictionary containing format and package assignments
            creative_placeholders: Dict mapping package names to their placeholder lists

        Returns:
            List of validation error messages (empty if valid)
        """
        validation_errors = []

        # First validate that the asset conforms to its format requirements
        format_errors = self._validate_asset_against_format_requirements(asset)
        validation_errors.extend(format_errors)

        # Get creative FORMAT dimensions (not asset dimensions) for placeholder validation
        # For backward compatibility, if format field is missing, try to use asset dimensions
        format_id = asset.get("format", "")
        format_width, format_height = None, None

        if format_id:
            # If format is specified, use strict format-based validation
            try:
                format_width, format_height = self._get_format_dimensions(format_id)
            except ValueError as e:
                validation_errors.append(str(e))
                return validation_errors
        else:
            # For backward compatibility: if no format specified, use asset dimensions if available
            if asset.get("width") and asset.get("height"):
                format_width, format_height = asset["width"], asset["height"]
                self.log(
                    f"⚠️  Using asset dimensions for placeholder validation (format field missing): {format_width}x{format_height}"
                )
            else:
                # No format and no dimensions - this is a validation error
                validation_errors.append(
                    f"Creative {asset.get('creative_id')} missing both format specification and width/height dimensions"
                )
                return validation_errors

        # Get placeholders for this asset's package assignments
        package_assignments = asset.get("package_assignments", [])

        # If no package assignments, skip placeholder validation entirely
        # This maintains backward compatibility with tests and simple scenarios
        if not package_assignments:
            return validation_errors

        # Check if any assigned package has a matching placeholder for the FORMAT size
        found_match = False
        available_sizes = set()

        for pkg_id in package_assignments:
            if pkg_id in creative_placeholders:
                placeholders = creative_placeholders[pkg_id]
                for placeholder in placeholders:
                    size = placeholder.get("size", {})
                    placeholder_width = size.get("width")
                    placeholder_height = size.get("height")

                    if placeholder_width and placeholder_height:
                        available_sizes.add(f"{placeholder_width}x{placeholder_height}")

                        if placeholder_width == format_width and placeholder_height == format_height:
                            found_match = True
                            break

                if found_match:
                    break

        if not found_match and available_sizes:
            validation_errors.append(
                f"Creative format {format_id} ({format_width}x{format_height}) does not match any LineItem placeholder. "
                f"Available sizes: {', '.join(sorted(available_sizes))}. "
                f"Creative will be rejected by GAM - please use matching format dimensions."
            )

        return validation_errors

    def _validate_asset_against_format_requirements(self, asset: dict[str, Any]) -> list[str]:
        """
        Validate that asset dimensions and properties conform to format asset requirements.

        Args:
            asset: Creative asset dictionary

        Returns:
            List of validation error messages (empty if valid)
        """
        validation_errors = []
        format_id = asset.get("format", "")

        if not format_id:
            return validation_errors  # Format validation handled elsewhere

        # Get format definition from registry
        try:
            from src.core.schemas import FORMAT_REGISTRY

            if format_id not in FORMAT_REGISTRY:
                return validation_errors  # Unknown format handled elsewhere

            format_def = FORMAT_REGISTRY[format_id]
            if not format_def.assets_required:
                return validation_errors  # No asset requirements to validate

        except Exception as e:
            self.log(f"⚠️ Error accessing format registry for asset validation: {e}")
            return validation_errors

        # Validate asset against format asset requirements
        asset_width = asset.get("width")
        asset_height = asset.get("height")
        asset_type = self._determine_asset_type(asset)

        # Find matching asset requirement
        matching_requirement = None
        for req in format_def.assets_required:
            if req.asset_type == asset_type or req.asset_type == "image":  # Default to image for display assets
                matching_requirement = req
                break

        if not matching_requirement:
            # No specific requirement found - this might be okay for some formats
            return validation_errors

        req_dict = matching_requirement.requirements or {}

        # Validate dimensions if specified in requirements
        if asset_width and asset_height:
            # Check exact dimensions
            if "width" in req_dict and "height" in req_dict:
                required_width = req_dict["width"]
                required_height = req_dict["height"]
                if isinstance(required_width, int) and isinstance(required_height, int):
                    if asset_width != required_width or asset_height != required_height:
                        validation_errors.append(
                            f"Asset dimensions {asset_width}x{asset_height} do not match "
                            f"format requirement {required_width}x{required_height} for {asset_type} in {format_id}"
                        )

            # Check minimum dimensions
            if "min_width" in req_dict and asset_width < req_dict["min_width"]:
                validation_errors.append(
                    f"Asset width {asset_width} below minimum {req_dict['min_width']} for {asset_type} in {format_id}"
                )
            if "min_height" in req_dict and asset_height < req_dict["min_height"]:
                validation_errors.append(
                    f"Asset height {asset_height} below minimum {req_dict['min_height']} for {asset_type} in {format_id}"
                )

            # Check maximum dimensions (if specified)
            if "max_width" in req_dict and asset_width > req_dict["max_width"]:
                validation_errors.append(
                    f"Asset width {asset_width} exceeds maximum {req_dict['max_width']} for {asset_type} in {format_id}"
                )
            if "max_height" in req_dict and asset_height > req_dict["max_height"]:
                validation_errors.append(
                    f"Asset height {asset_height} exceeds maximum {req_dict['max_height']} for {asset_type} in {format_id}"
                )

        return validation_errors

    def _determine_asset_type(self, asset: dict[str, Any]) -> str:
        """Determine the asset type based on asset properties."""
        # Check if it's a video asset
        if asset.get("duration") or "video" in asset.get("format", "").lower():
            return "video"

        # Check if it's HTML/rich media
        url = asset.get("url", "")
        if any(tag in asset.get("tag", "") for tag in ["<html", "<div", "<script"]) or url.endswith((".html", ".js")):
            return "html"

        # Default to image for display creatives
        return "image"

    def _is_html_snippet(self, content: str) -> bool:
        """Detect if content is HTML/JS snippet rather than URL."""
        if not content:
            return False
        html_indicators = ["<script", "<iframe", "<ins", "<div", "document.write", "innerHTML"]
        return any(indicator in content for indicator in html_indicators)

    def _create_gam_creative(
        self, asset: dict[str, Any], creative_type: str, placeholders: list[dict] = None
    ) -> dict[str, Any] | None:
        """Create the appropriate GAM creative object based on creative type."""
        base_creative = {
            "advertiserId": self.company_id,
            "name": asset["name"],
            "destinationUrl": asset.get("click_url", ""),
        }

        if creative_type == "third_party_tag":
            return self._create_third_party_creative(asset, base_creative, placeholders)
        elif creative_type == "native":
            return self._create_native_creative(asset, base_creative, placeholders)
        elif creative_type == "html5":
            return self._create_html5_creative(asset, base_creative, placeholders)
        elif creative_type == "hosted_asset":
            return self._create_hosted_asset_creative(asset, base_creative, placeholders)
        else:
            self.log(f"Unknown creative type: {creative_type}")
            return None

    def _create_third_party_creative(
        self, asset: dict[str, Any], base_creative: dict, placeholders: list[dict] = None
    ) -> dict[str, Any]:
        """Create a ThirdPartyCreative for tag-based delivery using AdCP v1.3+ fields."""
        width, height = self._get_creative_dimensions(asset, placeholders)

        # Get snippet from AdCP v1.3+ field
        snippet = asset.get("snippet")
        if not snippet:
            # Fallback for legacy support
            if self._is_html_snippet(asset.get("url", "")):
                snippet = asset["url"]
            else:
                raise ValueError(f"No snippet found for third-party creative {asset['creative_id']}")

        creative = {
            **base_creative,
            "xsi_type": "ThirdPartyCreative",
            "size": {"width": width, "height": height},
            "snippet": snippet,
            "isSafeFrameCompatible": True,  # Default to safe
            "isSSLScanRequired": True,  # Default to secure
        }

        # Add optional fields from delivery_settings
        if "delivery_settings" in asset and asset["delivery_settings"]:
            settings = asset["delivery_settings"]
            if "safe_frame_compatible" in settings:
                creative["isSafeFrameCompatible"] = settings["safe_frame_compatible"]
            if "ssl_required" in settings:
                creative["isSSLScanRequired"] = settings["ssl_required"]

        # Add impression tracking URLs using unified method
        self._add_tracking_urls_to_creative(creative, asset)

        return creative

    def _create_native_creative(
        self, asset: dict[str, Any], base_creative: dict, placeholders: list[dict] = None
    ) -> dict[str, Any]:
        """Create a TemplateCreative for native ads."""
        # Native ads use 1x1 size convention
        creative = {
            **base_creative,
            "xsi_type": "TemplateCreative",
            "size": {"width": 1, "height": 1},
            "creativeTemplateId": self._get_native_template_id(asset),
            "creativeTemplateVariableValues": self._build_native_template_variables(asset),
        }

        # Add impression tracking URLs using unified method
        self._add_tracking_urls_to_creative(creative, asset)

        return creative

    def _create_html5_creative(
        self, asset: dict[str, Any], base_creative: dict, placeholders: list[dict] = None
    ) -> dict[str, Any]:
        """Create an Html5Creative for rich media HTML5 ads."""
        width, height = self._get_creative_dimensions(asset, placeholders)

        creative = {
            **base_creative,
            "xsi_type": "Html5Creative",
            "size": {"width": width, "height": height},
            "htmlAsset": {
                "htmlSource": self._get_html5_source(asset),
                "size": {"width": width, "height": height},
            },
            "overrideSize": False,  # Use the creative size for display
            "isInterstitial": False,  # Default to non-interstitial
        }

        # Add backup image if provided (AdCP v1.3+ feature)
        if "backup_image_url" in asset:
            creative["backupImageAsset"] = {
                "assetUrl": asset["backup_image_url"],
                "size": {"width": width, "height": height},
            }

        # Configure interstitial setting if specified
        if "delivery_settings" in asset and asset["delivery_settings"]:
            settings = asset["delivery_settings"]
            if "interstitial" in settings:
                creative["isInterstitial"] = settings["interstitial"]
            if "override_size" in settings:
                creative["overrideSize"] = settings["override_size"]

        # Add impression tracking URLs using unified method
        self._add_tracking_urls_to_creative(creative, asset)

        return creative

    def _get_html5_source(self, asset: dict[str, Any]) -> str:
        """Get HTML5 source content from asset."""
        media_url = asset.get("media_url", "")

        # For HTML5 creatives, we need to handle different scenarios:
        # 1. Direct HTML content in media_url (if it's a data URL or inline HTML)
        # 2. ZIP file URL containing HTML5 creative assets
        # 3. Direct HTML file URL

        if media_url.startswith("data:text/html"):
            # Extract HTML content from data URL
            return media_url.split(",", 1)[1] if "," in media_url else ""
        elif media_url.lower().endswith(".zip"):
            # For ZIP files, GAM expects the URL to be referenced
            # The actual HTML content will be extracted by GAM
            return f"<!-- HTML5 Creative ZIP: {media_url} -->"
        else:
            # For direct HTML files or URLs, reference the URL
            # In real implementation, you might fetch and validate the HTML content
            return f"<!-- HTML5 Creative URL: {media_url} -->"

    def _create_hosted_asset_creative(
        self, asset: dict[str, Any], base_creative: dict, placeholders: list[dict] = None
    ) -> dict[str, Any]:
        """Create ImageCreative or VideoCreative for hosted assets."""
        format_str = asset.get("format", "")
        width, height = self._get_creative_dimensions(asset, placeholders)

        creative = {
            **base_creative,
            "size": {"width": width, "height": height},
        }

        # Check if we have binary data to upload
        if asset.get("media_data"):
            # Upload binary asset to GAM and get asset ID
            uploaded_asset = self._upload_binary_asset(asset)
            if format_str.startswith("video"):
                creative["xsi_type"] = "VideoCreative"
                creative["videoAsset"] = uploaded_asset
                creative["duration"] = asset.get("duration", 0)  # Duration in milliseconds
            else:  # Default to image
                creative["xsi_type"] = "ImageCreative"
                creative["primaryImageAsset"] = uploaded_asset
        else:
            # Fallback to URL-based assets (legacy behavior)
            if format_str.startswith("video"):
                creative["xsi_type"] = "VideoCreative"
                creative["videoSourceUrl"] = asset.get("media_url") or asset.get("url")
                creative["duration"] = asset.get("duration", 0)  # Duration in milliseconds
            else:  # Default to image
                creative["xsi_type"] = "ImageCreative"
                creative["primaryImageAsset"] = {"assetUrl": asset.get("media_url") or asset.get("url")}

        # Add impression tracking URLs for hosted assets (both image and video)
        self._add_tracking_urls_to_creative(creative, asset)

        return creative

    def _get_creative_dimensions(self, asset: dict[str, Any], placeholders: list[dict] = None) -> tuple[int, int]:
        """Get creative FORMAT dimensions for GAM creative creation and placeholder validation.

        Note: This returns FORMAT dimensions, not asset dimensions. The format defines the
        overall creative size that GAM will use, while individual assets within the format
        may have different dimensions as specified in the format's asset requirements.

        Args:
            asset: Creative asset dictionary containing format information
            placeholders: List of creative placeholders from LineItem(s)

        Returns:
            Tuple of (width, height) format dimensions for GAM creative

        Raises:
            ValueError: If creative format dimensions cannot be determined or don't match placeholders
        """
        # Use FORMAT dimensions for GAM creative size, with asset dimensions as fallback
        format_id = asset.get("format", "")
        format_width, format_height = None, None

        if format_id:
            # If format is specified, use format-based dimensions
            try:
                format_width, format_height = self._get_format_dimensions(format_id)
                self.log(
                    f"Using format dimensions for GAM creative: {format_width}x{format_height} (format: {format_id})"
                )
            except ValueError as e:
                raise ValueError(f"Creative {asset.get('creative_id', 'unknown')}: {str(e)}")
        else:
            # For backward compatibility: if no format specified, use asset dimensions
            if asset.get("width") and asset.get("height"):
                format_width, format_height = asset["width"], asset["height"]
                self.log(f"📐 Using asset dimensions for GAM creative: {format_width}x{format_height}")
            else:
                raise ValueError(
                    f"Creative {asset.get('creative_id', 'unknown')}: No format specified and no width/height dimensions available"
                )

        # Validate asset dimensions against format requirements separately
        asset_errors = self._validate_asset_against_format_requirements(asset)
        if asset_errors:
            self.log(
                f"⚠️ Asset validation warnings for {asset.get('creative_id', 'unknown')}: {'; '.join(asset_errors)}"
            )
            # Note: We log warnings but don't fail here - some asset validation might be advisory

        # If we have placeholders, validate format size matches them
        if placeholders:
            # Find a matching placeholder for the FORMAT size
            for placeholder in placeholders:
                size = placeholder.get("size", {})
                placeholder_width = size.get("width")
                placeholder_height = size.get("height")

                if placeholder_width == format_width and placeholder_height == format_height:
                    self.log(f"✓ Matched format size {format_width}x{format_height} to LineItem placeholder")
                    return format_width, format_height

            # If no exact match, FAIL - format size must match placeholder
            available_sizes = [f"{p['size']['width']}x{p['size']['height']}" for p in placeholders if "size" in p]
            error_msg = (
                f"Creative format {format_id} ({format_width}x{format_height}) does not match any LineItem placeholder. "
                f"Available sizes: {', '.join(available_sizes)}. "
                f"Creative will be rejected by GAM - format must match placeholder dimensions."
            )
            self.log(f"❌ {error_msg}")
            raise ValueError(error_msg)

        # No placeholders provided - use format dimensions
        self.log(f"📐 Using format dimensions for GAM creative: {format_width}x{format_height}")
        return format_width, format_height

    def _get_format_dimensions(self, format_id: str) -> tuple[int, int]:
        """Get dimensions from format registry or database.

        Args:
            format_id: Format identifier (e.g., "display_300x250")

        Returns:
            Tuple of (width, height) dimensions

        Raises:
            ValueError: If format dimensions cannot be determined from registry or database
        """
        if not format_id:
            raise ValueError(
                "Format ID is required - cannot determine creative dimensions without format specification"
            )

        # First try format registry (hardcoded formats in schemas.py)
        try:
            from src.core.schemas import FORMAT_REGISTRY

            if format_id in FORMAT_REGISTRY:
                format_obj = FORMAT_REGISTRY[format_id]
                requirements = format_obj.requirements or {}

                # Handle different requirement structures
                if "width" in requirements and "height" in requirements:
                    width = requirements["width"]
                    height = requirements["height"]

                    # Ensure they're integers (some formats use strings like "100%")
                    if isinstance(width, int) and isinstance(height, int):
                        self.log(f"📋 Found dimensions in format registry for {format_id}: {width}x{height}")
                        return width, height

        except Exception as e:
            self.log(f"⚠️ Error accessing format registry: {e}")

        # Second try database lookup (only if not in dry-run mode to avoid mocking issues)
        if not self.dry_run:
            try:
                from sqlalchemy import select

                from src.core.database.database_session import get_db_session
                from src.core.database.models import CreativeFormat

                with get_db_session() as session:
                    # First try tenant-specific format, then standard/foundational
                    stmt = (
                        select(CreativeFormat)
                        .where(
                            CreativeFormat.format_id == format_id, CreativeFormat.tenant_id.in_([self.tenant_id, None])
                        )
                        .order_by(
                            # Prefer tenant-specific, then standard, then foundational
                            CreativeFormat.tenant_id.desc().nullslast(),
                            CreativeFormat.is_standard.desc(),
                            CreativeFormat.is_foundational.desc(),
                        )
                    )
                    format_record = session.scalars(stmt).first()

                    if format_record and format_record.width and format_record.height:
                        self.log(
                            f"💾 Found dimensions in database for {format_id}: {format_record.width}x{format_record.height}"
                        )
                        return format_record.width, format_record.height

            except Exception as e:
                self.log(f"⚠️ Error accessing database for format lookup: {e}")

        # Last resort: try to extract dimensions from format name (e.g., "display_300x250")
        # This handles test formats and formats following standard naming conventions
        import re

        dimension_match = re.search(r"(\d+)x(\d+)", format_id)
        if dimension_match:
            width, height = int(dimension_match.group(1)), int(dimension_match.group(2))
            self.log(f"🔍 Extracted dimensions from format name '{format_id}': {width}x{height}")
            return width, height

        # No fallbacks - fail if we can't get proper dimensions
        raise ValueError(
            f"Cannot determine dimensions for format '{format_id}'. "
            f"Format not found in registry or database. "
            f"Please use explicit width/height fields or ensure format is properly defined."
        )

    def _add_tracking_urls_to_creative(self, creative: dict[str, Any], asset: dict[str, Any]) -> None:
        """
        Add impression tracking URLs to GAM creative object.

        Supports tracking for all creative types:
        - ThirdPartyCreative: thirdPartyImpressionTrackingUrls
        - ImageCreative/VideoCreative: thirdPartyImpressionUrls
        - TemplateCreative (native): handled via template variables

        Args:
            creative: GAM creative object to modify
            asset: Creative asset dictionary with tracking configuration
        """
        # Get tracking URLs from delivery_settings
        tracking_urls = []

        if "delivery_settings" in asset and asset["delivery_settings"]:
            settings = asset["delivery_settings"]
            if "tracking_urls" in settings:
                tracking_urls = settings["tracking_urls"]

        # Also check for direct tracking_urls field (AdCP v1.3+ support)
        if "tracking_urls" in asset:
            tracking_urls.extend(asset["tracking_urls"])

        # Add tracking URLs based on creative type
        if tracking_urls:
            creative_type = creative.get("xsi_type", "")

            if creative_type == "ThirdPartyCreative":
                # Third-party creatives use thirdPartyImpressionTrackingUrls
                creative["thirdPartyImpressionTrackingUrls"] = tracking_urls
                self.log(f"Added {len(tracking_urls)} third-party tracking URLs")

            elif creative_type in ["ImageCreative", "VideoCreative"]:
                # Hosted asset creatives use thirdPartyImpressionUrls
                creative["thirdPartyImpressionUrls"] = tracking_urls
                self.log(f"Added {len(tracking_urls)} impression tracking URLs to {creative_type}")

            elif creative_type == "TemplateCreative":
                # Native creatives: tracking should be handled via template variables
                self.log(
                    f"Note: {len(tracking_urls)} tracking URLs provided for native creative - should be handled via template variables"
                )

            else:
                self.log(f"Warning: Cannot add tracking URLs to unknown creative type: {creative_type}")

    def _upload_binary_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        """
        Upload binary asset data to GAM using CreativeAssetService.

        Args:
            asset: Creative asset dictionary containing media_data

        Returns:
            GAM CreativeAsset object with assetId

        Raises:
            Exception: If upload fails or media_data is invalid
        """
        if self.dry_run:
            self.log(f"Would upload binary asset for creative {asset['creative_id']}")
            return {
                "assetId": "mock_asset_123456",
                "fileName": asset.get("filename", f"{asset['creative_id']}.jpg"),
                "fileSize": len(asset.get("media_data", b"")),
            }

        media_data = asset.get("media_data")
        if not media_data:
            raise ValueError(f"No media_data found for asset {asset['creative_id']}")

        # Decode base64 if needed
        if isinstance(media_data, str):
            import base64

            try:
                media_data = base64.b64decode(media_data)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 media_data for asset {asset['creative_id']}: {e}")

        if not isinstance(media_data, bytes):
            raise ValueError(f"media_data must be bytes or base64 string for asset {asset['creative_id']}")

        # Get CreativeAssetService
        creative_asset_service = self.client.GetService("CreativeAssetService")

        # Determine content type from format or filename
        content_type = self._get_content_type(asset)
        filename = asset.get("filename") or f"{asset['creative_id']}.{self._get_file_extension(content_type)}"

        # Create CreativeAsset object
        creative_asset = {
            "assetByteArray": media_data,
            "fileName": filename,
        }

        try:
            self.log(f"Uploading {len(media_data)} bytes for creative {asset['creative_id']} as {filename}")

            # Upload the asset
            uploaded_assets = creative_asset_service.createAssets([creative_asset])

            if not uploaded_assets or len(uploaded_assets) == 0:
                raise Exception(f"Failed to upload asset for creative {asset['creative_id']}: No assets returned")

            uploaded_asset = uploaded_assets[0]
            self.log(f"✓ Uploaded asset with ID: {uploaded_asset['assetId']}")

            return uploaded_asset

        except Exception as e:
            self.log(f"[red]Error uploading binary asset for creative {asset['creative_id']}: {e}[/red]")
            raise

    def _get_content_type(self, asset: dict[str, Any]) -> str:
        """Determine content type from asset format or filename."""
        format_str = asset.get("format", "").lower()
        filename = asset.get("filename", "").lower()

        # Check format first
        if format_str.startswith("video"):
            if "mp4" in format_str or filename.endswith(".mp4"):
                return "video/mp4"
            elif "mov" in format_str or filename.endswith(".mov"):
                return "video/quicktime"
            elif "avi" in format_str or filename.endswith(".avi"):
                return "video/avi"
            else:
                return "video/mp4"  # Default video format
        else:
            # Image formats
            if filename.endswith(".png") or "png" in format_str:
                return "image/png"
            elif filename.endswith(".gif") or "gif" in format_str:
                return "image/gif"
            elif filename.endswith(".jpg") or filename.endswith(".jpeg") or "jpg" in format_str or "jpeg" in format_str:
                return "image/jpeg"
            else:
                return "image/jpeg"  # Default image format

    def _get_file_extension(self, content_type: str) -> str:
        """Get file extension from content type."""
        content_type_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "video/mp4": "mp4",
            "video/quicktime": "mov",
            "video/avi": "avi",
        }
        return content_type_map.get(content_type, "jpg")

    def _get_native_template_id(self, asset: dict[str, Any]) -> str:
        """Get or find a native template ID for this creative."""
        # Check if template ID is specified
        if "native_template_id" in asset and asset["native_template_id"]:
            return asset["native_template_id"]

        # For now, use a placeholder - in real implementation, would query GAM for available templates
        if self.dry_run:
            return "12345"  # Placeholder for dry run

        # In real implementation, would query CreativeTemplateService for native-eligible templates
        # and select the most appropriate one based on the asset components
        raise NotImplementedError("Native template discovery not yet implemented - specify native_template_id")

    def _build_native_template_variables(self, asset: dict[str, Any]) -> list[dict[str, Any]]:
        """Build template variables for native creative from AdCP v1.3+ template_variables."""
        variables = []

        # Get template variables from AdCP v1.3+ field
        template_vars = asset.get("template_variables")
        if not template_vars:
            raise ValueError(f"No template_variables found for native creative {asset['creative_id']}")

        # Map AdCP template variable names to GAM template variables
        # AdCP uses more standardized naming than our old approach
        variable_mappings = {
            "headline": "Headline",
            "body": "Body",
            "main_image_url": "MainImage",
            "logo_url": "Logo",
            "cta_text": "CallToAction",
            "advertiser_name": "Advertiser",
            "price": "Price",
            "star_rating": "StarRating",
        }

        for adcp_key, gam_var in variable_mappings.items():
            if adcp_key in template_vars:
                value_obj = {"uniqueName": gam_var}

                # Handle asset URLs vs text content based on field name
                if "_url" in adcp_key:
                    value_obj["assetUrl"] = template_vars[adcp_key]
                else:
                    value_obj["value"] = template_vars[adcp_key]

                variables.append(value_obj)

        return variables

    def _configure_vast_for_line_items(self, media_buy_id: str, asset: dict[str, Any], line_item_map: dict):
        """Configure VAST settings at the line item level (not creative level)."""
        # VAST configuration happens at line item creation time, not creative upload time
        # This is a placeholder for future VAST support
        self.log(f"VAST configuration for {asset['creative_id']} would be handled at line item level")
        if self.dry_run:
            self.log("Would update line items with VAST configuration:")
            self.log(f"  VAST URL: {asset.get('url') or asset.get('media_url')}")
            self.log(f"  Duration: {asset.get('duration', 0)} seconds")

    def check_media_buy_status(self, media_buy_id: str, today: datetime) -> CheckMediaBuyStatusResponse:
        """Checks the status of all LineItems in a GAM Order."""
        self.log(f"[bold]GoogleAdManager.check_media_buy_status[/bold] for order '{media_buy_id}'")

        if self.dry_run:
            self.log("Would call: line_item_service.getLineItemsByStatement()")
            self.log(f"  Query: WHERE orderId = {media_buy_id}")
            return CheckMediaBuyStatusResponse(
                media_buy_id=media_buy_id, status="delivering", last_updated=datetime.now().astimezone()
            )

        line_item_service = self.client.GetService("LineItemService")
        statement = (
            self.client.new_statement_builder()
            .where("orderId = :orderId")
            .with_bind_variable("orderId", int(media_buy_id))
        )

        try:
            response = line_item_service.getLineItemsByStatement(statement.ToStatement())
            line_items = response.get("results", [])

            if not line_items:
                return CheckMediaBuyStatusResponse(media_buy_id=media_buy_id, status="pending_creative")

            # Determine the overall status. This is a simplified logic.
            # A real implementation might need to handle more nuanced statuses.
            statuses = {item["status"] for item in line_items}

            overall_status = "live"
            if "PAUSED" in statuses:
                overall_status = "paused"
            elif all(s == "DELIVERING" for s in statuses):
                overall_status = "delivering"
            elif all(s == "COMPLETED" for s in statuses):
                overall_status = "completed"
            elif any(s in ["PENDING_APPROVAL", "DRAFT"] for s in statuses):
                overall_status = "pending_approval"

            # For delivery data, we'd need a reporting call.
            # For now, we'll return placeholder data.
            return CheckMediaBuyStatusResponse(
                media_buy_id=media_buy_id, status=overall_status, last_updated=datetime.now().astimezone()
            )

        except Exception as e:
            logger.error(f"Error checking media buy status in GAM: {e}")
            raise

    def get_media_buy_delivery(
        self, media_buy_id: str, date_range: ReportingPeriod, today: datetime
    ) -> AdapterGetMediaBuyDeliveryResponse:
        """Runs and parses a delivery report in GAM to get detailed performance data."""
        self.log(f"[bold]GoogleAdManager.get_media_buy_delivery[/bold] for order '{media_buy_id}'")
        self.log(f"Date range: {date_range.start.date()} to {date_range.end.date()}")

        if self.dry_run:
            # Simulate the report query
            self.log("Would call: report_service.runReportJob()")
            self.log("  Report Query:")
            self.log("    Dimensions: DATE, ORDER_ID, LINE_ITEM_ID, CREATIVE_ID")
            self.log("    Columns: AD_SERVER_IMPRESSIONS, AD_SERVER_CLICKS, AD_SERVER_CPM_AND_CPC_REVENUE")
            self.log(f"    Date Range: {date_range.start.date()} to {date_range.end.date()}")
            self.log(f"    Filter: ORDER_ID = {media_buy_id}")

            # Return simulated data
            simulated_impressions = random.randint(50000, 150000)
            simulated_spend = simulated_impressions * 0.01  # $10 CPM

            self.log(f"Would return: {simulated_impressions:,} impressions, ${simulated_spend:,.2f} spend")

            return AdapterGetMediaBuyDeliveryResponse(
                media_buy_id=media_buy_id,
                reporting_period=date_range,
                totals=DeliveryTotals(
                    impressions=simulated_impressions,
                    spend=simulated_spend,
                    clicks=int(simulated_impressions * 0.002),  # 0.2% CTR
                    video_completions=int(simulated_impressions * 0.7),  # 70% completion rate
                ),
                by_package=[],
                currency="USD",
            )

        report_service = self.client.GetService("ReportService")

        report_job = {
            "reportQuery": {
                "dimensions": ["DATE", "ORDER_ID", "LINE_ITEM_ID", "CREATIVE_ID"],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CTR",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",  # This is spend from the buyer's view
                    "VIDEO_COMPLETIONS",
                    "VIDEO_COMPLETION_RATE",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {
                    "year": date_range.start.year,
                    "month": date_range.start.month,
                    "day": date_range.start.day,
                },
                "endDate": {"year": date_range.end.year, "month": date_range.end.month, "day": date_range.end.day},
                "statement": self._create_order_statement(int(media_buy_id)),
            }
        }

        try:
            report_job_id = report_service.runReportJob(report_job)

            # Wait for completion with timeout
            max_wait = ReportingConfig.REPORT_TIMEOUT_SECONDS
            wait_time = 0
            poll_interval = ReportingConfig.POLL_INTERVAL_SECONDS

            while wait_time < max_wait:
                status = report_service.getReportJobStatus(report_job_id)
                if status == "COMPLETED":
                    break
                elif status == "FAILED":
                    raise Exception("GAM report job failed")

                time.sleep(poll_interval)
                wait_time += poll_interval

            if report_service.getReportJobStatus(report_job_id) != "COMPLETED":
                raise Exception(f"GAM report timed out after {max_wait} seconds")

            # Use modern ReportService method instead of deprecated GetDataDownloader
            try:
                download_url = report_service.getReportDownloadURL(report_job_id, "CSV_DUMP")
            except Exception as e:
                raise Exception(f"Failed to get GAM report download URL: {str(e)}") from e

            # Validate URL is from Google for security
            parsed_url = urlparse(download_url)
            if not parsed_url.hostname or not any(
                parsed_url.hostname.endswith(domain) for domain in ReportingConfig.ALLOWED_DOMAINS
            ):
                raise Exception(f"Invalid download URL: not from Google domain ({parsed_url.hostname})")

            # Download the report using requests with proper timeout and error handling
            try:
                response = requests.get(
                    download_url,
                    timeout=(ReportingConfig.HTTP_CONNECT_TIMEOUT, ReportingConfig.HTTP_READ_TIMEOUT),
                    headers={"User-Agent": ReportingConfig.USER_AGENT},
                    stream=True,  # For better memory handling of large files
                )
                response.raise_for_status()
            except requests.exceptions.Timeout as e:
                raise Exception(f"GAM report download timed out: {str(e)}") from e
            except requests.exceptions.RequestException as e:
                raise Exception(f"Failed to download GAM report: {str(e)}") from e

            # Parse the CSV data directly from the response with memory safety
            try:
                # The response content is gzipped CSV data
                with gzip.open(io.BytesIO(response.content), "rt") as gz_file:
                    report_csv = gz_file.read()

                # Limit CSV size to prevent memory issues
                if len(report_csv) > ReportingConfig.MAX_CSV_SIZE_BYTES:
                    logger.warning(
                        f"GAM report CSV size ({len(report_csv)} bytes) exceeds limit ({ReportingConfig.MAX_CSV_SIZE_BYTES} bytes)"
                    )
                    report_csv = report_csv[: ReportingConfig.MAX_CSV_SIZE_BYTES]

                report_reader = csv.reader(io.StringIO(report_csv))
            except Exception as e:
                raise Exception(f"Failed to parse GAM report CSV data: {str(e)}") from e

            # Skip header row
            header = next(report_reader)

            # Map columns to indices for robust parsing
            col_map = {col: i for i, col in enumerate(header)}

            totals = {"impressions": 0, "spend": 0.0, "clicks": 0, "video_completions": 0}
            by_package = {}

            for row in report_reader:
                impressions = int(row[col_map["AD_SERVER_IMPRESSIONS"]])
                spend = float(row[col_map["AD_SERVER_CPM_AND_CPC_REVENUE"]]) / 1000000  # Convert from micros
                clicks = int(row[col_map["AD_SERVER_CLICKS"]])
                video_completions = int(row[col_map["VIDEO_COMPLETIONS"]])
                line_item_id = row[col_map["LINE_ITEM_ID"]]

                totals["impressions"] += impressions
                totals["spend"] += spend
                totals["clicks"] += clicks
                totals["video_completions"] += video_completions

                if line_item_id not in by_package:
                    by_package[line_item_id] = {"impressions": 0, "spend": 0.0}

                by_package[line_item_id]["impressions"] += impressions
                by_package[line_item_id]["spend"] += spend

            return AdapterGetMediaBuyDeliveryResponse(
                media_buy_id=media_buy_id,
                reporting_period=date_range,
                totals=DeliveryTotals(**totals),
                by_package=[PackageDelivery(package_id=k, **v) for k, v in by_package.items()],
                currency="USD",
            )

        except Exception as e:
            logger.error(f"Error getting delivery report from GAM: {e}")
            raise

    def update_media_buy_performance_index(
        self, media_buy_id: str, package_performance: list[PackagePerformance]
    ) -> bool:
        logger.info("GAM Adapter: update_media_buy_performance_index called. (Not yet implemented)")
        return True

    def _get_order_line_items(self, order_id: str) -> list[dict]:
        """Get all line items for an order.

        Args:
            order_id: The GAM order ID

        Returns:
            List of line item dictionaries
        """
        if self.dry_run:
            self.log(f"Would call: line_item_service.getLineItemsByStatement(WHERE orderId={order_id})")
            # Return mock line items for dry run testing
            return [
                {"id": "123", "lineItemType": "NETWORK", "name": "Test Line Item 1"},
                {"id": "124", "lineItemType": "STANDARD", "name": "Test Line Item 2"},
            ]

        try:
            line_item_service = self.client.GetService("LineItemService")
            statement = (
                ad_manager.StatementBuilder().Where("orderId = :orderId").WithBindVariable("orderId", int(order_id))
            )

            response = line_item_service.getLineItemsByStatement(statement.ToStatement())
            return response.get("results", [])

        except Exception as e:
            self.log(f"[red]Error fetching line items for order {order_id}: {e}[/red]")
            return []

    def _check_order_has_guaranteed_items(self, order_id: str) -> tuple[bool, list[str]]:
        """Check if an order contains any guaranteed line items.

        Args:
            order_id: The GAM order ID

        Returns:
            Tuple of (has_guaranteed_items: bool, guaranteed_types: list[str])
        """
        line_items = self._get_order_line_items(order_id)
        guaranteed_types = []

        for line_item in line_items:
            line_item_type = line_item.get("lineItemType", "STANDARD")
            if line_item_type in GUARANTEED_LINE_ITEM_TYPES:
                guaranteed_types.append(line_item_type)

        has_guaranteed = len(guaranteed_types) > 0
        self.log(f"Order {order_id} has guaranteed items: {has_guaranteed} (types: {guaranteed_types})")
        return has_guaranteed, guaranteed_types

    def _is_admin_principal(self) -> bool:
        """Check if the current principal has admin privileges.

        Returns:
            True if principal is admin, False otherwise
        """
        # Check if principal has admin role or special admin flag
        platform_mappings = getattr(self.principal, "platform_mappings", {})
        gam_mappings = platform_mappings.get("google_ad_manager", {})
        is_admin = (
            gam_mappings.get("gam_admin", False)
            or gam_mappings.get("is_admin", False)
            or getattr(self.principal, "role", "") == "admin"
        )

        self.log(f"Principal {self.principal.name} admin check: {is_admin}")
        return is_admin

    def _get_order_status(self, order_id: str) -> str:
        """Get the current status of a GAM order.

        Args:
            order_id: The GAM order ID

        Returns:
            Order status string (e.g., 'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'PAUSED')
        """
        if self.dry_run:
            self.log(f"Would call: order_service.getOrdersByStatement(WHERE id={order_id})")
            return "DRAFT"  # Mock status for dry run

        try:
            order_service = self.client.GetService("OrderService")
            statement = ad_manager.StatementBuilder().Where("id = :orderId").WithBindVariable("orderId", int(order_id))

            response = order_service.getOrdersByStatement(statement.ToStatement())
            orders = response.get("results", [])

            if orders:
                status = orders[0].get("status", "UNKNOWN")
                self.log(f"Order {order_id} current status: {status}")
                return status
            else:
                self.log(f"[yellow]Warning: Order {order_id} not found[/yellow]")
                return "NOT_FOUND"

        except Exception as e:
            self.log(f"[red]Error fetching order status for {order_id}: {e}[/red]")
            return "ERROR"

    def _create_approval_workflow_step(self, media_buy_id: str):
        """Create a workflow step for order approval tracking."""
        try:
            import uuid
            from datetime import datetime

            from src.core.database.database_session import get_db_session
            from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

            with get_db_session() as db_session:
                # Create workflow step
                workflow_step = WorkflowStep(
                    step_id=str(uuid.uuid4()),
                    tenant_id=self.tenant_id,
                    workflow_id=f"approval_{media_buy_id}",
                    status="pending_approval",
                    step_type="order_approval",
                    created_at=datetime.now(),
                    metadata={"order_id": media_buy_id, "action": "submit_for_approval"},
                )
                db_session.add(workflow_step)

                # Create object workflow mapping
                mapping = ObjectWorkflowMapping(
                    object_type="media_buy",
                    object_id=media_buy_id,
                    workflow_id=f"approval_{media_buy_id}",
                    tenant_id=self.tenant_id,
                )
                db_session.add(mapping)

                db_session.commit()
                self.log(f"✓ Created approval workflow step for Order {media_buy_id}")

        except Exception as e:
            self.log(f"[yellow]Warning: Could not create workflow step: {e}[/yellow]")

    def _update_approval_workflow_step(self, media_buy_id: str, new_status: str):
        """Update an existing approval workflow step."""
        try:
            from datetime import datetime

            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import WorkflowStep

            with get_db_session() as db_session:
                stmt = select(WorkflowStep).filter_by(
                    tenant_id=self.tenant_id, workflow_id=f"approval_{media_buy_id}", step_type="order_approval"
                )
                workflow_step = db_session.scalars(stmt).first()

                if workflow_step:
                    workflow_step.status = new_status
                    workflow_step.updated_at = datetime.now()
                    workflow_step.metadata["approved_by"] = self.principal.name
                    db_session.commit()
                    self.log(f"✓ Updated workflow step for Order {media_buy_id} to {new_status}")

        except Exception as e:
            self.log(f"[yellow]Warning: Could not update workflow step: {e}[/yellow]")

    def update_media_buy(
        self, media_buy_id: str, action: str, package_id: str | None, budget: int | None, today: datetime
    ) -> UpdateMediaBuyResponse:
        """Updates an Order or LineItem in GAM using standardized actions."""
        self.log(
            f"[bold]GoogleAdManager.update_media_buy[/bold] for {media_buy_id} with action {action}",
            dry_run_prefix=False,
        )

        if action not in REQUIRED_UPDATE_ACTIONS:
            return UpdateMediaBuyResponse(
                status="failed", reason=f"Action '{action}' not supported. Supported actions: {REQUIRED_UPDATE_ACTIONS}"
            )

        if self.dry_run:
            if action == "pause_media_buy":
                self.log(f"Would pause Order {media_buy_id}")
                self.log(f"Would call: order_service.performOrderAction(PauseOrders, {media_buy_id})")
            elif action == "resume_media_buy":
                self.log(f"Would resume Order {media_buy_id}")
                self.log(f"Would call: order_service.performOrderAction(ResumeOrders, {media_buy_id})")
            elif action == "pause_package" and package_id:
                self.log(f"Would pause LineItem '{package_id}' in Order {media_buy_id}")
                self.log(
                    f"Would call: line_item_service.performLineItemAction(PauseLineItems, WHERE orderId={media_buy_id} AND name='{package_id}')"
                )
            elif action == "resume_package" and package_id:
                self.log(f"Would resume LineItem '{package_id}' in Order {media_buy_id}")
                self.log(
                    f"Would call: line_item_service.performLineItemAction(ResumeLineItems, WHERE orderId={media_buy_id} AND name='{package_id}')"
                )
            elif (
                action in ["update_package_budget", "update_package_impressions"] and package_id and budget is not None
            ):
                self.log(f"Would update budget for LineItem '{package_id}' to ${budget}")
                if action == "update_package_impressions":
                    self.log("Would directly set impression goal")
                else:
                    self.log("Would calculate new impression goal based on CPM")
                self.log("Would call: line_item_service.updateLineItems([updated_line_item])")
            elif action == "activate_order":
                # Check for guaranteed line items
                has_guaranteed, guaranteed_types = self._check_order_has_guaranteed_items(media_buy_id)
                if has_guaranteed:
                    return UpdateMediaBuyResponse(
                        status="failed",
                        reason=f"Cannot auto-activate order with guaranteed line items ({guaranteed_types}). Use submit_for_approval instead.",
                    )
                self.log(f"Would activate non-guaranteed Order {media_buy_id}")
                self.log(f"Would call: order_service.performOrderAction(ResumeOrders, {media_buy_id})")
                self.log(
                    f"Would call: line_item_service.performLineItemAction(ActivateLineItems, WHERE orderId={media_buy_id})"
                )
            elif action == "submit_for_approval":
                self.log(f"Would submit Order {media_buy_id} for approval")
                self.log(f"Would call: order_service.performOrderAction(SubmitOrdersForApproval, {media_buy_id})")
            elif action == "approve_order":
                if not self._is_admin_principal():
                    return UpdateMediaBuyResponse(status="failed", reason="Only admin users can approve orders")
                self.log(f"Would approve Order {media_buy_id}")
                self.log(f"Would call: order_service.performOrderAction(ApproveOrders, {media_buy_id})")
            elif action == "archive_order":
                self.log(f"Would archive Order {media_buy_id}")
                self.log(f"Would call: order_service.performOrderAction(ArchiveOrders, {media_buy_id})")

            return UpdateMediaBuyResponse(
                status="accepted",
                implementation_date=today + timedelta(days=1),
                detail=f"Would {action} in Google Ad Manager",
            )
        else:
            try:
                if action in ["pause_media_buy", "resume_media_buy"]:
                    order_service = self.client.GetService("OrderService")

                    if action == "pause_media_buy":
                        order_action = {"xsi_type": "PauseOrders"}
                    else:
                        order_action = {"xsi_type": "ResumeOrders"}

                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("id = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    result = order_service.performOrderAction(order_action, statement.ToStatement())

                    if result and result["numChanges"] > 0:
                        self.log(f"✓ Successfully performed {action} on Order {media_buy_id}")
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No orders were updated")

                elif action in ["pause_package", "resume_package"] and package_id:
                    line_item_service = self.client.GetService("LineItemService")

                    if action == "pause_package":
                        line_item_action = {"xsi_type": "PauseLineItems"}
                    else:
                        line_item_action = {"xsi_type": "ResumeLineItems"}

                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("orderId = :orderId AND name = :name")
                        .WithBindVariable("orderId", int(media_buy_id))
                        .WithBindVariable("name", package_id)
                    )

                    result = line_item_service.performLineItemAction(line_item_action, statement.ToStatement())

                    if result and result["numChanges"] > 0:
                        self.log(f"✓ Successfully performed {action} on LineItem '{package_id}'")
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No line items were updated")

                elif (
                    action in ["update_package_budget", "update_package_impressions"]
                    and package_id
                    and budget is not None
                ):
                    line_item_service = self.client.GetService("LineItemService")

                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("orderId = :orderId AND name = :name")
                        .WithBindVariable("orderId", int(media_buy_id))
                        .WithBindVariable("name", package_id)
                    )

                    response = line_item_service.getLineItemsByStatement(statement.ToStatement())
                    line_items = response.get("results", [])

                    if not line_items:
                        return UpdateMediaBuyResponse(
                            status="failed",
                            reason=f"Could not find LineItem with name '{package_id}' in Order '{media_buy_id}'",
                        )

                    line_item_to_update = line_items[0]

                    if action == "update_package_budget":
                        # Calculate new impression goal based on the new budget
                        cpm = line_item_to_update["costPerUnit"]["microAmount"] / 1000000
                        new_impression_goal = int((budget / cpm) * 1000) if cpm > 0 else 0
                    else:  # update_package_impressions
                        # Direct impression update
                        new_impression_goal = budget  # In this case, budget parameter contains impressions

                    line_item_to_update["primaryGoal"]["units"] = new_impression_goal

                    updated_line_items = line_item_service.updateLineItems([line_item_to_update])

                    if not updated_line_items:
                        return UpdateMediaBuyResponse(status="failed", reason="Failed to update LineItem in GAM")

                    self.log(f"✓ Successfully updated budget for LineItem {line_item_to_update['id']}")

                elif action == "activate_order":
                    # Check for guaranteed line items first
                    has_guaranteed, guaranteed_types = self._check_order_has_guaranteed_items(media_buy_id)
                    if has_guaranteed:
                        return UpdateMediaBuyResponse(
                            status="failed",
                            reason=f"Cannot auto-activate order with guaranteed line items ({guaranteed_types}). Use submit_for_approval instead.",
                        )

                    # Activate non-guaranteed order
                    order_service = self.client.GetService("OrderService")
                    line_item_service = self.client.GetService("LineItemService")

                    # Resume the order
                    order_action = {"xsi_type": "ResumeOrders"}
                    order_statement = (
                        ad_manager.StatementBuilder()
                        .Where("id = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    order_result = order_service.performOrderAction(order_action, order_statement.ToStatement())

                    # Activate line items
                    line_item_action = {"xsi_type": "ActivateLineItems"}
                    line_item_statement = (
                        ad_manager.StatementBuilder()
                        .Where("orderId = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    line_item_result = line_item_service.performLineItemAction(
                        line_item_action, line_item_statement.ToStatement()
                    )

                    if (order_result and order_result.get("numChanges", 0) > 0) or (
                        line_item_result and line_item_result.get("numChanges", 0) > 0
                    ):
                        self.log(f"✓ Successfully activated Order {media_buy_id}")
                        self.audit_logger.log_success(f"Activated GAM Order {media_buy_id}")
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No changes made during activation")

                elif action == "submit_for_approval":
                    order_service = self.client.GetService("OrderService")

                    submit_action = {"xsi_type": "SubmitOrdersForApproval"}
                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("id = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    result = order_service.performOrderAction(submit_action, statement.ToStatement())

                    if result and result.get("numChanges", 0) > 0:
                        self.log(f"✓ Successfully submitted Order {media_buy_id} for approval")
                        self.audit_logger.log_success(f"Submitted GAM Order {media_buy_id} for approval")

                        # Create workflow step for tracking approval
                        self._create_approval_workflow_step(media_buy_id)
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No changes made during submission")

                elif action == "approve_order":
                    if not self._is_admin_principal():
                        return UpdateMediaBuyResponse(status="failed", reason="Only admin users can approve orders")

                    # Check order status
                    order_status = self._get_order_status(media_buy_id)
                    if order_status not in ["PENDING_APPROVAL", "DRAFT"]:
                        return UpdateMediaBuyResponse(
                            status="failed",
                            reason=f"Order status is '{order_status}'. Can only approve orders in PENDING_APPROVAL or DRAFT status",
                        )

                    order_service = self.client.GetService("OrderService")

                    approve_action = {"xsi_type": "ApproveOrders"}
                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("id = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    result = order_service.performOrderAction(approve_action, statement.ToStatement())

                    if result and result.get("numChanges", 0) > 0:
                        self.log(f"✓ Successfully approved Order {media_buy_id}")
                        self.audit_logger.log_success(f"Approved GAM Order {media_buy_id}")

                        # Update any existing workflow steps
                        self._update_approval_workflow_step(media_buy_id, "approved")
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No changes made during approval")

                elif action == "archive_order":
                    # Check order status - only archive completed or cancelled orders
                    order_status = self._get_order_status(media_buy_id)
                    if order_status not in ["DELIVERED", "COMPLETED", "CANCELLED", "PAUSED"]:
                        return UpdateMediaBuyResponse(
                            status="failed",
                            reason=f"Order status is '{order_status}'. Can only archive DELIVERED, COMPLETED, CANCELLED, or PAUSED orders",
                        )

                    order_service = self.client.GetService("OrderService")

                    archive_action = {"xsi_type": "ArchiveOrders"}
                    statement = (
                        ad_manager.StatementBuilder()
                        .Where("id = :orderId")
                        .WithBindVariable("orderId", int(media_buy_id))
                    )

                    result = order_service.performOrderAction(archive_action, statement.ToStatement())

                    if result and result.get("numChanges", 0) > 0:
                        self.log(f"✓ Successfully archived Order {media_buy_id}")
                        self.audit_logger.log_success(f"Archived GAM Order {media_buy_id}")
                    else:
                        return UpdateMediaBuyResponse(status="failed", reason="No changes made during archiving")

                return UpdateMediaBuyResponse(
                    status="accepted",
                    implementation_date=today + timedelta(days=1),
                    detail=f"Successfully executed {action} in Google Ad Manager",
                )

            except Exception as e:
                self.log(f"[red]Error updating GAM Order/LineItem: {e}[/red]")
                return UpdateMediaBuyResponse(status="failed", reason=str(e))

    def get_config_ui_endpoint(self) -> str | None:
        """Return the endpoint path for GAM-specific configuration UI."""
        return "/adapters/gam/config"

    def register_ui_routes(self, app: Flask) -> None:
        """Register GAM-specific configuration UI routes."""

        @app.route("/adapters/gam/config/<tenant_id>/<product_id>", methods=["GET", "POST"])
        def gam_product_config(tenant_id, product_id):
            # Get tenant and product
            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import AdapterConfig, Product, Tenant

            with get_db_session() as db_session:
                stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                tenant = db_session.scalars(stmt).first()
                if not tenant:
                    flash("Tenant not found", "error")
                    return redirect(url_for("tenants"))

                stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
                product = db_session.scalars(stmt).first()

            if not product:
                flash("Product not found", "error")
                return redirect(url_for("products", tenant_id=tenant_id))

            product_id_db = product.product_id
            product_name = product.name
            implementation_config = json.loads(product.implementation_config) if product.implementation_config else {}

            # Get network code from adapter config
            with get_db_session() as db_session:
                stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
                adapter_config = db_session.scalars(stmt).first()
                network_code = adapter_config.gam_network_code if adapter_config else "XXXXX"

            if request.method == "POST":
                try:
                    # Build config from form data
                    config = {
                        "order_name_template": request.form.get("order_name_template"),
                        "applied_team_ids": [
                            int(x.strip()) for x in request.form.get("applied_team_ids", "").split(",") if x.strip()
                        ],
                        "line_item_type": request.form.get("line_item_type"),
                        "priority": int(request.form.get("priority", 8)),
                        "cost_type": request.form.get("cost_type"),
                        "creative_rotation_type": request.form.get("creative_rotation_type"),
                        "delivery_rate_type": request.form.get("delivery_rate_type"),
                        "primary_goal_type": request.form.get("primary_goal_type"),
                        "primary_goal_unit_type": request.form.get("primary_goal_unit_type"),
                        "include_descendants": "include_descendants" in request.form,
                        "environment_type": request.form.get("environment_type"),
                        "allow_overbook": "allow_overbook" in request.form,
                        "skip_inventory_check": "skip_inventory_check" in request.form,
                        "disable_viewability_avg_revenue_optimization": "disable_viewability_avg_revenue_optimization"
                        in request.form,
                    }

                    # Process creative placeholders
                    widths = request.form.getlist("placeholder_width[]")
                    heights = request.form.getlist("placeholder_height[]")
                    counts = request.form.getlist("placeholder_count[]")
                    request.form.getlist("placeholder_is_native[]")

                    creative_placeholders = []
                    for i in range(len(widths)):
                        if widths[i] and heights[i]:
                            creative_placeholders.append(
                                {
                                    "width": int(widths[i]),
                                    "height": int(heights[i]),
                                    "expected_creative_count": int(counts[i]) if i < len(counts) else 1,
                                    "is_native": f"placeholder_is_native_{i}" in request.form,
                                }
                            )
                    config["creative_placeholders"] = creative_placeholders

                    # Process frequency caps
                    cap_impressions = request.form.getlist("cap_max_impressions[]")
                    cap_units = request.form.getlist("cap_time_unit[]")
                    cap_ranges = request.form.getlist("cap_time_range[]")

                    frequency_caps = []
                    for i in range(len(cap_impressions)):
                        if cap_impressions[i]:
                            frequency_caps.append(
                                {
                                    "max_impressions": int(cap_impressions[i]),
                                    "time_unit": cap_units[i] if i < len(cap_units) else "DAY",
                                    "time_range": int(cap_ranges[i]) if i < len(cap_ranges) else 1,
                                }
                            )
                    config["frequency_caps"] = frequency_caps

                    # Process targeting
                    config["targeted_ad_unit_ids"] = [
                        x.strip() for x in request.form.get("targeted_ad_unit_ids", "").split("\n") if x.strip()
                    ]
                    config["targeted_placement_ids"] = [
                        x.strip() for x in request.form.get("targeted_placement_ids", "").split("\n") if x.strip()
                    ]
                    config["competitive_exclusion_labels"] = [
                        x.strip() for x in request.form.get("competitive_exclusion_labels", "").split(",") if x.strip()
                    ]

                    # Process discount
                    if request.form.get("discount_type"):
                        config["discount_type"] = request.form.get("discount_type")
                        config["discount_value"] = float(request.form.get("discount_value", 0))

                    # Process video settings
                    if config["environment_type"] == "VIDEO_PLAYER":
                        if request.form.get("companion_delivery_option"):
                            config["companion_delivery_option"] = request.form.get("companion_delivery_option")
                        if request.form.get("video_max_duration"):
                            config["video_max_duration"] = (
                                int(request.form.get("video_max_duration")) * 1000
                            )  # Convert to milliseconds
                        if request.form.get("skip_offset"):
                            config["skip_offset"] = (
                                int(request.form.get("skip_offset")) * 1000
                            )  # Convert to milliseconds

                    # Process custom targeting
                    custom_targeting = request.form.get("custom_targeting_keys", "{}")
                    try:
                        config["custom_targeting_keys"] = json.loads(custom_targeting) if custom_targeting else {}
                    except json.JSONDecodeError:
                        config["custom_targeting_keys"] = {}

                    # Native style ID
                    if request.form.get("native_style_id"):
                        config["native_style_id"] = request.form.get("native_style_id")

                    # Validate the configuration
                    validation_result = self.validate_product_config(config)
                    if validation_result[0]:
                        # Save to database
                        with get_db_session() as db_session:
                            stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
                            product = db_session.scalars(stmt).first()
                            if product:
                                product.implementation_config = json.dumps(config)
                                db_session.commit()
                        flash("GAM configuration saved successfully", "success")
                        return redirect(url_for("edit_product", tenant_id=tenant_id, product_id=product_id))
                    else:
                        flash(f"Validation error: {validation_result[1]}", "error")

                except Exception as e:
                    flash(f"Error saving configuration: {str(e)}", "error")

            # Load existing config or defaults
            config = implementation_config or {}

            return render_template(
                "adapters/gam_product_config.html",
                tenant_id=tenant_id,
                product={"product_id": product_id_db, "name": product_name},
                config=config,
                network_code=network_code,
            )

    def validate_product_config(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate GAM-specific product configuration."""
        try:
            # Use Pydantic model for validation
            gam_config = GAMImplementationConfig(**config)

            # Additional custom validation
            if not gam_config.creative_placeholders:
                return False, "At least one creative placeholder is required"

            # Validate team IDs are positive integers
            for team_id in gam_config.applied_team_ids:
                if team_id <= 0:
                    return False, f"Invalid team ID: {team_id}"

            # Validate frequency caps
            for cap in gam_config.frequency_caps:
                if cap.max_impressions <= 0:
                    return False, "Frequency cap impressions must be positive"
                if cap.time_range <= 0:
                    return False, "Frequency cap time range must be positive"

            return True, None

        except Exception as e:
            return False, str(e)

    async def get_available_inventory(self) -> dict[str, Any]:
        """
        Fetch available inventory from cached database (requires inventory sync to be run first).
        This includes custom targeting keys/values, audience segments, and ad units.
        """
        try:
            # Get inventory from database cache instead of fetching from GAM
            from sqlalchemy import and_, create_engine, func, select
            from sqlalchemy.orm import sessionmaker

            from src.core.database.db_config import DatabaseConfig
            from src.core.database.models import GAMInventory

            # Create database session
            engine = create_engine(DatabaseConfig.get_connection_string())
            Session = sessionmaker(bind=engine)

            with Session() as session:
                # Check if inventory has been synced

                stmt = select(func.count()).select_from(GAMInventory).where(GAMInventory.tenant_id == self.tenant_id)
                inventory_count = session.scalar(stmt)

                if inventory_count == 0:
                    # No inventory synced yet
                    return {
                        "error": "No inventory found. Please sync GAM inventory first.",
                        "audiences": [],
                        "formats": [],
                        "placements": [],
                        "key_values": [],
                        "properties": {"needs_sync": True},
                    }

                # Get custom targeting keys from database
                logger.debug(f"Fetching inventory for tenant_id={self.tenant_id}")
                stmt = select(GAMInventory).where(
                    and_(
                        GAMInventory.tenant_id == self.tenant_id,
                        GAMInventory.inventory_type == "custom_targeting_key",
                    )
                )
                custom_keys = session.scalars(stmt).all()
                logger.debug(f"Found {len(custom_keys)} custom targeting keys")

                # Get custom targeting values from database
                stmt = select(GAMInventory).where(
                    and_(
                        GAMInventory.tenant_id == self.tenant_id,
                        GAMInventory.inventory_type == "custom_targeting_value",
                    )
                )
                custom_values = session.scalars(stmt).all()

                # Group values by key
                values_by_key = {}
                for value in custom_values:
                    key_id = (
                        value.inventory_metadata.get("custom_targeting_key_id") if value.inventory_metadata else None
                    )
                    if key_id:
                        if key_id not in values_by_key:
                            values_by_key[key_id] = []
                        values_by_key[key_id].append(
                            {
                                "id": value.inventory_id,
                                "name": value.name,
                                "display_name": value.path[1] if len(value.path) > 1 else value.name,
                            }
                        )

                # Format key-values for the wizard
                key_values = []
                for key in custom_keys[:20]:  # Limit to first 20 keys for UI
                    # Get display name from path or fallback to name
                    display_name = key.name
                    if key.path and len(key.path) > 0 and key.path[0]:
                        display_name = key.path[0]

                    key_data = {
                        "id": key.inventory_id,
                        "name": key.name,
                        "display_name": display_name,
                        "type": key.inventory_metadata.get("type", "CUSTOM") if key.inventory_metadata else "CUSTOM",
                        "values": values_by_key.get(key.inventory_id, [])[:20],  # Limit to first 20 values
                    }
                    key_values.append(key_data)
                logger.debug(f"Formatted {len(key_values)} key-value pairs for wizard")

                # Get ad units for placements
                stmt = (
                    select(GAMInventory)
                    .where(and_(GAMInventory.tenant_id == self.tenant_id, GAMInventory.inventory_type == "ad_unit"))
                    .limit(20)
                )
                ad_units = session.scalars(stmt).all()

                placements = []
                for unit in ad_units:
                    metadata = unit.inventory_metadata or {}
                    placements.append(
                        {
                            "id": unit.inventory_id,
                            "name": unit.name,
                            "sizes": metadata.get("sizes", []),
                            "platform": metadata.get("target_platform", "WEB"),
                        }
                    )

                # Get audience segments if available
                stmt = (
                    select(GAMInventory)
                    .where(
                        and_(
                            GAMInventory.tenant_id == self.tenant_id, GAMInventory.inventory_type == "audience_segment"
                        )
                    )
                    .limit(20)
                )
                audience_segments = session.scalars(stmt).all()

                audiences = []
                for segment in audience_segments:
                    metadata = segment.inventory_metadata or {}
                    audiences.append(
                        {
                            "id": segment.inventory_id,
                            "name": segment.name,
                            "size": metadata.get("size", 0),
                            "type": metadata.get("type", "unknown"),
                        }
                    )

                # Get last sync time
                stmt = (
                    select(GAMInventory.last_synced)
                    .where(GAMInventory.tenant_id == self.tenant_id)
                    .order_by(GAMInventory.last_synced.desc())
                )
                last_sync = session.execute(stmt).first()

                last_sync_time = last_sync[0].isoformat() if last_sync else None

                # Return formatted inventory data from cache
                return {
                    "audiences": audiences,
                    "formats": [],  # GAM uses standard IAB formats
                    "placements": placements,
                    "key_values": key_values,
                    "properties": {
                        "network_code": self.network_code,
                        "total_custom_keys": len(custom_keys),
                        "total_custom_values": len(custom_values),
                        "last_sync": last_sync_time,
                        "from_cache": True,
                    },
                }

        except Exception as e:
            self.logger.error(f"Error fetching GAM inventory from cache: {e}")
            # Return error indicating sync is needed
            return {
                "error": f"Error accessing inventory cache: {str(e)}. Please run GAM inventory sync.",
                "audiences": [],
                "formats": [],
                "placements": [],
                "key_values": [],
                "properties": {"needs_sync": True},
            }

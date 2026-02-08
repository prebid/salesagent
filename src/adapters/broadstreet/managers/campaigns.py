"""Broadstreet Campaign Manager.

Handles campaign (media buy) operations including creation,
updates, and status management.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.adapters.broadstreet.client import BroadstreetClient
from src.adapters.broadstreet.config_schema import (
    parse_implementation_config,
)

logger = logging.getLogger(__name__)


class BroadstreetCampaignManager:
    """Manages campaign operations for Broadstreet.

    Campaigns in Broadstreet map to Media Buys in AdCP.
    """

    def __init__(
        self,
        client: BroadstreetClient | None,
        advertiser_id: str,
        dry_run: bool = False,
        log_func: Callable[[str], None] | None = None,
    ):
        """Initialize the campaign manager.

        Args:
            client: Broadstreet API client (None for dry-run mode)
            advertiser_id: Broadstreet advertiser ID
            dry_run: Whether to simulate operations
            log_func: Optional logging function
        """
        self.client = client
        self.advertiser_id = advertiser_id
        self.dry_run = dry_run
        self.log = log_func or (lambda msg: logger.info(msg))

    def create_campaign(
        self,
        name: str,
        start_date: datetime,
        end_date: datetime,
        impl_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new campaign.

        Args:
            name: Campaign name
            start_date: Campaign start date
            end_date: Campaign end date
            impl_config: Product implementation config

        Returns:
            Created campaign data with 'id' key
        """
        config = parse_implementation_config(impl_config)

        # Build campaign name using template
        campaign_name = name

        if self.dry_run:
            self.log(f"Would create campaign: {campaign_name}")
            self.log(f"  Advertiser ID: {self.advertiser_id}")
            self.log(f"  Start Date: {start_date.isoformat()}")
            self.log(f"  End Date: {end_date.isoformat()}")
            self.log(f"  Cost Type: {config.cost_type}")
            self.log(f"  Delivery Rate: {config.delivery_rate}")

            # Return mock campaign data
            mock_id = f"dry_run_{int(datetime.now().timestamp())}"
            return {
                "id": mock_id,
                "name": campaign_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

        if not self.client:
            raise RuntimeError("Client not available in non-dry-run mode")

        result = self.client.create_campaign(
            advertiser_id=self.advertiser_id,
            name=campaign_name,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

        campaign_id = result.get("id") or result.get("Id")
        self.log(f"Created Broadstreet campaign: {campaign_id}")

        return {
            "id": str(campaign_id),
            "name": campaign_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }

    def create_placements_for_packages(
        self,
        campaign_id: str,
        packages: list[dict[str, Any]],
        products_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create placements for each package.

        Each package maps to one or more placements based on the
        product's zone targeting configuration.

        Args:
            campaign_id: Broadstreet campaign ID
            packages: List of package data from the request
            products_map: Map of product_id to product data (including implementation_config)

        Returns:
            List of placement results with package_id and placement details
        """
        placement_results = []

        for package in packages:
            package_id = package.get("package_id", "unknown")
            product_id = package.get("product_id")

            # Get product config
            product = products_map.get(product_id or "", {})
            impl_config = parse_implementation_config(product.get("implementation_config"))

            # Get zones to target
            zone_ids = impl_config.get_zone_ids()

            if not zone_ids:
                self.log(f"[yellow]Warning: Package {package_id} has no zones configured[/yellow]")
                placement_results.append(
                    {
                        "package_id": package_id,
                        "product_id": product_id,
                        "status": "no_zones",
                        "placements": [],
                    }
                )
                continue

            if self.dry_run:
                self.log(f"Would create placements for package: {package_id}")
                self.log(f"  Product: {product_id}")
                self.log(f"  Zones: {zone_ids}")
                self.log(f"  Budget: {package.get('budget', 'N/A')}")
                self.log(f"  Impressions: {package.get('impressions', 'N/A')}")

                # Mock placement data
                mock_placements = [
                    {
                        "id": f"placement_{zone_id}_{package_id}",
                        "zone_id": zone_id,
                        "campaign_id": campaign_id,
                    }
                    for zone_id in zone_ids
                ]

                placement_results.append(
                    {
                        "package_id": package_id,
                        "product_id": product_id,
                        "status": "created",
                        "placements": mock_placements,
                    }
                )
            else:
                # Note: Broadstreet placements require an advertisement_id
                # In the real flow, placements are created when creatives are added
                # For now, we just track the zone configuration
                self.log(f"Package {package_id} configured for zones: {zone_ids}")
                self.log("  (Placements will be created when creatives are added)")

                placement_results.append(
                    {
                        "package_id": package_id,
                        "product_id": product_id,
                        "status": "pending_creatives",
                        "zone_ids": zone_ids,
                    }
                )

        return placement_results

    def build_campaign_name(
        self,
        template: str,
        po_number: str | None,
        product_name: str | None,
        advertiser_name: str | None,
    ) -> str:
        """Build campaign name from template.

        Args:
            template: Name template with placeholders
            po_number: PO number from request
            product_name: Product name
            advertiser_name: Advertiser name

        Returns:
            Formatted campaign name
        """
        name = template
        name = name.replace("{po_number}", po_number or "unknown")
        name = name.replace("{product_name}", product_name or "product")
        name = name.replace("{advertiser_name}", advertiser_name or "advertiser")
        name = name.replace("{timestamp}", datetime.now().strftime("%Y%m%d_%H%M%S"))
        return name

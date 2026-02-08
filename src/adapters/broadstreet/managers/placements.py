"""Broadstreet Placement Manager.

Handles placement operations for linking advertisements to zones
within campaigns. In Broadstreet:
- Placements link ads to zones within a campaign
- Each package maps to one or more placements (one per zone)
"""

import logging
from collections.abc import Callable
from typing import Any

from src.adapters.broadstreet.client import BroadstreetClient
from src.adapters.broadstreet.config_schema import parse_implementation_config

logger = logging.getLogger(__name__)


class PlacementInfo:
    """Tracks placement state for a package during creation."""

    def __init__(
        self,
        package_id: str,
        product_id: str | None,
        zone_ids: list[str],
        advertisement_ids: list[str] | None = None,
    ):
        self.package_id = package_id
        self.product_id = product_id
        self.zone_ids = zone_ids
        self.advertisement_ids = advertisement_ids or []
        self.placement_ids: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "package_id": self.package_id,
            "product_id": self.product_id,
            "zone_ids": self.zone_ids,
            "advertisement_ids": self.advertisement_ids,
            "placement_ids": self.placement_ids,
        }


class BroadstreetPlacementManager:
    """Manages placement operations for Broadstreet.

    Placements in Broadstreet link advertisements to zones within campaigns.
    Each AdCP package maps to placements for its configured zones.

    Note: This manager handles same-request placement creation only.
    Cross-request operations (pause/resume) are handled directly in the
    adapter via database queries, following the GAM adapter pattern.
    """

    def __init__(
        self,
        client: BroadstreetClient | None,
        advertiser_id: str,
        dry_run: bool = False,
        log_func: Callable[[str], None] | None = None,
    ):
        """Initialize the placement manager.

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

        # Track placement state per media buy (same-request only)
        # Structure: {media_buy_id: {package_id: PlacementInfo}}
        self._placement_cache: dict[str, dict[str, PlacementInfo]] = {}

    def register_package(
        self,
        media_buy_id: str,
        package_id: str,
        product_id: str | None,
        impl_config: dict[str, Any] | None,
    ) -> PlacementInfo:
        """Register a package for placement tracking.

        Called during create_media_buy to set up package→zone mappings.

        Args:
            media_buy_id: Media buy (campaign) ID
            package_id: Package ID
            product_id: Product ID
            impl_config: Product implementation config

        Returns:
            PlacementInfo for the package
        """
        config = parse_implementation_config(impl_config)
        zone_ids = config.get_zone_ids()

        info = PlacementInfo(
            package_id=package_id,
            product_id=product_id,
            zone_ids=zone_ids,
        )

        # Initialize cache for this media buy if needed
        if media_buy_id not in self._placement_cache:
            self._placement_cache[media_buy_id] = {}

        self._placement_cache[media_buy_id][package_id] = info

        self.log(f"Registered package {package_id} with zones: {zone_ids}")
        return info

    def get_package_info(self, media_buy_id: str, package_id: str) -> PlacementInfo | None:
        """Get placement info for a package.

        Args:
            media_buy_id: Media buy ID
            package_id: Package ID

        Returns:
            PlacementInfo if found, None otherwise
        """
        return self._placement_cache.get(media_buy_id, {}).get(package_id)

    def get_all_packages(self, media_buy_id: str) -> list[PlacementInfo]:
        """Get all packages for a media buy.

        Args:
            media_buy_id: Media buy ID

        Returns:
            List of PlacementInfo objects
        """
        return list(self._placement_cache.get(media_buy_id, {}).values())

    def create_placements(
        self,
        campaign_id: str,
        media_buy_id: str,
        package_id: str,
        advertisement_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Create placements linking ads to zones for a package.

        Args:
            campaign_id: Broadstreet campaign ID
            media_buy_id: Media buy ID (for cache lookup)
            package_id: Package ID
            advertisement_ids: List of Broadstreet advertisement IDs

        Returns:
            List of created placement data
        """
        info = self.get_package_info(media_buy_id, package_id)
        if not info:
            self.log(f"[yellow]Warning: No registration found for package {package_id}[/yellow]")
            return []

        if not info.zone_ids:
            self.log(f"[yellow]Warning: Package {package_id} has no zones[/yellow]")
            return []

        results = []

        for zone_id in info.zone_ids:
            for ad_id in advertisement_ids:
                if self.dry_run:
                    self.log(f"Would create placement: ad {ad_id} → zone {zone_id}")
                    placement_id = f"placement_{zone_id}_{ad_id}"
                    results.append(
                        {
                            "id": placement_id,
                            "zone_id": zone_id,
                            "advertisement_id": ad_id,
                            "campaign_id": campaign_id,
                        }
                    )
                    info.placement_ids.append(placement_id)
                else:
                    if self.client:
                        try:
                            placement_data = self.client.create_placement(
                                advertiser_id=self.advertiser_id,
                                campaign_id=campaign_id,
                                zone_id=zone_id,
                                advertisement_id=ad_id,
                            )
                            placement_id = str(placement_data.get("id", f"placement_{zone_id}_{ad_id}"))
                            results.append(
                                {
                                    "id": placement_id,
                                    "zone_id": zone_id,
                                    "advertisement_id": ad_id,
                                    "campaign_id": campaign_id,
                                }
                            )
                            info.placement_ids.append(placement_id)
                            self.log(f"Created placement {placement_id}: ad {ad_id} → zone {zone_id}")
                        except Exception as e:
                            logger.error(f"Error creating placement for zone {zone_id}: {e}", exc_info=True)
                            self.log(f"Error creating placement: {e}")

        # Update advertisement IDs
        info.advertisement_ids.extend(advertisement_ids)

        return results

"""Broadstreet Inventory Manager.

Handles zone discovery and inventory sync for Broadstreet.
Zones are Broadstreet's primary ad placement concept.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.adapters.base_inventory import BaseInventoryManager, InventoryItem
from src.adapters.broadstreet.client import BroadstreetClient

logger = logging.getLogger(__name__)


class ZoneInfo(InventoryItem):
    """Represents a Broadstreet zone."""

    def __init__(
        self,
        zone_id: str,
        name: str,
        width: int | None = None,
        height: int | None = None,
        display_type: str = "standard",
        ad_count: int = 1,
    ):
        super().__init__(item_id=zone_id, name=name)
        self.zone_id = zone_id
        self.width = width
        self.height = height
        self.display_type = display_type
        self.ad_count = ad_count

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "display_type": self.display_type,
            "ad_count": self.ad_count,
        }


class BroadstreetInventoryManager(BaseInventoryManager):
    """Manages inventory discovery and zone sync for Broadstreet.

    Broadstreet's inventory is organized around zones - ad placements
    that can be embedded in publisher pages.
    """

    def __init__(
        self,
        client: BroadstreetClient | None,
        network_id: str,
        dry_run: bool = False,
        log_func: Callable[[str], None] | None = None,
    ):
        """Initialize the inventory manager.

        Args:
            client: Broadstreet API client (None for dry-run mode)
            network_id: Broadstreet network ID
            dry_run: Whether to simulate operations
            log_func: Optional logging function
        """
        super().__init__(
            client=client,
            identifier=network_id,
            dry_run=dry_run,
            log_func=log_func,
        )
        self.network_id = network_id

        # Cache zones
        self._zone_cache: dict[str, ZoneInfo] = {}

    def fetch_zones(self, refresh: bool = False) -> list[ZoneInfo]:
        """Fetch all available zones from Broadstreet.

        Args:
            refresh: Force refresh of cached data

        Returns:
            List of ZoneInfo objects
        """
        if self._zone_cache and not refresh:
            return list(self._zone_cache.values())

        self.log("Fetching zones from Broadstreet")

        if self.dry_run:
            # Return simulated zones
            simulated_zones = [
                ZoneInfo(
                    zone_id="zone_1",
                    name="Top Banner",
                    width=728,
                    height=90,
                    display_type="standard",
                ),
                ZoneInfo(
                    zone_id="zone_2",
                    name="Sidebar",
                    width=300,
                    height=250,
                    display_type="standard",
                ),
                ZoneInfo(
                    zone_id="zone_3",
                    name="Footer",
                    width=970,
                    height=250,
                    display_type="rotation",
                    ad_count=3,
                ),
            ]
            for zone in simulated_zones:
                self._zone_cache[zone.zone_id] = zone
            self._last_sync = datetime.now()
            self.log(f"  Simulated {len(simulated_zones)} zones")
            return simulated_zones

        if self.client:
            try:
                zones_data = self.client.get_zones()
                zones = []
                for zone_data in zones_data:
                    zone = ZoneInfo(
                        zone_id=str(zone_data.get("id", zone_data.get("Id", ""))),
                        name=zone_data.get("name", zone_data.get("Name", "")),
                        width=zone_data.get("width", zone_data.get("Width")),
                        height=zone_data.get("height", zone_data.get("Height")),
                        display_type=zone_data.get("display_type", "standard"),
                        ad_count=zone_data.get("ad_count", 1),
                    )
                    zones.append(zone)
                    self._zone_cache[zone.zone_id] = zone
                self._last_sync = datetime.now()
                self.log(f"  Fetched {len(zones)} zones from Broadstreet")
                return zones
            except Exception as e:
                logger.error(f"Error fetching zones from Broadstreet: {e}", exc_info=True)
                self.log(f"Error fetching zones: {e}")
                return []

        return []

    def get_zone(self, zone_id: str) -> ZoneInfo | None:
        """Get zone info by ID.

        Args:
            zone_id: Zone ID

        Returns:
            ZoneInfo if found, None otherwise
        """
        # Fetch zones if cache is empty
        if not self._zone_cache:
            self.fetch_zones()

        return self._zone_cache.get(zone_id)

    def validate_zone_ids(self, zone_ids: list[str]) -> tuple[list[str], list[str]]:
        """Validate that zone IDs exist.

        Args:
            zone_ids: List of zone IDs to validate

        Returns:
            Tuple of (valid_ids, invalid_ids)
        """
        # Fetch zones if cache is empty
        if not self._zone_cache:
            self.fetch_zones()

        valid = []
        invalid = []

        for zone_id in zone_ids:
            if zone_id in self._zone_cache:
                valid.append(zone_id)
            else:
                invalid.append(zone_id)

        return valid, invalid

    def get_zones_by_size(self, width: int, height: int) -> list[ZoneInfo]:
        """Get zones matching a specific size.

        Args:
            width: Zone width
            height: Zone height

        Returns:
            List of matching ZoneInfo objects
        """
        # Fetch zones if cache is empty
        if not self._zone_cache:
            self.fetch_zones()

        return [zone for zone in self._zone_cache.values() if zone.width == width and zone.height == height]

    def build_inventory_response(self) -> dict[str, Any]:
        """Build inventory response for get_available_inventory.

        Returns:
            Dictionary with zones and inventory details
        """
        zones = self.fetch_zones()

        # Build zone list
        zone_list = [zone.to_dict() for zone in zones]

        # Collect unique sizes
        sizes = set()
        for zone in zones:
            if zone.width and zone.height:
                sizes.add((zone.width, zone.height))

        # Build creative specs
        creative_specs = [
            {"format": "display", "sizes": [list(s) for s in sizes]},
            {"format": "html", "sizes": []},  # HTML ads don't have fixed sizes
            {"format": "text", "sizes": []},
        ]

        return {
            "zones": zone_list,
            "ad_units": [],  # Broadstreet uses zones instead of ad units
            "targeting_options": {
                "geographic": ["countries"],  # Limited geo targeting
            },
            "creative_specs": creative_specs,
            "properties": {
                "network_id": self.network_id,
                "supports_webhooks": False,  # Broadstreet is synchronous
                "reporting_delay_minutes": 0,  # Real-time reporting
            },
        }

    def sync_zones_to_products(self) -> list[dict[str, Any]]:
        """Generate product suggestions based on available zones.

        This can be used to help configure products with appropriate zones.

        Returns:
            List of suggested product configurations
        """
        zones = self.fetch_zones()

        # Group zones by size
        sizes_to_zones: dict[tuple[int, int], list[ZoneInfo]] = {}
        for zone in zones:
            if zone.width and zone.height:
                key = (zone.width, zone.height)
                if key not in sizes_to_zones:
                    sizes_to_zones[key] = []
                sizes_to_zones[key].append(zone)

        suggestions = []

        # Create suggestion for each size
        for (width, height), size_zones in sizes_to_zones.items():
            zone_ids = [z.zone_id for z in size_zones]
            suggestions.append(
                {
                    "name": f"Broadstreet {width}x{height} Display",
                    "description": f"Display advertising across {len(zone_ids)} zones",
                    "implementation_config": {
                        "targeted_zone_ids": zone_ids,
                        "creative_sizes": [{"width": width, "height": height}],
                        "ad_format": "display",
                        "cost_type": "CPM",
                        "automation_mode": "automatic",
                    },
                    "reporting_capabilities": {
                        "supports_webhooks": False,
                        "expected_delay_minutes": 0,
                        "polling_supported": True,
                    },
                }
            )

        return suggestions

    def clear_cache(self) -> None:
        """Clear the zone cache."""
        self._zone_cache.clear()
        super().clear_cache()

    # BaseInventoryManager abstract method implementations

    def discover_inventory(self, refresh: bool = False) -> list[ZoneInfo]:
        """Discover available inventory from Broadstreet.

        Args:
            refresh: Force refresh of cached data

        Returns:
            List of ZoneInfo objects
        """
        return self.fetch_zones(refresh=refresh)

    def validate_inventory_ids(self, inventory_ids: list[str]) -> tuple[list[str], list[str]]:
        """Validate that inventory IDs (zone IDs) exist.

        Args:
            inventory_ids: List of zone IDs to validate

        Returns:
            Tuple of (valid_ids, invalid_ids)
        """
        return self.validate_zone_ids(inventory_ids)

    def suggest_products(self) -> list[dict[str, Any]]:
        """Generate product configuration suggestions based on available zones.

        Returns:
            List of suggested product configurations
        """
        return self.sync_zones_to_products()

"""
Google Ad Manager Inventory Manager

This manager handles:
- Ad unit discovery and hierarchy management
- Placement operations and management
- Custom targeting key/value discovery
- Audience segment management
- Inventory caching mechanisms
- Integration with GAM client for inventory operations

Extracted from gam_inventory_service.py and gam_inventory_discovery.py to provide
a clean, focused interface for inventory operations within the modular GAM architecture.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.adapters.gam.client import GAMClientManager
from src.adapters.gam_inventory_discovery import (
    AdUnit,
    AudienceSegment,
    GAMInventoryDiscovery,
    Label,
    Placement,
)

logger = logging.getLogger(__name__)


class GAMInventoryManager:
    """Manages GAM inventory operations with caching and database integration."""

    def __init__(self, client_manager: GAMClientManager, tenant_id: str, dry_run: bool = False):
        """Initialize inventory manager.

        Args:
            client_manager: GAM client manager instance
            tenant_id: Tenant identifier
            dry_run: Whether to run in dry-run mode
        """
        self.client_manager = client_manager
        self.tenant_id = tenant_id
        self.dry_run = dry_run
        self._discovery: GAMInventoryDiscovery | MockGAMInventoryDiscovery | None = None
        self._cache_timeout = timedelta(hours=24)

        logger.info(f"Initialized GAMInventoryManager for tenant {tenant_id} (dry_run: {dry_run})")

    def _get_discovery(self) -> GAMInventoryDiscovery | MockGAMInventoryDiscovery:
        """Get or create GAM inventory discovery instance."""
        if not self._discovery:
            if self.dry_run:
                # In dry-run mode, create a mock discovery instance
                self._discovery = MockGAMInventoryDiscovery(None, self.tenant_id)
            else:
                client = self.client_manager.get_client()
                self._discovery = GAMInventoryDiscovery(client, self.tenant_id)
        return self._discovery

    def discover_ad_units(self, parent_id: str | None = None, max_depth: int = 10) -> list[AdUnit]:
        """Discover ad units in the GAM network.

        Args:
            parent_id: Parent ad unit ID to start from (None for root)
            max_depth: Maximum depth to traverse

        Returns:
            List of discovered ad units
        """
        logger.info(f"Discovering ad units for tenant {self.tenant_id} (parent: {parent_id}, depth: {max_depth})")

        if self.dry_run:
            logger.info("[DRY RUN] Would discover ad units from GAM API")
            return []

        discovery = self._get_discovery()
        return discovery.discover_ad_units(parent_id, max_depth)

    def discover_placements(self) -> list[Placement]:
        """Discover all placements in the GAM network.

        Returns:
            List of discovered placements
        """
        logger.info(f"Discovering placements for tenant {self.tenant_id}")

        if self.dry_run:
            logger.info("[DRY RUN] Would discover placements from GAM API")
            return []

        discovery = self._get_discovery()
        return discovery.discover_placements()

    def discover_custom_targeting(self) -> dict[str, Any]:
        """Discover all custom targeting keys and their values.

        Returns:
            Dictionary with discovered keys and values
        """
        logger.info(f"Discovering custom targeting for tenant {self.tenant_id}")

        if self.dry_run:
            logger.info("[DRY RUN] Would discover custom targeting from GAM API")
            return {"keys": [], "total_values": 0}

        discovery = self._get_discovery()
        return discovery.discover_custom_targeting()

    def discover_audience_segments(self) -> list[AudienceSegment]:
        """Discover audience segments (first-party and third-party).

        Returns:
            List of discovered audience segments
        """
        logger.info(f"Discovering audience segments for tenant {self.tenant_id}")

        if self.dry_run:
            logger.info("[DRY RUN] Would discover audience segments from GAM API")
            return []

        discovery = self._get_discovery()
        return discovery.discover_audience_segments()

    def discover_labels(self) -> list[Label]:
        """Discover all labels (for competitive exclusion, etc.).

        Returns:
            List of discovered labels
        """
        logger.info(f"Discovering labels for tenant {self.tenant_id}")

        if self.dry_run:
            logger.info("[DRY RUN] Would discover labels from GAM API")
            return []

        discovery = self._get_discovery()
        return discovery.discover_labels()

    def sync_all_inventory(self, custom_targeting_limit: int = 1000, fetch_values: bool = False) -> dict[str, Any]:
        """Perform full inventory sync from GAM.

        Args:
            custom_targeting_limit: Maximum number of values per custom targeting key (only used if fetch_values=True)
            fetch_values: Whether to fetch custom targeting values during sync (default False for lazy loading)

        Returns:
            Summary of synced data
        """
        logger.info(f"Starting full inventory sync for tenant {self.tenant_id}")
        if not fetch_values:
            logger.info("Custom targeting: Keys only (values will be lazy loaded on demand)")
        else:
            logger.info(f"Custom targeting: Keys + values (limit: {custom_targeting_limit} values per key)")

        if self.dry_run:
            logger.info("[DRY RUN] Would perform full inventory sync from GAM")
            return {
                "tenant_id": self.tenant_id,
                "sync_time": datetime.now(UTC).isoformat(),
                "dry_run": True,
                "ad_units": {"total": 0},
                "placements": {"total": 0},
                "labels": {"total": 0},
                "custom_targeting": {"total_keys": 0, "total_values": 0},
                "audience_segments": {"total": 0},
            }

        discovery = self._get_discovery()
        return discovery.sync_all(
            fetch_custom_targeting_values=fetch_values, max_custom_targeting_values_per_key=custom_targeting_limit
        )

    def build_ad_unit_tree(self) -> dict[str, Any]:
        """Build hierarchical tree structure of ad units.

        Returns:
            Hierarchical tree structure
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would build ad unit tree from cached data")
            return {
                "root_units": [],
                "total_units": 0,
                "last_sync": datetime.now(UTC).isoformat(),
                "dry_run": True,
            }

        discovery = self._get_discovery()
        return discovery.build_ad_unit_tree()

    def get_targetable_ad_units(
        self, include_inactive: bool = False, min_sizes: list[dict[str, int]] | None = None
    ) -> list[AdUnit]:
        """Get ad units suitable for targeting.

        Args:
            include_inactive: Include inactive units
            min_sizes: Minimum sizes required

        Returns:
            List of targetable ad units
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would retrieve targetable ad units from cache")
            return []

        discovery = self._get_discovery()
        return discovery.get_targetable_ad_units(include_inactive, min_sizes)

    def suggest_ad_units_for_product(
        self, creative_sizes: list[dict[str, int]], keywords: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Suggest ad units based on product requirements.

        Args:
            creative_sizes: List of creative sizes the product supports
            keywords: Optional keywords to match in ad unit names/paths

        Returns:
            List of suggested ad units with relevance scores
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would suggest ad units based on product requirements")
            return []

        discovery = self._get_discovery()
        return discovery.suggest_ad_units_for_product(creative_sizes, keywords)

    def get_placements_for_ad_units(self, ad_unit_ids: list[str]) -> list[Placement]:
        """Get placements that target specific ad units.

        Args:
            ad_unit_ids: List of ad unit IDs to find placements for

        Returns:
            List of matching placements
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would find placements for ad units: {ad_unit_ids}")
            return []

        discovery = self._get_discovery()
        return discovery.get_placements_for_ad_units(ad_unit_ids)

    def save_to_cache(self, cache_dir: str) -> None:
        """Save discovered inventory to cache files.

        Args:
            cache_dir: Directory to save cache files
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would save inventory cache to {cache_dir}")
            return

        discovery = self._get_discovery()
        discovery.save_to_cache(cache_dir)

    def load_from_cache(self, cache_dir: str) -> bool:
        """Load inventory from cache if available and fresh.

        Args:
            cache_dir: Directory to load cache files from

        Returns:
            True if loaded successfully, False otherwise
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would load inventory cache from {cache_dir}")
            return False

        discovery = self._get_discovery()
        return discovery.load_from_cache(cache_dir)

    def get_inventory_summary(self) -> dict[str, Any]:
        """Get summary of current inventory state.

        Returns:
            Summary of inventory counts and last sync info
        """
        if self.dry_run:
            return {
                "tenant_id": self.tenant_id,
                "dry_run": True,
                "ad_units": 0,
                "placements": 0,
                "labels": 0,
                "custom_targeting_keys": 0,
                "audience_segments": 0,
                "last_sync": None,
            }

        discovery = self._get_discovery()
        return {
            "tenant_id": self.tenant_id,
            "ad_units": len(discovery.ad_units),
            "placements": len(discovery.placements),
            "labels": len(discovery.labels),
            "custom_targeting_keys": len(discovery.custom_targeting_keys),
            "audience_segments": len(discovery.audience_segments),
            "last_sync": discovery.last_sync.isoformat() if discovery.last_sync else None,
        }

    def validate_inventory_access(self, ad_unit_ids: list[str]) -> dict[str, bool]:
        """Validate that specified ad units are accessible and targetable.

        Args:
            ad_unit_ids: List of ad unit IDs to validate

        Returns:
            Dictionary mapping ad unit IDs to accessibility status
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would validate inventory access for: {ad_unit_ids}")
            return dict.fromkeys(ad_unit_ids, True)

        discovery = self._get_discovery()
        results = {}

        for unit_id in ad_unit_ids:
            unit = discovery.ad_units.get(unit_id)
            if unit:
                # Ad unit exists and is in our inventory
                is_targetable = unit.explicitly_targeted or unit.status.value == "ACTIVE"
                results[unit_id] = is_targetable
            else:
                # Ad unit not found in our inventory
                results[unit_id] = False

        return results


class MockGAMInventoryDiscovery:
    """Mock inventory discovery for dry-run mode."""

    def __init__(self, client, tenant_id: str):
        self.client = client
        self.tenant_id = tenant_id
        self.ad_units: dict[str, Any] = {}
        self.placements: dict[str, Any] = {}
        self.labels: dict[str, Any] = {}
        self.custom_targeting_keys: dict[str, Any] = {}
        self.custom_targeting_values: dict[str, Any] = {}
        self.audience_segments: dict[str, Any] = {}
        self.last_sync = None

    def discover_ad_units(self, parent_id=None, max_depth=10):
        logger.info(f"[MOCK] Discovering ad units (parent: {parent_id}, depth: {max_depth})")
        return []

    def discover_placements(self):
        logger.info("[MOCK] Discovering placements")
        return []

    def discover_custom_targeting(self):
        logger.info("[MOCK] Discovering custom targeting")
        return {"keys": [], "total_values": 0}

    def discover_audience_segments(self):
        logger.info("[MOCK] Discovering audience segments")
        return []

    def discover_labels(self):
        logger.info("[MOCK] Discovering labels")
        return []

    def sync_all(
        self, fetch_custom_targeting_values: bool = False, max_custom_targeting_values_per_key: int = 1000
    ) -> dict[str, Any]:
        logger.info("[MOCK] Performing full sync")
        return {
            "tenant_id": self.tenant_id,
            "sync_time": datetime.now(UTC).isoformat(),
            "dry_run": True,
            "ad_units": {"total": 0},
            "placements": {"total": 0},
            "labels": {"total": 0},
            "custom_targeting": {"total_keys": 0, "total_values": 0},
            "audience_segments": {"total": 0},
        }

    def sync_selective(
        self,
        sync_types: list[str],
        custom_targeting_limit: int = 1000,
        audience_segment_limit: int | None = None,
    ) -> dict[str, Any]:
        logger.info(f"[MOCK] Performing selective sync: {sync_types}")
        return {
            "tenant_id": self.tenant_id,
            "sync_time": datetime.now(UTC).isoformat(),
            "dry_run": True,
            "sync_types": sync_types,
            "duration_seconds": 0,
        }

    def build_ad_unit_tree(self):
        return {
            "root_units": [],
            "total_units": 0,
            "last_sync": datetime.now(UTC).isoformat(),
            "dry_run": True,
        }

    def get_targetable_ad_units(self, include_inactive=False, min_sizes=None):
        return []

    def suggest_ad_units_for_product(self, creative_sizes, keywords=None):
        return []

    def get_placements_for_ad_units(self, ad_unit_ids):
        return []

    def save_to_cache(self, cache_dir):
        logger.info(f"[MOCK] Saving cache to {cache_dir}")

    def load_from_cache(self, cache_dir):
        logger.info(f"[MOCK] Loading cache from {cache_dir}")
        return False

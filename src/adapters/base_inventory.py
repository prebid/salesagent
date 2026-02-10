"""
Base Inventory Manager Interface.

Defines the common interface for inventory discovery and sync operations
across different ad server adapters.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class BaseInventoryManager(ABC):
    """Base class for inventory management across ad server adapters.

    Provides common patterns for:
    - Inventory discovery (ad units, zones, placements)
    - Caching mechanisms
    - Product configuration suggestions
    - Inventory validation

    Subclasses should implement platform-specific discovery logic.
    """

    def __init__(
        self,
        client: Any | None,
        identifier: str,
        dry_run: bool = False,
        log_func: Callable[[str], None] | None = None,
        cache_timeout: timedelta | None = None,
    ):
        """Initialize the inventory manager.

        Args:
            client: Platform-specific API client (None for dry-run mode)
            identifier: Network/tenant identifier (e.g., network_id, tenant_id)
            dry_run: Whether to simulate operations
            log_func: Optional logging function
            cache_timeout: How long to cache inventory data
        """
        self.client = client
        self.identifier = identifier
        self.dry_run = dry_run
        self.log = log_func or (lambda msg: logger.info(msg))
        self._cache_timeout = cache_timeout or timedelta(hours=24)
        self._last_sync: datetime | None = None

    @abstractmethod
    def discover_inventory(self, refresh: bool = False) -> list[Any]:
        """Discover available inventory from the ad server.

        This is the primary discovery method. Platform-specific implementations
        should fetch their core inventory type (ad units, zones, etc.).

        Args:
            refresh: Force refresh of cached data

        Returns:
            List of inventory items (platform-specific type)
        """
        pass

    @abstractmethod
    def validate_inventory_ids(self, inventory_ids: list[str]) -> tuple[list[str], list[str]]:
        """Validate that inventory IDs exist.

        Args:
            inventory_ids: List of inventory IDs to validate

        Returns:
            Tuple of (valid_ids, invalid_ids)
        """
        pass

    @abstractmethod
    def build_inventory_response(self) -> dict[str, Any]:
        """Build inventory response for get_available_inventory.

        Returns:
            Dictionary with inventory details in platform-agnostic format
        """
        pass

    @abstractmethod
    def suggest_products(self) -> list[dict[str, Any]]:
        """Generate product configuration suggestions based on available inventory.

        This can be used to help configure products with appropriate
        inventory targeting.

        Returns:
            List of suggested product configurations
        """
        pass

    def clear_cache(self) -> None:
        """Clear the inventory cache."""
        self._last_sync = None
        self.log("Cleared inventory cache")

    def is_cache_valid(self) -> bool:
        """Check if the cache is still valid.

        Returns:
            True if cache is valid, False if expired or empty
        """
        if not self._last_sync:
            return False
        return datetime.now() - self._last_sync < self._cache_timeout

    def get_inventory_summary(self) -> dict[str, Any]:
        """Get summary of current inventory state.

        Returns:
            Summary of inventory counts and last sync info
        """
        return {
            "identifier": self.identifier,
            "dry_run": self.dry_run,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "cache_valid": self.is_cache_valid(),
        }


class InventoryItem:
    """Base class for inventory items.

    Provides common interface for serialization and comparison.
    """

    def __init__(self, item_id: str, name: str):
        self.item_id = item_id
        self.name = name

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.item_id,
            "name": self.name,
        }

    def __eq__(self, other):
        if not isinstance(other, InventoryItem):
            return False
        return self.item_id == other.item_id

    def __hash__(self):
        return hash(self.item_id)

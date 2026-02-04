"""Tests for BaseInventoryManager interface."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from src.adapters.base_inventory import BaseInventoryManager, InventoryItem


class ConcreteInventoryManager(BaseInventoryManager):
    """Concrete implementation for testing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: dict[str, InventoryItem] = {}

    def discover_inventory(self, refresh: bool = False) -> list[InventoryItem]:
        if refresh:
            self._items.clear()
        if not self._items:
            # Simulated discovery
            self._items = {
                "item_1": InventoryItem("item_1", "Item One"),
                "item_2": InventoryItem("item_2", "Item Two"),
            }
            self._last_sync = datetime.now()
        return list(self._items.values())

    def validate_inventory_ids(self, inventory_ids: list[str]) -> tuple[list[str], list[str]]:
        if not self._items:
            self.discover_inventory()
        valid = [id for id in inventory_ids if id in self._items]
        invalid = [id for id in inventory_ids if id not in self._items]
        return valid, invalid

    def build_inventory_response(self) -> dict[str, Any]:
        items = self.discover_inventory()
        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def suggest_products(self) -> list[dict[str, Any]]:
        items = self.discover_inventory()
        return [{"name": f"Product for {item.name}", "target_id": item.item_id} for item in items]


class TestBaseInventoryManager:
    """Tests for BaseInventoryManager base class."""

    @pytest.fixture
    def manager(self):
        """Create a concrete inventory manager instance."""
        return ConcreteInventoryManager(
            client=None,
            identifier="test_network",
            dry_run=True,
        )

    def test_init(self, manager):
        """Test initialization."""
        assert manager.identifier == "test_network"
        assert manager.dry_run is True
        assert manager.client is None
        assert manager._last_sync is None

    def test_discover_inventory(self, manager):
        """Test inventory discovery."""
        items = manager.discover_inventory()
        assert len(items) == 2
        assert all(isinstance(item, InventoryItem) for item in items)

    def test_discover_inventory_caches(self, manager):
        """Test that discovery caches results."""
        items1 = manager.discover_inventory()
        items2 = manager.discover_inventory()
        assert items1 == items2

    def test_discover_inventory_refresh(self, manager):
        """Test that refresh clears cache."""
        manager.discover_inventory()
        assert len(manager._items) == 2

        # Add a new item directly to test refresh
        manager._items["item_3"] = InventoryItem("item_3", "Item Three")
        assert len(manager._items) == 3

        # Refresh should reset to original items
        items = manager.discover_inventory(refresh=True)
        assert len(items) == 2

    def test_validate_inventory_ids(self, manager):
        """Test inventory ID validation."""
        valid, invalid = manager.validate_inventory_ids(["item_1", "item_2", "item_unknown"])

        assert "item_1" in valid
        assert "item_2" in valid
        assert "item_unknown" in invalid

    def test_build_inventory_response(self, manager):
        """Test building inventory response."""
        response = manager.build_inventory_response()

        assert "items" in response
        assert "count" in response
        assert response["count"] == 2

    def test_suggest_products(self, manager):
        """Test product suggestions."""
        suggestions = manager.suggest_products()

        assert len(suggestions) == 2
        assert all("name" in s and "target_id" in s for s in suggestions)

    def test_clear_cache(self, manager):
        """Test clearing cache."""
        manager.discover_inventory()
        assert manager._last_sync is not None

        manager.clear_cache()
        assert manager._last_sync is None

    def test_is_cache_valid_when_empty(self, manager):
        """Test cache validity when empty."""
        assert manager.is_cache_valid() is False

    def test_is_cache_valid_when_fresh(self, manager):
        """Test cache validity when fresh."""
        manager.discover_inventory()
        assert manager.is_cache_valid() is True

    def test_is_cache_valid_when_expired(self, manager):
        """Test cache validity when expired."""
        manager.discover_inventory()
        # Simulate cache expiration
        manager._last_sync = datetime.now() - timedelta(hours=25)
        assert manager.is_cache_valid() is False

    def test_get_inventory_summary(self, manager):
        """Test inventory summary."""
        summary = manager.get_inventory_summary()

        assert summary["identifier"] == "test_network"
        assert summary["dry_run"] is True
        assert summary["last_sync"] is None
        assert summary["cache_valid"] is False

    def test_get_inventory_summary_after_discovery(self, manager):
        """Test inventory summary after discovery."""
        manager.discover_inventory()
        summary = manager.get_inventory_summary()

        assert summary["last_sync"] is not None
        assert summary["cache_valid"] is True

    def test_custom_log_func(self):
        """Test custom logging function."""
        messages = []

        def capture_log(msg):
            messages.append(msg)

        manager = ConcreteInventoryManager(
            client=None,
            identifier="test",
            dry_run=True,
            log_func=capture_log,
        )

        manager.clear_cache()
        assert "Cleared inventory cache" in messages

    def test_custom_cache_timeout(self):
        """Test custom cache timeout."""
        manager = ConcreteInventoryManager(
            client=None,
            identifier="test",
            dry_run=True,
            cache_timeout=timedelta(minutes=5),
        )

        manager.discover_inventory()
        assert manager.is_cache_valid() is True

        # Simulate time passing
        manager._last_sync = datetime.now() - timedelta(minutes=6)
        assert manager.is_cache_valid() is False


class TestInventoryItem:
    """Tests for InventoryItem base class."""

    def test_init(self):
        """Test initialization."""
        item = InventoryItem("item_1", "Test Item")
        assert item.item_id == "item_1"
        assert item.name == "Test Item"

    def test_to_dict(self):
        """Test dictionary conversion."""
        item = InventoryItem("item_1", "Test Item")
        result = item.to_dict()

        assert result == {"id": "item_1", "name": "Test Item"}

    def test_equality(self):
        """Test equality comparison."""
        item1 = InventoryItem("item_1", "Test Item")
        item2 = InventoryItem("item_1", "Different Name")
        item3 = InventoryItem("item_2", "Test Item")

        assert item1 == item2  # Same ID
        assert item1 != item3  # Different ID

    def test_hash(self):
        """Test hashing for use in sets/dicts."""
        item1 = InventoryItem("item_1", "Test Item")
        item2 = InventoryItem("item_1", "Different Name")

        # Same ID should have same hash
        assert hash(item1) == hash(item2)

        # Can be used in sets
        item_set = {item1, item2}
        assert len(item_set) == 1  # Deduped by ID

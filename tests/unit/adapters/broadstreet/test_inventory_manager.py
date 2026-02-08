"""Unit tests for Broadstreet Inventory Manager."""

import pytest

from src.adapters.broadstreet.managers.inventory import (
    BroadstreetInventoryManager,
    ZoneInfo,
)


class TestZoneInfo:
    """Tests for ZoneInfo class."""

    def test_init_minimal(self):
        """Test ZoneInfo with minimal parameters."""
        zone = ZoneInfo(zone_id="zone_1", name="Test Zone")

        assert zone.zone_id == "zone_1"
        assert zone.name == "Test Zone"
        assert zone.width is None
        assert zone.height is None
        assert zone.display_type == "standard"
        assert zone.ad_count == 1

    def test_init_full(self):
        """Test ZoneInfo with all parameters."""
        zone = ZoneInfo(
            zone_id="zone_1",
            name="Banner Zone",
            width=728,
            height=90,
            display_type="rotation",
            ad_count=3,
        )

        assert zone.width == 728
        assert zone.height == 90
        assert zone.display_type == "rotation"
        assert zone.ad_count == 3

    def test_to_dict(self):
        """Test ZoneInfo serialization."""
        zone = ZoneInfo(
            zone_id="zone_1",
            name="Test Zone",
            width=300,
            height=250,
        )

        result = zone.to_dict()

        assert result["zone_id"] == "zone_1"
        assert result["name"] == "Test Zone"
        assert result["width"] == 300
        assert result["height"] == 250


class TestBroadstreetInventoryManager:
    """Tests for BroadstreetInventoryManager."""

    @pytest.fixture
    def manager(self):
        """Create an inventory manager in dry-run mode."""
        return BroadstreetInventoryManager(
            client=None,
            network_id="net_123",
            dry_run=True,
        )

    def test_fetch_zones_dry_run(self, manager):
        """Test fetching zones in dry-run mode."""
        zones = manager.fetch_zones()

        assert len(zones) > 0
        assert all(isinstance(z, ZoneInfo) for z in zones)

        # Verify some expected zones
        zone_ids = [z.zone_id for z in zones]
        assert "zone_1" in zone_ids
        assert "zone_2" in zone_ids

    def test_fetch_zones_cached(self, manager):
        """Test that zones are cached."""
        # First fetch
        zones1 = manager.fetch_zones()

        # Second fetch should use cache
        zones2 = manager.fetch_zones()

        assert zones1 == zones2
        assert len(manager._zone_cache) > 0

    def test_fetch_zones_refresh(self, manager):
        """Test forcing refresh of zone cache."""
        # First fetch
        manager.fetch_zones()

        # Force refresh
        zones = manager.fetch_zones(refresh=True)

        assert len(zones) > 0

    def test_get_zone(self, manager):
        """Test getting zone by ID."""
        manager.fetch_zones()

        zone = manager.get_zone("zone_1")
        assert zone is not None
        assert zone.zone_id == "zone_1"

        # Non-existent zone
        zone = manager.get_zone("zone_unknown")
        assert zone is None

    def test_get_zone_auto_fetch(self, manager):
        """Test that get_zone auto-fetches if cache is empty."""
        # Don't explicitly fetch first
        zone = manager.get_zone("zone_1")

        # Should have fetched automatically
        assert zone is not None
        assert len(manager._zone_cache) > 0

    def test_validate_zone_ids(self, manager):
        """Test validating zone IDs."""
        valid, invalid = manager.validate_zone_ids(["zone_1", "zone_2", "zone_unknown"])

        assert "zone_1" in valid
        assert "zone_2" in valid
        assert "zone_unknown" in invalid

    def test_validate_zone_ids_all_valid(self, manager):
        """Test validating all valid zone IDs."""
        valid, invalid = manager.validate_zone_ids(["zone_1", "zone_2"])

        assert len(valid) == 2
        assert len(invalid) == 0

    def test_validate_zone_ids_all_invalid(self, manager):
        """Test validating all invalid zone IDs."""
        valid, invalid = manager.validate_zone_ids(["unknown_1", "unknown_2"])

        assert len(valid) == 0
        assert len(invalid) == 2

    def test_get_zones_by_size(self, manager):
        """Test getting zones by size."""
        manager.fetch_zones()

        # Get zones matching 728x90 (from simulated data)
        zones = manager.get_zones_by_size(728, 90)
        assert len(zones) >= 1
        assert all(z.width == 728 and z.height == 90 for z in zones)

    def test_get_zones_by_size_no_match(self, manager):
        """Test getting zones with no matching size."""
        manager.fetch_zones()

        zones = manager.get_zones_by_size(999, 999)
        assert len(zones) == 0

    def test_build_inventory_response(self, manager):
        """Test building inventory response."""
        response = manager.build_inventory_response()

        assert "zones" in response
        assert "ad_units" in response
        assert "targeting_options" in response
        assert "creative_specs" in response
        assert "properties" in response

        # Check zones
        assert len(response["zones"]) > 0

        # Check properties
        assert response["properties"]["supports_webhooks"] is False
        assert response["properties"]["network_id"] == "net_123"

        # Check creative specs
        formats = [spec["format"] for spec in response["creative_specs"]]
        assert "display" in formats
        assert "html" in formats
        assert "text" in formats

    def test_sync_zones_to_products(self, manager):
        """Test generating product suggestions from zones."""
        suggestions = manager.sync_zones_to_products()

        assert len(suggestions) > 0

        for suggestion in suggestions:
            assert "name" in suggestion
            assert "description" in suggestion
            assert "implementation_config" in suggestion
            assert "reporting_capabilities" in suggestion

            # Check implementation config
            config = suggestion["implementation_config"]
            assert "targeted_zone_ids" in config
            assert "creative_sizes" in config
            assert config["cost_type"] == "CPM"
            assert config["automation_mode"] == "automatic"

            # Check reporting capabilities
            caps = suggestion["reporting_capabilities"]
            assert caps["supports_webhooks"] is False

    def test_clear_cache(self, manager):
        """Test clearing the zone cache."""
        # Fetch zones to populate cache
        manager.fetch_zones()
        assert len(manager._zone_cache) > 0

        # Clear cache
        manager.clear_cache()
        assert len(manager._zone_cache) == 0

    def test_fetch_zones_empty_when_no_client(self):
        """Test that fetch returns empty when no client and not dry-run."""
        manager = BroadstreetInventoryManager(
            client=None,
            network_id="net_123",
            dry_run=False,  # Not dry run but no client
        )

        zones = manager.fetch_zones()
        assert zones == []


class TestBaseInventoryManagerInterface:
    """Tests for BaseInventoryManager interface implementation."""

    @pytest.fixture
    def manager(self):
        """Create an inventory manager in dry-run mode."""
        return BroadstreetInventoryManager(
            client=None,
            network_id="net_123",
            dry_run=True,
        )

    def test_discover_inventory(self, manager):
        """Test discover_inventory abstract method."""
        items = manager.discover_inventory()

        assert len(items) > 0
        assert all(isinstance(z, ZoneInfo) for z in items)

    def test_discover_inventory_refresh(self, manager):
        """Test discover_inventory with refresh."""
        items1 = manager.discover_inventory()
        items2 = manager.discover_inventory(refresh=True)

        assert len(items1) == len(items2)

    def test_validate_inventory_ids(self, manager):
        """Test validate_inventory_ids abstract method."""
        valid, invalid = manager.validate_inventory_ids(["zone_1", "zone_unknown"])

        assert "zone_1" in valid
        assert "zone_unknown" in invalid

    def test_suggest_products(self, manager):
        """Test suggest_products abstract method."""
        suggestions = manager.suggest_products()

        assert len(suggestions) > 0
        for suggestion in suggestions:
            assert "name" in suggestion
            assert "implementation_config" in suggestion

    def test_extends_base_inventory_manager(self):
        """Test that BroadstreetInventoryManager extends BaseInventoryManager."""
        from src.adapters.base_inventory import BaseInventoryManager

        assert issubclass(BroadstreetInventoryManager, BaseInventoryManager)

    def test_zone_info_extends_inventory_item(self):
        """Test that ZoneInfo extends InventoryItem."""
        from src.adapters.base_inventory import InventoryItem

        assert issubclass(ZoneInfo, InventoryItem)

    def test_zone_info_inherits_equality(self):
        """Test ZoneInfo inherits equality from InventoryItem."""
        zone1 = ZoneInfo(zone_id="zone_1", name="Zone One")
        zone2 = ZoneInfo(zone_id="zone_1", name="Different Name")

        # Should be equal by ID (inherited from InventoryItem)
        assert zone1 == zone2

    def test_is_cache_valid(self, manager):
        """Test cache validity tracking."""
        assert not manager.is_cache_valid()

        manager.discover_inventory()
        assert manager.is_cache_valid()


class TestInventoryManagerWithMockedClient:
    """Tests for inventory manager with mocked client."""

    def test_fetch_zones_from_client(self):
        """Test fetching zones from mocked client."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.get_zones.return_value = [
            {"id": "123", "name": "API Zone", "width": 300, "height": 250},
            {"Id": "456", "Name": "Another Zone", "Width": 728, "Height": 90},
        ]

        manager = BroadstreetInventoryManager(
            client=mock_client,
            network_id="net_123",
            dry_run=False,
        )

        zones = manager.fetch_zones()

        assert len(zones) == 2
        assert zones[0].zone_id == "123"
        assert zones[0].name == "API Zone"
        assert zones[1].zone_id == "456"
        assert zones[1].name == "Another Zone"

    def test_fetch_zones_handles_error(self):
        """Test that fetch handles client errors gracefully."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.get_zones.side_effect = Exception("API Error")

        manager = BroadstreetInventoryManager(
            client=mock_client,
            network_id="net_123",
            dry_run=False,
        )

        zones = manager.fetch_zones()

        # Should return empty list on error
        assert zones == []

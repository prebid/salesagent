"""Unit tests for Broadstreet Inventory Manager.

Inventory is data the TEST states. The manager used to answer ``fetch_zones`` from a
fixed list it invented whenever ``dry_run`` was set — "Top Banner" 728x90, "Sidebar"
300x250, and two more — and fifteen tests here asserted those invented zones and the
cache, filter, product-suggestion and interface results derived from them. That branch is
gone, so the zones come from the vendor stand-in, and the assertions can name what the
manager DID with them.
"""

import pytest

from src.adapters.broadstreet.managers.inventory import (
    BroadstreetInventoryManager,
    ZoneInfo,
)
from tests.helpers.broadstreet_client import stub_broadstreet_client, zone_payload

#: The zones the stand-in vendor serves. Two share a size so the size filter has
#: something to select from and something to leave behind.
NETWORK_ZONES = (
    zone_payload("zone_1", "Top Banner", width=728, height=90),
    zone_payload("zone_2", "Sidebar", width=300, height=250),
    zone_payload("zone_3", "Footer Banner", width=728, height=90),
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
    def client(self):
        """The stand-in vendor, serving NETWORK_ZONES."""
        return stub_broadstreet_client(zones=NETWORK_ZONES)

    @pytest.fixture
    def manager(self, client):
        """Create an inventory manager over the stand-in vendor."""
        return BroadstreetInventoryManager(
            client=client,
            network_id="net_123",
        )

    # ``test_fetch_zones_dry_run`` stood here, asserting that the fabricated "zone_1" and
    # "zone_2" came back. Nothing replaced it: the mapping of a vendor zone payload onto
    # ``ZoneInfo`` — including both ``id``/``Id`` casings — is graded by
    # ``TestInventoryManagerWithMockedClient`` below, and the cold-cache fetch is graded by
    # the two cache tests that follow. A third test of the same call would have been a
    # fourth copy of one obligation.

    def test_fetch_zones_cached(self, manager, client):
        """A second fetch is served from the cache, not from Broadstreet."""
        zones1 = manager.fetch_zones()
        zones2 = manager.fetch_zones()

        assert zones1 == zones2
        assert len(manager._zone_cache) == len(NETWORK_ZONES)
        client.get_zones.assert_called_once_with()

    def test_fetch_zones_refresh(self, manager, client):
        """``refresh=True`` goes back to Broadstreet even with a warm cache."""
        manager.fetch_zones()
        zones = manager.fetch_zones(refresh=True)

        assert len(zones) == len(NETWORK_ZONES)
        assert client.get_zones.call_count == 2

    def test_get_zone(self, manager):
        """Test getting zone by ID."""
        manager.fetch_zones()

        zone = manager.get_zone("zone_1")
        assert zone is not None
        assert zone.zone_id == "zone_1"

        # Non-existent zone
        zone = manager.get_zone("zone_unknown")
        assert zone is None

    def test_get_zone_auto_fetch(self, manager, client):
        """get_zone fills an empty cache from Broadstreet rather than answering None."""
        zone = manager.get_zone("zone_1")

        client.get_zones.assert_called_once_with()
        assert zone is not None
        assert len(manager._zone_cache) == len(NETWORK_ZONES)

    @pytest.mark.parametrize(
        ("requested", "expected_valid", "expected_invalid"),
        [
            pytest.param(["zone_1", "zone_2", "zone_unknown"], ["zone_1", "zone_2"], ["zone_unknown"], id="mixed"),
            pytest.param(["zone_1", "zone_2"], ["zone_1", "zone_2"], [], id="all-valid"),
            pytest.param(["unknown_1", "unknown_2"], [], ["unknown_1", "unknown_2"], id="all-invalid"),
        ],
    )
    def test_validate_zone_ids(self, manager, requested, expected_valid, expected_invalid):
        """A product's configured zones are split into those the network has and those it does not.

        The three cases were three tests. The all-invalid one passed vacuously while the
        manager had no zones at all — everything is invalid against an empty network —
        so it only discriminates now that the cases share a stated inventory.
        """
        valid, invalid = manager.validate_zone_ids(requested)

        assert valid == expected_valid
        assert invalid == expected_invalid

    @pytest.mark.parametrize(
        ("width", "height", "expected_zone_ids"),
        [
            pytest.param(728, 90, ["zone_1", "zone_3"], id="two-zones-share-the-size"),
            pytest.param(300, 250, ["zone_2"], id="one-zone"),
            pytest.param(999, 999, [], id="no-match"),
        ],
    )
    def test_get_zones_by_size(self, manager, width, height, expected_zone_ids):
        """The size filter selects the zones that accept a creative of that size.

        Asserting WHICH zones, not merely how many: a filter that returned the whole
        network would satisfy a ``len(...) >= 1``.
        """
        manager.fetch_zones()

        zones = manager.get_zones_by_size(width, height)

        assert [zone.zone_id for zone in zones] == expected_zone_ids

    def test_build_inventory_response(self, manager):
        """Test building inventory response."""
        response = manager.build_inventory_response()

        assert "zones" in response
        assert "ad_units" in response
        assert "targeting_options" in response
        assert "creative_specs" in response
        assert "properties" in response

        # Every zone the network serves is offered
        assert len(response["zones"]) == len(NETWORK_ZONES)

        # Check properties
        assert response["properties"]["supports_webhooks"] is False
        assert response["properties"]["network_id"] == "net_123"

        # Check creative specs
        formats = [spec["format"] for spec in response["creative_specs"]]
        assert "display" in formats
        assert "html" in formats
        assert "text" in formats

    def test_sync_zones_to_products(self, manager):
        """One product suggestion per creative SIZE, targeting every zone of that size.

        The grouping is the whole point of the derivation, and it is what the old
        ``len(suggestions) > 0`` could not show: the network's two 728x90 zones become a
        single product that targets both, not one product each.
        """
        suggestions = manager.sync_zones_to_products()

        assert [(s["name"], s["implementation_config"]["targeted_zone_ids"]) for s in suggestions] == [
            ("Broadstreet 728x90 Display", ["zone_1", "zone_3"]),
            ("Broadstreet 300x250 Display", ["zone_2"]),
        ]

        for suggestion in suggestions:
            assert "name" in suggestion
            assert "description" in suggestion
            assert "implementation_config" in suggestion
            assert "reporting_capabilities" in suggestion

            # Check implementation config
            config = suggestion["implementation_config"]
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
        assert len(manager._zone_cache) == len(NETWORK_ZONES)

        # Clear cache
        manager.clear_cache()
        assert len(manager._zone_cache) == 0


class TestBaseInventoryManagerInterface:
    """Tests for BaseInventoryManager interface implementation.

    ``discover_inventory``, ``validate_inventory_ids`` and ``suggest_products`` are
    one-line ``return self.<concrete>(...)`` delegations, so the three tests that
    exercised them separately graded the same one fact three times, each through the
    concrete method its sibling above already grades. They are now the single routing
    test below.

    ``test_discover_inventory_refresh`` went with them: it asserted
    ``len(items1) == len(items2)`` over a fixed network, which holds for any
    implementation that returns the same zones twice — and held as ``0 == 0`` while the
    manager had no zones at all. Refresh is graded on ``fetch_zones`` above, where the
    assertion is that Broadstreet was asked a second time.
    """

    @pytest.fixture
    def client(self):
        """The stand-in vendor, serving NETWORK_ZONES."""
        return stub_broadstreet_client(zones=NETWORK_ZONES)

    @pytest.fixture
    def manager(self, client):
        """Create an inventory manager over the stand-in vendor."""
        return BroadstreetInventoryManager(
            client=client,
            network_id="net_123",
        )

    def test_interface_methods_route_to_the_broadstreet_ones(self, manager):
        """The three abstract methods the base class requires answer for zones.

        What the base interface calls "inventory" is a Broadstreet zone, so each alias
        must return what its zone-named counterpart returns — that routing is the only
        thing these three add over the tests above.
        """
        assert manager.discover_inventory() == manager.fetch_zones()
        assert manager.validate_inventory_ids(["zone_1", "zone_unknown"]) == manager.validate_zone_ids(
            ["zone_1", "zone_unknown"]
        )
        assert manager.suggest_products() == manager.sync_zones_to_products()

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
        )

        zones = manager.fetch_zones()

        # Should return empty list on error
        assert zones == []

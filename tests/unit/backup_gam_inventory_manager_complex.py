"""
Simplified unit tests for GAMInventoryManager class.

Focuses on core inventory management functionality with minimal mocking
to comply with pre-commit limits. Complex integration scenarios moved to
integration test files.
"""

from datetime import timedelta
from unittest.mock import Mock, patch

from src.adapters.gam.managers.inventory import GAMInventoryManager, MockGAMInventoryDiscovery
from tests.unit.helpers.gam_mock_factory import GAMClientMockFactory, GAMDataFactory


class TestGAMInventoryManagerCore:
    """Core functionality tests with minimal mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.tenant_id = "test_tenant_123"

    def test_init_with_valid_parameters(self):
        """Test initialization with valid parameters."""
        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        assert inventory_manager.client_manager == self.mock_client_manager
        assert inventory_manager.tenant_id == self.tenant_id
        assert inventory_manager.dry_run is True
        assert inventory_manager._discovery is None
        assert inventory_manager._cache_timeout == timedelta(hours=24)

    def test_dry_run_mode_uses_mock_discovery(self):
        """Test that dry-run mode creates MockGAMInventoryDiscovery."""
        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        discovery = inventory_manager._get_discovery()

        assert isinstance(discovery, MockGAMInventoryDiscovery)
        assert discovery.tenant_id == self.tenant_id
        assert inventory_manager._discovery == discovery

    def test_discovery_caching_behavior(self):
        """Test that discovery instance is cached after first creation."""
        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        # First call creates discovery
        discovery1 = inventory_manager._get_discovery()
        # Second call should return cached instance
        discovery2 = inventory_manager._get_discovery()

        assert discovery1 == discovery2
        assert isinstance(discovery1, MockGAMInventoryDiscovery)

    def test_discover_ad_units_delegates_to_discovery(self):
        """Test that discover_ad_units properly delegates to discovery instance."""
        mock_discovery = Mock()
        mock_ad_units = [GAMDataFactory.create_ad_unit_data("unit_1"), GAMDataFactory.create_ad_unit_data("unit_2")]
        mock_discovery.discover_ad_units.return_value = mock_ad_units

        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=False)
        inventory_manager._discovery = mock_discovery  # Set cached discovery

        result = inventory_manager.discover_ad_units(parent_id="root", max_depth=5)

        mock_discovery.discover_ad_units.assert_called_once_with(parent_id="root", max_depth=5)
        assert result == mock_ad_units

    def test_get_ad_unit_delegates_to_discovery(self):
        """Test that get_ad_unit properly delegates to discovery instance."""
        mock_discovery = Mock()
        mock_ad_unit = GAMDataFactory.create_ad_unit_data("specific_unit")
        mock_discovery.get_ad_unit.return_value = mock_ad_unit

        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=False)
        inventory_manager._discovery = mock_discovery  # Set cached discovery

        result = inventory_manager.get_ad_unit("specific_unit")

        mock_discovery.get_ad_unit.assert_called_once_with("specific_unit")
        assert result == mock_ad_unit

    def test_search_ad_units_delegates_to_discovery(self):
        """Test that search_ad_units properly delegates to discovery instance."""
        mock_discovery = Mock()
        mock_results = [GAMDataFactory.create_ad_unit_data("search_result")]
        mock_discovery.search_ad_units.return_value = mock_results

        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=False)
        inventory_manager._discovery = mock_discovery  # Set cached discovery

        result = inventory_manager.search_ad_units("sports")

        mock_discovery.search_ad_units.assert_called_once_with("sports")
        assert result == mock_results

    def test_get_placements_delegates_to_discovery(self):
        """Test that get_placements properly delegates to discovery instance."""
        mock_discovery = Mock()
        mock_placements = [{"id": "placement_1", "name": "Sports Placement"}]
        mock_discovery.get_placements.return_value = mock_placements

        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=False)
        inventory_manager._discovery = mock_discovery  # Set cached discovery

        result = inventory_manager.get_placements()

        mock_discovery.get_placements.assert_called_once()
        assert result == mock_placements

    @patch("src.adapters.gam.managers.inventory.GAMInventoryDiscovery")
    def test_real_discovery_creation_in_production_mode(self, mock_discovery_class):
        """Test that production mode creates real GAMInventoryDiscovery instance."""
        mock_discovery_instance = Mock()
        mock_discovery_class.return_value = mock_discovery_instance

        inventory_manager = GAMInventoryManager(self.mock_client_manager, self.tenant_id, dry_run=False)

        discovery = inventory_manager._get_discovery()

        mock_discovery_class.assert_called_once_with(self.mock_client_manager.get_client.return_value, self.tenant_id)
        assert discovery == mock_discovery_instance

    def test_cache_timeout_configuration(self):
        """Test that cache timeout can be configured during initialization."""
        custom_timeout = timedelta(hours=12)
        inventory_manager = GAMInventoryManager(
            self.mock_client_manager, self.tenant_id, dry_run=False, cache_timeout=custom_timeout
        )

        assert inventory_manager._cache_timeout == custom_timeout


class TestMockGAMInventoryDiscoveryDirectly:
    """Direct tests of MockGAMInventoryDiscovery functionality."""

    def test_mock_discovery_initialization(self):
        """Test MockGAMInventoryDiscovery initialization."""
        mock_discovery = MockGAMInventoryDiscovery("test_tenant")

        assert mock_discovery.tenant_id == "test_tenant"
        assert mock_discovery.client is None

    def test_mock_discovery_generates_realistic_ad_units(self):
        """Test that mock discovery generates realistic ad unit data."""
        mock_discovery = MockGAMInventoryDiscovery("test_tenant")

        ad_units = mock_discovery.discover_ad_units(parent_id="root", max_depth=2)

        assert len(ad_units) > 0
        # Check first ad unit has expected structure
        first_unit = ad_units[0]
        assert "id" in first_unit
        assert "name" in first_unit
        assert "adUnitCode" in first_unit
        assert "status" in first_unit

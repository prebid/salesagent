"""
Simplified unit tests for GAMOrdersManager class.

Focuses on core order management functionality with minimal mocking
to comply with pre-commit limits. Complex integration scenarios moved to
integration test files.
"""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.adapters.gam.managers.orders import GAMOrdersManager
from tests.unit.helpers.gam_mock_factory import (
    GAMClientMockFactory,
    GAMDataFactory,
    GAMServiceMockFactory,
    GAMTestSetup,
)


class TestGAMOrdersManagerCore:
    """Core functionality tests with minimal mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_context = GAMTestSetup.create_standard_context()
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.advertiser_id = self.test_context["advertiser_id"]
        self.trafficker_id = self.test_context["trafficker_id"]
        self.start_time = self.test_context["dates"]["start_time"]
        self.end_time = self.test_context["dates"]["end_time"]

    def test_init_with_valid_parameters(self):
        """Test initialization with valid parameters."""
        orders_manager = GAMOrdersManager(
            self.mock_client_manager, self.advertiser_id, self.trafficker_id, dry_run=False
        )

        assert orders_manager.client_manager == self.mock_client_manager
        assert orders_manager.advertiser_id == self.advertiser_id
        assert orders_manager.trafficker_id == self.trafficker_id
        assert orders_manager.dry_run is False

    def test_init_with_dry_run_enabled(self):
        """Test initialization with dry_run enabled."""
        orders_manager = GAMOrdersManager(
            self.mock_client_manager, self.advertiser_id, self.trafficker_id, dry_run=True
        )

        assert orders_manager.dry_run is True

    def test_create_order_success(self):
        """Test successful order creation."""
        mock_order_service = GAMServiceMockFactory.create_order_service()
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        order_id = orders_manager.create_order(
            order_name="Test Order", total_budget=5000.0, start_time=self.start_time, end_time=self.end_time
        )

        assert order_id == "54321"  # From mock factory
        self.mock_client_manager.get_service.assert_called_once_with("OrderService")
        mock_order_service.createOrders.assert_called_once()

    def test_create_order_with_optional_parameters(self):
        """Test order creation with optional PO number and team IDs."""
        mock_order_service = GAMServiceMockFactory.create_order_service()
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        order_id = orders_manager.create_order(
            order_name="Test Order with PO",
            total_budget=10000.0,
            start_time=self.start_time,
            end_time=self.end_time,
            po_number="PO-2025-001",
            applied_team_ids=["team_1", "team_2"],
        )

        assert order_id == "54321"  # From mock factory
        mock_order_service.createOrders.assert_called_once()

        # Verify order structure includes optional fields
        call_args = mock_order_service.createOrders.call_args[0][0]
        order_data = call_args[0]

        assert order_data["externalOrderId"] == "PO-2025-001"
        assert order_data["appliedTeamIds"] == ["team_1", "team_2"]

    def test_get_order_delegates_to_service(self):
        """Test that get_order properly delegates to OrderService."""
        mock_order_service = Mock()
        mock_order_data = GAMDataFactory.create_order_data("12345", "Retrieved Order")
        mock_order_service.getOrdersByStatement.return_value.results = [mock_order_data]
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        result = orders_manager.get_order("12345")

        assert result == mock_order_data
        self.mock_client_manager.get_service.assert_called_once_with("OrderService")
        mock_order_service.getOrdersByStatement.assert_called_once()

    def test_get_order_not_found_returns_none(self):
        """Test that get_order returns None when order is not found."""
        mock_order_service = Mock()
        mock_order_service.getOrdersByStatement.return_value.results = []
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        result = orders_manager.get_order("nonexistent")

        assert result is None

    def test_update_order_status_success(self):
        """Test successful order status update."""
        mock_order_service = Mock()
        updated_order = GAMDataFactory.create_order_data("54321", "Updated Order")
        updated_order["status"] = "APPROVED"
        mock_order_service.updateOrders.return_value = [updated_order]
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        result = orders_manager.update_order_status("54321", "APPROVED")

        assert result == updated_order
        mock_order_service.updateOrders.assert_called_once()

    def test_get_orders_by_advertiser_delegates_to_service(self):
        """Test that get_orders_by_advertiser properly delegates to OrderService."""
        mock_order_service = Mock()
        mock_orders = [GAMDataFactory.create_order_data("1"), GAMDataFactory.create_order_data("2")]
        mock_order_service.getOrdersByStatement.return_value.results = mock_orders
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        result = orders_manager.get_orders_by_advertiser()

        assert result == mock_orders
        mock_order_service.getOrdersByStatement.assert_called_once()

    def test_delete_order_delegates_to_service(self):
        """Test that delete_order properly delegates to OrderService."""
        mock_order_service = Mock()
        mock_order_service.performOrderAction.return_value = Mock()
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        orders_manager.delete_order("54321")

        mock_order_service.performOrderAction.assert_called_once()
        # Verify delete action was called
        call_args = mock_order_service.performOrderAction.call_args[0]
        assert "ArchiveOrders" in str(call_args[0])  # Action type

    @patch("src.adapters.gam.managers.orders.logger")
    def test_dry_run_mode_logs_operations(self, mock_logger):
        """Test that dry-run mode logs operations without making actual calls."""
        orders_manager = GAMOrdersManager(
            self.mock_client_manager, self.advertiser_id, self.trafficker_id, dry_run=True
        )

        # Dry-run operations should return success without calling services
        order_id = orders_manager.create_order(
            order_name="Dry Run Order", total_budget=1000.0, start_time=self.start_time, end_time=self.end_time
        )

        # Should return a simulated ID
        assert order_id is not None
        # Should not call actual service
        self.mock_client_manager.get_service.assert_not_called()
        # Should log the dry-run operation
        mock_logger.info.assert_called()


class TestGAMOrdersManagerErrorHandling:
    """Error handling and edge case tests."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_context = GAMTestSetup.create_standard_context()
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.advertiser_id = self.test_context["advertiser_id"]
        self.trafficker_id = self.test_context["trafficker_id"]

    def test_create_order_service_error_propagates(self):
        """Test that service errors during order creation are propagated."""
        mock_order_service = Mock()
        mock_order_service.createOrders.side_effect = Exception("GAM API Error")
        self.mock_client_manager.get_service.return_value = mock_order_service

        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        with pytest.raises(Exception, match="GAM API Error"):
            orders_manager.create_order(
                order_name="Failed Order", total_budget=1000.0, start_time=datetime.now(), end_time=datetime.now()
            )

    def test_invalid_budget_raises_error(self):
        """Test that invalid budget values raise appropriate errors."""
        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        with pytest.raises(ValueError, match="Budget must be positive"):
            orders_manager.create_order(
                order_name="Invalid Budget Order",
                total_budget=-1000.0,
                start_time=datetime.now(),
                end_time=datetime.now(),
            )

    def test_invalid_date_range_raises_error(self):
        """Test that invalid date ranges raise appropriate errors."""
        orders_manager = GAMOrdersManager(self.mock_client_manager, self.advertiser_id, self.trafficker_id)

        end_date = datetime(2025, 1, 1)
        start_date = datetime(2025, 2, 1)  # Start after end

        with pytest.raises(ValueError, match="Start time must be before end time"):
            orders_manager.create_order(
                order_name="Invalid Date Order", total_budget=1000.0, start_time=start_date, end_time=end_date
            )

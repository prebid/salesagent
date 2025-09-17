"""
Centralized mock factory for GAM-related testing.

Reduces duplicate mock creation and provides standardized test fixtures
to help keep test files under the 10-mock limit enforced by pre-commit hooks.
"""

from datetime import datetime
from typing import Any
from unittest.mock import Mock


class GAMClientMockFactory:
    """Factory for creating standardized GAM client mocks."""

    @staticmethod
    def create_client_manager(network_code: str = "12345678") -> Mock:
        """Create a mock GAMClientManager with standard configuration."""
        mock_client_manager = Mock()
        mock_client_manager.network_code = network_code
        mock_client_manager.get_client.return_value = GAMClientMockFactory.create_gam_client()
        mock_client_manager.get_service.return_value = Mock()
        mock_client_manager.check_health.return_value = {"status": "healthy"}
        return mock_client_manager

    @staticmethod
    def create_gam_client() -> Mock:
        """Create a mock AdManagerClient."""
        mock_client = Mock()
        mock_client.GetService.return_value = Mock()
        return mock_client

    @staticmethod
    def create_auth_manager(config: dict[str, Any] = None) -> Mock:
        """Create a mock GAMAuthManager."""
        if config is None:
            config = {"refresh_token": "test_token", "network_code": "12345678"}

        mock_auth = Mock()
        mock_auth.config = config
        mock_auth.get_credentials.return_value = Mock()
        mock_auth.get_auth_method.return_value = "oauth"
        return mock_auth


class GAMServiceMockFactory:
    """Factory for creating standardized GAM service mocks."""

    @staticmethod
    def create_order_service() -> Mock:
        """Create a mock OrderService with standard responses."""
        mock_service = Mock()
        mock_service.createOrders.return_value = [{"id": 54321, "name": "Test Order"}]
        mock_service.getOrdersByStatement.return_value = Mock()
        mock_service.updateOrders.return_value = [{"id": 54321, "status": "APPROVED"}]
        return mock_service

    @staticmethod
    def create_line_item_service() -> Mock:
        """Create a mock LineItemService with standard responses."""
        mock_service = Mock()
        mock_service.createLineItems.return_value = [{"id": 98765, "name": "Test Line Item"}]
        mock_service.getLineItemsByStatement.return_value = Mock()
        mock_service.updateLineItems.return_value = [{"id": 98765, "status": "READY"}]
        return mock_service

    @staticmethod
    def create_creative_service() -> Mock:
        """Create a mock CreativeService with standard responses."""
        mock_service = Mock()
        mock_service.createCreatives.return_value = [{"id": 13579, "name": "Test Creative"}]
        mock_service.getCreativesByStatement.return_value = Mock()
        return mock_service

    @staticmethod
    def create_inventory_service() -> Mock:
        """Create a mock InventoryService with standard responses."""
        mock_service = Mock()
        mock_service.getAdUnitsByStatement.return_value = Mock()
        mock_service.getPlacementsByStatement.return_value = Mock()
        return mock_service

    @staticmethod
    def create_network_service() -> Mock:
        """Create a mock NetworkService with standard responses."""
        mock_service = Mock()
        mock_service.getCurrentNetwork.return_value = {
            "networkCode": "12345678",
            "displayName": "Test Network",
            "timeZone": "America/New_York",
        }
        return mock_service


class GAMDataFactory:
    """Factory for creating standardized GAM data objects."""

    @staticmethod
    def create_order_data(order_id: str = "54321", name: str = "Test Order") -> dict[str, Any]:
        """Create standard order data."""
        return {
            "id": order_id,
            "name": name,
            "advertiserId": "123456",
            "traffickerId": "987654",
            "status": "DRAFT",
            "startDateTime": {
                "date": {"year": 2025, "month": 3, "day": 1},
                "hour": 0,
                "minute": 0,
                "second": 0,
                "timeZoneId": "America/New_York",
            },
            "endDateTime": {
                "date": {"year": 2025, "month": 3, "day": 31},
                "hour": 23,
                "minute": 59,
                "second": 59,
                "timeZoneId": "America/New_York",
            },
        }

    @staticmethod
    def create_line_item_data(line_item_id: str = "98765", name: str = "Test Line Item") -> dict[str, Any]:
        """Create standard line item data."""
        return {
            "id": line_item_id,
            "name": name,
            "orderId": "54321",
            "status": "READY",
            "lineItemType": "STANDARD",
            "costType": "CPM",
            "costPerUnit": {"currencyCode": "USD", "microAmount": 5000000},
            "primaryGoal": {"goalType": "LIFETIME", "unitType": "IMPRESSIONS", "units": 100000},
        }

    @staticmethod
    def create_creative_data(creative_id: str = "13579", name: str = "Test Creative") -> dict[str, Any]:
        """Create standard creative data."""
        return {
            "id": creative_id,
            "name": name,
            "advertiserId": "123456",
            "size": {"width": 300, "height": 250},
            "snippet": "<div>Test Creative Content</div>",
            "previewUrl": "https://example.com/preview",
        }

    @staticmethod
    def create_ad_unit_data(ad_unit_id: str = "456789", name: str = "Test Ad Unit") -> dict[str, Any]:
        """Create standard ad unit data."""
        return {
            "id": ad_unit_id,
            "name": name,
            "parentId": "123456",
            "hasChildren": False,
            "adUnitCode": "test-ad-unit-code",
            "status": "ACTIVE",
            "targetWindow": "BLANK",
        }


class GAMTestSetup:
    """Centralized test setup for common GAM testing patterns."""

    @staticmethod
    def create_standard_context(tenant_id: str = "test_tenant", advertiser_id: str = "123456") -> dict[str, Any]:
        """Create standard test context with common configuration."""
        return {
            "tenant_id": tenant_id,
            "advertiser_id": advertiser_id,
            "trafficker_id": "987654",
            "network_code": "12345678",
            "config": {"refresh_token": "test_token", "network_code": "12345678"},
            "dates": {"start_time": datetime(2025, 3, 1, 0, 0, 0), "end_time": datetime(2025, 3, 31, 23, 59, 59)},
        }

    @staticmethod
    def create_mock_services() -> dict[str, Mock]:
        """Create a dictionary of all common GAM services."""
        return {
            "OrderService": GAMServiceMockFactory.create_order_service(),
            "LineItemService": GAMServiceMockFactory.create_line_item_service(),
            "CreativeService": GAMServiceMockFactory.create_creative_service(),
            "InventoryService": GAMServiceMockFactory.create_inventory_service(),
            "NetworkService": GAMServiceMockFactory.create_network_service(),
        }

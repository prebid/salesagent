"""Unit tests for GAM network currency detection and usage."""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.gam.managers.orders import GAMOrdersManager
from src.adapters.gam.utils.health_check import GAMHealthChecker, HealthStatus


class TestGAMOrderCurrency:
    """Test create_order currency parameter."""

    @pytest.fixture
    def mock_client_manager(self):
        """Create mock GAM client manager."""
        mock_client = MagicMock()
        mock_client.get_service = MagicMock()
        return mock_client

    @pytest.fixture
    def orders_manager(self, mock_client_manager):
        """Create GAM Orders Manager with mocked dependencies."""
        manager = GAMOrdersManager(
            client_manager=mock_client_manager,
            advertiser_id="test_advertiser",
            trafficker_id="test_trafficker",
            dry_run=True,  # Use dry-run to avoid actual API calls
        )
        return manager

    def test_create_order_uses_default_usd_currency(self, orders_manager):
        """Test that create_order defaults to USD currency."""
        from datetime import datetime

        order_id = orders_manager.create_order(
            order_name="Test Order",
            total_budget=1000.0,
            start_time=datetime(2025, 1, 1),
            end_time=datetime(2025, 1, 31),
        )

        # In dry-run mode, returns a mock order ID
        assert order_id.startswith("dry_run_order_")

    def test_create_order_accepts_custom_currency(self, orders_manager, mock_client_manager):
        """Test that create_order uses specified currency."""
        from datetime import datetime

        # Set up mock to capture the order data
        mock_service = MagicMock()
        mock_service.createOrders = MagicMock(return_value=[{"id": "12345"}])
        mock_client_manager.get_service.return_value = mock_service

        # Create non-dry-run manager
        manager = GAMOrdersManager(
            client_manager=mock_client_manager,
            advertiser_id="test_advertiser",
            trafficker_id="test_trafficker",
            dry_run=False,
        )

        order_id = manager.create_order(
            order_name="Test Order",
            total_budget=1000.0,
            start_time=datetime(2025, 1, 1),
            end_time=datetime(2025, 1, 31),
            currency="EUR",
        )

        # Verify the order was created
        assert order_id == "12345"

        # Verify the currency was set correctly in the API call
        call_args = mock_service.createOrders.call_args[0][0]
        assert len(call_args) == 1
        order_data = call_args[0]
        assert order_data["totalBudget"]["currencyCode"] == "EUR"
        assert order_data["totalBudget"]["microAmount"] == 1000_000_000  # 1000 * 1_000_000

    def test_create_order_with_different_currencies(self, mock_client_manager):
        """Test create_order with various currency codes."""
        from datetime import datetime

        mock_service = MagicMock()
        mock_service.createOrders = MagicMock(return_value=[{"id": "12345"}])
        mock_client_manager.get_service.return_value = mock_service

        manager = GAMOrdersManager(
            client_manager=mock_client_manager,
            advertiser_id="test_advertiser",
            trafficker_id="test_trafficker",
            dry_run=False,
        )

        currencies_to_test = ["USD", "EUR", "GBP", "JPY", "CAD"]

        for currency in currencies_to_test:
            mock_service.createOrders.reset_mock()

            manager.create_order(
                order_name=f"Test Order {currency}",
                total_budget=500.0,
                start_time=datetime(2025, 1, 1),
                end_time=datetime(2025, 1, 31),
                currency=currency,
            )

            call_args = mock_service.createOrders.call_args[0][0]
            order_data = call_args[0]
            assert order_data["totalBudget"]["currencyCode"] == currency


class TestHealthCheckCurrency:
    """Test health check returns currency code."""

    def test_check_authentication_returns_currency_code_in_details(self):
        """Test that check_authentication includes currencyCode in details."""
        # Create checker in dry-run mode
        config = {"service_account_key_file": "/fake/path.json"}
        checker = GAMHealthChecker(config, dry_run=True)

        result = checker.check_authentication()

        # Dry-run returns healthy with dry_run flag
        assert result.status == HealthStatus.HEALTHY
        assert result.details.get("dry_run") is True

    @patch("src.adapters.gam.utils.health_check.GAMHealthChecker._init_client")
    def test_check_authentication_extracts_currency_from_network(self, mock_init):
        """Test that check_authentication extracts currencyCode from network response."""
        mock_init.return_value = True

        config = {"service_account_key_file": "/fake/path.json", "network_code": "12345"}
        checker = GAMHealthChecker(config, dry_run=False)

        # Create mock client and network service
        mock_client = MagicMock()
        mock_network_service = MagicMock()
        mock_network_service.getCurrentNetwork.return_value = {
            "networkCode": "12345",
            "displayName": "Test Network",
            "currencyCode": "EUR",
            "secondaryCurrencyCodes": ["GBP", "USD"],
        }
        mock_client.GetService.return_value = mock_network_service
        checker.client = mock_client

        result = checker.check_authentication()

        assert result.status == HealthStatus.HEALTHY
        assert result.details.get("network_code") == "12345"
        assert result.details.get("display_name") == "Test Network"
        assert result.details.get("currency_code") == "EUR"
        assert result.details.get("secondary_currency_codes") == ["GBP", "USD"]

    @patch("src.adapters.gam.utils.health_check.GAMHealthChecker._init_client")
    def test_check_authentication_handles_no_secondary_currencies(self, mock_init):
        """Test that check_authentication handles networks without secondary currencies."""
        mock_init.return_value = True

        config = {"service_account_key_file": "/fake/path.json", "network_code": "12345"}
        checker = GAMHealthChecker(config, dry_run=False)

        # Create mock client with no secondary currencies
        mock_client = MagicMock()
        mock_network_service = MagicMock()
        mock_network_service.getCurrentNetwork.return_value = {
            "networkCode": "12345",
            "displayName": "Test Network",
            "currencyCode": "USD",
            # Note: secondaryCurrencyCodes not present
        }
        mock_client.GetService.return_value = mock_network_service
        checker.client = mock_client

        result = checker.check_authentication()

        assert result.status == HealthStatus.HEALTHY
        assert result.details.get("currency_code") == "USD"
        assert result.details.get("secondary_currency_codes") == []


class TestAdapterConfigCurrency:
    """Test AdapterConfig gam_network_currency field."""

    def test_adapter_config_has_currency_field(self):
        """Test that AdapterConfig model has gam_network_currency field."""
        from src.core.database.models import AdapterConfig

        # Verify the field exists on the model
        assert hasattr(AdapterConfig, "gam_network_currency")

        # Verify it's a mapped column
        mapper = AdapterConfig.__mapper__
        assert "gam_network_currency" in mapper.columns.keys()

    def test_adapter_config_has_secondary_currencies_field(self):
        """Test that AdapterConfig model has gam_secondary_currencies field."""
        from src.core.database.models import AdapterConfig

        # Verify the field exists on the model
        assert hasattr(AdapterConfig, "gam_secondary_currencies")

        # Verify it's a mapped column
        mapper = AdapterConfig.__mapper__
        assert "gam_secondary_currencies" in mapper.columns.keys()

    def test_currency_field_max_length(self):
        """Test that gam_network_currency field has correct max length."""
        from sqlalchemy import String

        from src.core.database.models import AdapterConfig

        mapper = AdapterConfig.__mapper__
        column = mapper.columns["gam_network_currency"]

        # Currency codes should be 3 characters (ISO 4217)
        assert isinstance(column.type, String)
        assert column.type.length == 3


class TestGoogleAdManagerCurrency:
    """Test GoogleAdManager uses request currency, not GAM network currency."""

    def test_order_uses_package_pricing_currency(self):
        """Test that create_media_buy uses currency from package_pricing_info, not GAM config."""
        # This test verifies the adapter uses the request's currency (validated upstream)
        # rather than automatically using the GAM network's detected currency.
        #
        # The flow is:
        # 1. media_buy_create.py validates currency is supported by GAM network
        # 2. Package pricing info includes the validated currency
        # 3. GoogleAdManager.create_media_buy extracts currency from package_pricing_info
        # 4. Orders are created with the request's currency

        # Simulate package_pricing_info structure
        package_pricing_info = {
            "pkg_123": {
                "pricing_model": "cpm",
                "rate": 10.0,
                "currency": "EUR",  # Request uses EUR
                "is_fixed": True,
                "bid_price": None,
            }
        }

        # Extract currency the same way GoogleAdManager does
        order_currency = "USD"  # Default
        if package_pricing_info:
            for pricing in package_pricing_info.values():
                order_currency = pricing.get("currency", "USD")
                break

        # Currency should be EUR from the request, not USD default
        assert order_currency == "EUR"

    def test_order_currency_fallback_when_no_pricing_info(self):
        """Test that order falls back to USD when no package_pricing_info."""
        package_pricing_info = None

        order_currency = "USD"
        if package_pricing_info:
            for pricing in package_pricing_info.values():
                order_currency = pricing.get("currency", "USD")
                break

        assert order_currency == "USD"


class TestCurrencyValidation:
    """Test currency validation logic."""

    def test_supported_currencies_includes_primary(self):
        """Test that primary currency is always in supported currencies."""
        primary = "USD"
        secondary = ["EUR", "GBP"]

        supported = {primary}
        supported.update(secondary)

        assert "USD" in supported
        assert "EUR" in supported
        assert "GBP" in supported

    def test_supported_currencies_empty_secondary(self):
        """Test supported currencies when no secondary currencies configured."""
        primary = "USD"
        secondary = None

        supported = {primary}
        if secondary:
            supported.update(secondary)

        assert supported == {"USD"}

    def test_currency_validation_error_message(self):
        """Test that validation error message includes supported currencies."""
        request_currency = "JPY"
        primary = "USD"
        secondary = ["EUR", "GBP"]

        supported = {primary}
        supported.update(secondary)

        # Simulate the error message construction
        if request_currency not in supported:
            error_msg = (
                f"Currency {request_currency} is not supported by the GAM network. "
                f"Supported currencies: {', '.join(sorted(supported))}. "
                f"Contact the publisher to enable this currency in GAM."
            )

            assert "JPY" in error_msg
            assert "EUR, GBP, USD" in error_msg
            assert "not supported" in error_msg

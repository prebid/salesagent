"""
Simplified unit tests for GAMClientManager class.

Focuses on core functionality with minimal mocking to comply with pre-commit limits.
Complex integration scenarios are tested in integration test files.
"""

from unittest.mock import Mock, patch

import pytest

from src.adapters.gam.client import GAMClientManager
from src.adapters.gam.utils.health_check import HealthStatus
from tests.unit.helpers.gam_mock_factory import GAMClientMockFactory, GAMTestSetup


class TestGAMClientManagerCore:
    """Core functionality tests with minimal mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_context = GAMTestSetup.create_standard_context()
        self.config = self.test_context["config"]
        self.network_code = self.test_context["network_code"]

    def test_init_with_valid_config(self):
        """Test initialization with valid configuration."""
        client_manager = GAMClientManager(self.config, self.network_code)

        assert client_manager.config == self.config
        assert client_manager.network_code == self.network_code
        assert client_manager._client is None
        assert client_manager._health_checker is None

    def test_reset_client_clears_cached_instance(self):
        """Test that reset_client clears the cached client instance."""
        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._client = Mock()  # Set cached client

        client_manager.reset_client()

        assert client_manager._client is None

    def test_network_code_validation(self):
        """Test network code validation during initialization."""
        client_manager = GAMClientManager(self.config, "")

        with pytest.raises(ValueError, match="Network code is required for GAM client initialization"):
            client_manager._init_client()

    def test_from_existing_client_creates_manager(self):
        """Test creating GAMClientManager from existing AdManagerClient."""
        mock_client = GAMClientMockFactory.create_gam_client()
        mock_client.network_code = "87654321"

        client_manager = GAMClientManager.from_existing_client(mock_client)

        assert client_manager.config == {"existing_client": True}
        assert client_manager.network_code == "87654321"
        assert client_manager.auth_manager is None
        assert client_manager._client == mock_client

    def test_from_existing_client_get_client_returns_existing(self):
        """Test that get_client returns the existing client without re-initialization."""
        mock_client = GAMClientMockFactory.create_gam_client()
        mock_client.network_code = "87654321"

        client_manager = GAMClientManager.from_existing_client(mock_client)

        # Should return existing client without calling _init_client
        client = client_manager.get_client()
        assert client == mock_client

    def test_service_access_with_cached_client(self):
        """Test service access when client is already cached."""
        mock_client = GAMClientMockFactory.create_gam_client()
        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._client = mock_client  # Set cached client

        service = client_manager.get_service("OrderService")

        mock_client.GetService.assert_called_once_with("OrderService", version="v202411")
        assert service == mock_client.GetService.return_value

    def test_statement_builder_access_with_cached_client(self):
        """Test statement builder access when client is already cached."""
        mock_client = GAMClientMockFactory.create_gam_client()
        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._client = mock_client  # Set cached client

        statement_builder = client_manager.get_statement_builder()

        mock_client.GetService.assert_called_once_with("StatementBuilder", version="v202411")
        assert statement_builder == mock_client.GetService.return_value

    def test_connection_test_success(self):
        """Test connection test with successful network service call."""
        mock_client = GAMClientMockFactory.create_gam_client()
        mock_network_service = Mock()
        mock_network_service.getCurrentNetwork.return_value = {"id": "12345678"}
        mock_client.GetService.return_value = mock_network_service

        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._client = mock_client  # Set cached client

        assert client_manager.is_connected() is True

    def test_connection_test_failure(self):
        """Test connection test with failed network service call."""
        mock_client = GAMClientMockFactory.create_gam_client()
        mock_network_service = Mock()
        mock_network_service.getCurrentNetwork.side_effect = Exception("Connection failed")
        mock_client.GetService.return_value = mock_network_service

        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._client = mock_client  # Set cached client

        assert client_manager.is_connected() is False


class TestGAMClientManagerHealthChecking:
    """Health checking functionality tests."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_context = GAMTestSetup.create_standard_context()
        self.config = self.test_context["config"]
        self.network_code = self.test_context["network_code"]

    @patch("src.adapters.gam.client.GAMHealthChecker")
    def test_health_checker_initialization(self, mock_health_checker_class):
        """Test health checker initialization on first access."""
        mock_health_checker = Mock()
        mock_health_checker_class.return_value = mock_health_checker

        client_manager = GAMClientManager(self.config, self.network_code)

        health_checker = client_manager.get_health_checker(dry_run=True)

        mock_health_checker_class.assert_called_once_with(self.config, dry_run=True)
        assert health_checker == mock_health_checker
        assert client_manager._health_checker == mock_health_checker

    def test_health_check_delegation(self):
        """Test that health check methods properly delegate to health checker."""
        mock_health_checker = Mock()
        mock_health_checker.run_all_checks.return_value = (HealthStatus.HEALTHY, [])
        mock_health_checker.get_status_summary.return_value = {"status": "healthy"}

        client_manager = GAMClientManager(self.config, self.network_code)
        client_manager._health_checker = mock_health_checker

        # Test check_health delegation
        result = client_manager.check_health()
        mock_health_checker.run_all_checks.assert_called_once()
        assert result == (HealthStatus.HEALTHY, [])

        # Test get_health_status delegation
        status = client_manager.get_health_status()
        mock_health_checker.get_status_summary.assert_called_once()
        assert status == {"status": "healthy"}


class TestGAMClientManagerErrorHandling:
    """Error handling and edge case tests."""

    def test_empty_config_validation(self):
        """Test that empty config raises appropriate error."""
        with pytest.raises(ValueError, match="GAM config requires either"):
            GAMClientManager({}, "12345678")

    def test_multiple_reset_calls_are_safe(self):
        """Test that multiple reset_client calls are safe."""
        client_manager = GAMClientManager({"refresh_token": "test"}, "12345678")

        # Multiple resets should be safe
        client_manager.reset_client()
        client_manager.reset_client()
        client_manager.reset_client()

        assert client_manager._client is None

    def test_is_connected_handles_client_init_failure(self):
        """Test is_connected gracefully handles client initialization failure."""
        client_manager = GAMClientManager({"refresh_token": "test"}, "12345678")

        with patch.object(client_manager, "get_client") as mock_get_client:
            mock_get_client.side_effect = Exception("Client init failed")

            # Should return False instead of raising exception
            assert client_manager.is_connected() is False

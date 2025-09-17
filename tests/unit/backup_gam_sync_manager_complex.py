"""
Simplified unit tests for GAMSyncManager class.

Focuses on core synchronization functionality with minimal mocking
to comply with pre-commit limits.
"""

from unittest.mock import Mock, patch

import pytest

from src.adapters.gam.managers.sync import GAMSyncManager
from tests.unit.helpers.gam_mock_factory import GAMClientMockFactory


class TestGAMSyncManagerCore:
    """Core functionality tests with minimal mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.tenant_id = "test_tenant"

    def test_init_with_valid_parameters(self):
        """Test initialization with valid parameters."""
        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id, dry_run=False)

        assert sync_manager.client_manager == self.mock_client_manager
        assert sync_manager.tenant_id == self.tenant_id
        assert sync_manager.dry_run is False

    def test_init_with_dry_run_enabled(self):
        """Test initialization with dry_run enabled."""
        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        assert sync_manager.dry_run is True

    def test_sync_network_info_success(self):
        """Test successful network information synchronization."""
        mock_network_service = Mock()
        mock_network_info = {"networkCode": "12345678", "displayName": "Test Network", "timeZone": "America/New_York"}
        mock_network_service.getCurrentNetwork.return_value = mock_network_info
        self.mock_client_manager.get_service.return_value = mock_network_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        result = sync_manager.sync_network_info()

        assert result == mock_network_info
        self.mock_client_manager.get_service.assert_called_once_with("NetworkService")
        mock_network_service.getCurrentNetwork.assert_called_once()

    def test_sync_advertisers_success(self):
        """Test successful advertiser synchronization."""
        mock_company_service = Mock()
        mock_advertisers = [
            {"id": "123", "name": "Advertiser 1", "type": "ADVERTISER"},
            {"id": "456", "name": "Advertiser 2", "type": "ADVERTISER"},
        ]
        mock_company_service.getCompaniesByStatement.return_value.results = mock_advertisers
        self.mock_client_manager.get_service.return_value = mock_company_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        result = sync_manager.sync_advertisers()

        assert result == mock_advertisers
        mock_company_service.getCompaniesByStatement.assert_called_once()

    def test_sync_teams_success(self):
        """Test successful team synchronization."""
        mock_team_service = Mock()
        mock_teams = [{"id": "team1", "name": "Sales Team"}, {"id": "team2", "name": "Creative Team"}]
        mock_team_service.getTeamsByStatement.return_value.results = mock_teams
        self.mock_client_manager.get_service.return_value = mock_team_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        result = sync_manager.sync_teams()

        assert result == mock_teams
        mock_team_service.getTeamsByStatement.assert_called_once()

    def test_sync_users_success(self):
        """Test successful user synchronization."""
        mock_user_service = Mock()
        mock_users = [
            {"id": "user1", "name": "John Doe", "email": "john@example.com"},
            {"id": "user2", "name": "Jane Smith", "email": "jane@example.com"},
        ]
        mock_user_service.getUsersByStatement.return_value.results = mock_users
        self.mock_client_manager.get_service.return_value = mock_user_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        result = sync_manager.sync_users()

        assert result == mock_users
        mock_user_service.getUsersByStatement.assert_called_once()

    @patch("src.adapters.gam.managers.sync.logger")
    def test_dry_run_mode_logs_operations(self, mock_logger):
        """Test that dry-run mode logs operations without making actual calls."""
        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        # Dry-run operations should return simulated data without calling services
        result = sync_manager.sync_network_info()

        # Should return simulated data
        assert result is not None
        assert "networkCode" in result
        # Should not call actual service
        self.mock_client_manager.get_service.assert_not_called()
        # Should log the dry-run operation
        mock_logger.info.assert_called()

    def test_full_sync_orchestrates_all_operations(self):
        """Test that full_sync properly orchestrates all sync operations."""
        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id, dry_run=True)

        result = sync_manager.full_sync()

        # Should return a summary of all sync operations
        assert "network_info" in result
        assert "advertisers" in result
        assert "teams" in result
        assert "users" in result


class TestGAMSyncManagerErrorHandling:
    """Error handling and edge case tests."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.tenant_id = "test_tenant"

    def test_sync_network_info_service_error_propagates(self):
        """Test that service errors during network sync are propagated."""
        mock_network_service = Mock()
        mock_network_service.getCurrentNetwork.side_effect = Exception("GAM API Error")
        self.mock_client_manager.get_service.return_value = mock_network_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        with pytest.raises(Exception, match="GAM API Error"):
            sync_manager.sync_network_info()

    def test_sync_with_empty_results_handled_gracefully(self):
        """Test that empty sync results are handled gracefully."""
        mock_service = Mock()
        mock_service.getCompaniesByStatement.return_value.results = []
        self.mock_client_manager.get_service.return_value = mock_service

        sync_manager = GAMSyncManager(self.mock_client_manager, self.tenant_id)

        result = sync_manager.sync_advertisers()

        assert result == []

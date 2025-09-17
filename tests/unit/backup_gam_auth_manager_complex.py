"""
Simplified unit tests for GAMAuthManager class.

Focuses on core authentication functionality with minimal mocking
to comply with pre-commit limits.
"""

from unittest.mock import Mock, patch

import pytest

from src.adapters.gam.auth import GAMAuthManager


class TestGAMAuthManagerCore:
    """Core functionality tests with minimal mocking."""

    def test_init_with_oauth_config(self):
        """Test initialization with OAuth configuration."""
        config = {
            "refresh_token": "test_refresh_token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }

        auth_manager = GAMAuthManager(config)

        assert auth_manager.config == config
        assert auth_manager._credentials is None

    def test_init_with_service_account_config(self):
        """Test initialization with service account configuration."""
        config = {"key_file": "/path/to/key.json", "scopes": ["https://www.googleapis.com/auth/dfp"]}

        auth_manager = GAMAuthManager(config)

        assert auth_manager.config == config
        assert auth_manager._credentials is None

    def test_get_auth_method_oauth(self):
        """Test authentication method detection for OAuth."""
        config = {"refresh_token": "test_token"}
        auth_manager = GAMAuthManager(config)

        method = auth_manager.get_auth_method()

        assert method == "oauth"

    def test_get_auth_method_service_account(self):
        """Test authentication method detection for service account."""
        config = {"key_file": "/path/to/key.json"}
        auth_manager = GAMAuthManager(config)

        method = auth_manager.get_auth_method()

        assert method == "service_account"

    def test_get_auth_method_environment(self):
        """Test authentication method detection for environment variables."""
        config = {"use_environment": True}
        auth_manager = GAMAuthManager(config)

        method = auth_manager.get_auth_method()

        assert method == "environment"

    @patch("src.adapters.gam.auth.RefreshTokenCredentials")
    def test_get_credentials_oauth_creates_and_caches(self, mock_credentials_class):
        """Test OAuth credentials creation and caching."""
        mock_credentials = Mock()
        mock_credentials_class.return_value = mock_credentials

        config = {"refresh_token": "test_token", "client_id": "test_client_id", "client_secret": "test_secret"}
        auth_manager = GAMAuthManager(config)

        # First call should create credentials
        credentials1 = auth_manager.get_credentials()
        # Second call should return cached credentials
        credentials2 = auth_manager.get_credentials()

        assert credentials1 == mock_credentials
        assert credentials2 == mock_credentials
        # Should only create once
        mock_credentials_class.assert_called_once()

    @patch("src.adapters.gam.auth.ServiceAccountCredentials.from_json_keyfile_name")
    def test_get_credentials_service_account_creates_and_caches(self, mock_from_keyfile):
        """Test service account credentials creation and caching."""
        mock_credentials = Mock()
        mock_from_keyfile.return_value = mock_credentials

        config = {"key_file": "/path/to/key.json", "scopes": ["https://www.googleapis.com/auth/dfp"]}
        auth_manager = GAMAuthManager(config)

        # First call should create credentials
        credentials1 = auth_manager.get_credentials()
        # Second call should return cached credentials
        credentials2 = auth_manager.get_credentials()

        assert credentials1 == mock_credentials
        assert credentials2 == mock_credentials
        # Should only create once
        mock_from_keyfile.assert_called_once()

    def test_reset_credentials_clears_cache(self):
        """Test that reset_credentials clears the cached credentials."""
        config = {"refresh_token": "test_token"}
        auth_manager = GAMAuthManager(config)
        auth_manager._credentials = Mock()  # Set cached credentials

        auth_manager.reset_credentials()

        assert auth_manager._credentials is None

    def test_is_credentials_valid_with_none(self):
        """Test credentials validation when no credentials exist."""
        config = {"refresh_token": "test_token"}
        auth_manager = GAMAuthManager(config)

        is_valid = auth_manager.is_credentials_valid()

        assert is_valid is False

    def test_is_credentials_valid_with_mock_credentials(self):
        """Test credentials validation with mock credentials."""
        config = {"refresh_token": "test_token"}
        auth_manager = GAMAuthManager(config)

        mock_credentials = Mock()
        mock_credentials.access_token = "valid_token"
        mock_credentials.expired = False
        auth_manager._credentials = mock_credentials

        is_valid = auth_manager.is_credentials_valid()

        assert is_valid is True


class TestGAMAuthManagerErrorHandling:
    """Error handling and edge case tests."""

    def test_invalid_config_raises_error(self):
        """Test that invalid configuration raises appropriate error."""
        invalid_config = {}  # Empty config

        with pytest.raises(ValueError, match="GAM config requires either"):
            GAMAuthManager(invalid_config)

    def test_unsupported_auth_method_raises_error(self):
        """Test that unsupported authentication method raises error."""
        config = {"unsupported_field": "value"}
        auth_manager = GAMAuthManager.__new__(GAMAuthManager)  # Bypass __init__
        auth_manager.config = config

        with pytest.raises(ValueError, match="Unsupported authentication method"):
            auth_manager.get_auth_method()

    def test_credentials_creation_failure_propagates(self):
        """Test that credentials creation failures are properly propagated."""
        config = {"refresh_token": "invalid_token"}
        auth_manager = GAMAuthManager(config)

        with patch("src.adapters.gam.auth.RefreshTokenCredentials") as mock_creds:
            mock_creds.side_effect = Exception("Auth failed")

            with pytest.raises(Exception, match="Auth failed"):
                auth_manager.get_credentials()

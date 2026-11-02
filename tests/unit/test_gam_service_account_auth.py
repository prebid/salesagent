"""Unit tests for GAM service account authentication."""

import json
from unittest.mock import Mock, patch

import pytest

from src.adapters.gam import build_gam_config_from_adapter
from src.adapters.gam.auth import GAMAuthManager
from src.core.exceptions import AdCPConfigurationError


def test_gam_auth_manager_accepts_service_account_json():
    """Test that GAMAuthManager accepts service account JSON."""
    service_account_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test-project.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )

    config = {"service_account_json": service_account_json}
    auth_manager = GAMAuthManager(config)

    assert auth_manager.is_service_account_configured()
    assert not auth_manager.is_oauth_configured()
    assert auth_manager.get_auth_method() == "service_account"


def test_gam_auth_manager_accepts_oauth():
    """Test that GAMAuthManager accepts OAuth refresh token."""
    config = {"refresh_token": "test_refresh_token"}
    auth_manager = GAMAuthManager(config)

    assert not auth_manager.is_service_account_configured()
    assert auth_manager.is_oauth_configured()
    assert auth_manager.get_auth_method() == "oauth"


def test_gam_auth_manager_requires_auth_method():
    """Test that GAMAuthManager requires at least one auth method."""
    config = {}
    with pytest.raises(AdCPConfigurationError):
        GAMAuthManager(config)


def test_service_account_json_invalid_format():
    """Test that invalid JSON is rejected."""
    config = {"service_account_json": "not valid json"}
    auth_manager = GAMAuthManager(config)

    with pytest.raises(AdCPConfigurationError):
        auth_manager.get_credentials()


@patch("src.adapters.gam.auth.google.oauth2.service_account.Credentials.from_service_account_info")
def test_service_account_credentials_creation(mock_from_info):
    """Test that service account credentials are created and wrapped correctly."""
    from googleads.oauth2 import GoogleCredentialsClient

    service_account_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test-project.iam.gserviceaccount.com",
        }
    )

    mock_credentials = Mock()
    mock_from_info.return_value = mock_credentials

    config = {"service_account_json": service_account_json}
    auth_manager = GAMAuthManager(config)
    oauth2_client = auth_manager.get_credentials()

    # Verify it returns a GoogleCredentialsClient wrapper
    assert isinstance(oauth2_client, GoogleCredentialsClient)
    mock_from_info.assert_called_once()
    call_args = mock_from_info.call_args
    assert call_args[1]["scopes"] == ["https://www.googleapis.com/auth/dfp"]


def test_build_gam_config_with_service_account():
    """Test build_gam_config_from_adapter with service account auth."""
    adapter_config = Mock()
    adapter_config.gam_network_code = "12345"
    adapter_config.gam_trafficker_id = "67890"
    adapter_config.gam_manual_approval_required = False
    adapter_config.gam_auth_method = "service_account"
    adapter_config.gam_service_account_json = '{"type": "service_account"}'
    adapter_config.gam_refresh_token = None

    config = build_gam_config_from_adapter(adapter_config)

    assert config["network_code"] == "12345"
    assert config["trafficker_id"] == "67890"
    assert config["service_account_json"] == '{"type": "service_account"}'
    assert "refresh_token" not in config


def test_build_gam_config_with_oauth():
    """Test build_gam_config_from_adapter with OAuth."""
    adapter_config = Mock()
    adapter_config.gam_network_code = "12345"
    adapter_config.gam_trafficker_id = "67890"
    adapter_config.gam_manual_approval_required = False
    adapter_config.gam_auth_method = "oauth"
    adapter_config.gam_service_account_json = None
    adapter_config.gam_refresh_token = "test_token"

    config = build_gam_config_from_adapter(adapter_config)

    assert config["network_code"] == "12345"
    assert config["trafficker_id"] == "67890"
    assert config["refresh_token"] == "test_token"
    assert "service_account_json" not in config


@patch("src.adapters.gam.auth.google.oauth2.service_account.Credentials.from_service_account_info")
def test_service_account_returns_compatible_oauth2_client(mock_from_info):
    """Test that service account returns an OAuth2 client compatible with AdManagerClient."""
    from googleads.oauth2 import GoogleOAuth2Client

    service_account_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test-project.iam.gserviceaccount.com",
        }
    )

    mock_credentials = Mock()
    mock_from_info.return_value = mock_credentials

    config = {"service_account_json": service_account_json}
    auth_manager = GAMAuthManager(config)
    oauth2_client = auth_manager.get_credentials()

    # Verify it's a subclass of GoogleOAuth2Client (required by AdManagerClient)
    assert isinstance(oauth2_client, GoogleOAuth2Client)

"""Unit tests for GAM service account authentication."""

import json
from unittest.mock import Mock, patch

import pytest

from src.adapters.gam import build_gam_config_from_adapter
from src.adapters.gam.auth import GAMAuthManager


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
    with pytest.raises(ValueError, match="GAM config requires either"):
        GAMAuthManager(config)


def test_service_account_json_invalid_format():
    """Test that invalid JSON is rejected."""
    config = {"service_account_json": "not valid json"}
    auth_manager = GAMAuthManager(config)

    with pytest.raises(ValueError, match="Invalid service account JSON"):
        auth_manager.get_credentials()


def test_invalid_service_account_json_does_not_leak_payload():
    """The raised ValueError must not contain the failing input.

    json.JSONDecodeError carries the entire failing payload on `e.doc`. A
    naive ``f"{e}"`` interpolation calls ``__str__`` (safe), but any caller
    that logs the exception via ``%r`` or formats the cause chain may
    surface ``e.doc`` — which on a malformed SA key blob would contain
    bytes adjacent to the parse error (potentially including private-key
    material). Suppress the chain with ``from None`` and reconstruct only
    the parser's own diagnostic (msg + position) in the new ValueError.
    """
    sentinel_secret = "PRIVATE_KEY_FRAGMENT_DO_NOT_LEAK"
    payload = '{"type": "service_account", "private_key": "' + sentinel_secret + '"'  # missing closing brace
    config = {"service_account_json": payload}
    auth_manager = GAMAuthManager(config)

    with pytest.raises(ValueError) as exc_info:
        auth_manager.get_credentials()

    rendered = str(exc_info.value)
    assert sentinel_secret not in rendered, "Error message leaked SA key fragment"
    # And the suppressed cause chain ensures repr-based logging can't
    # surface the original exception's `doc` attribute either.
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


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


def _make_adapter_config(*, auth_method, service_account_json, refresh_token):
    """Build a mock AdapterConfig row for build_gam_config_from_adapter tests."""
    adapter_config = Mock()
    adapter_config.gam_network_code = "12345"
    adapter_config.gam_trafficker_id = "67890"
    adapter_config.gam_manual_approval_required = False
    adapter_config.gam_auth_method = auth_method
    adapter_config.gam_service_account_json = service_account_json
    adapter_config.gam_refresh_token = refresh_token
    return adapter_config


def test_build_gam_config_with_service_account():
    """Test build_gam_config_from_adapter with service account auth."""
    config = build_gam_config_from_adapter(
        _make_adapter_config(
            auth_method="service_account",
            service_account_json='{"type": "service_account"}',
            refresh_token=None,
        )
    )

    assert config["network_code"] == "12345"
    assert config["trafficker_id"] == "67890"
    assert config["service_account_json"] == '{"type": "service_account"}'
    assert "refresh_token" not in config


def test_build_gam_config_with_oauth():
    """Test build_gam_config_from_adapter with OAuth."""
    config = build_gam_config_from_adapter(
        _make_adapter_config(auth_method="oauth", service_account_json=None, refresh_token="test_token")
    )

    assert config["network_code"] == "12345"
    assert config["trafficker_id"] == "67890"
    assert config["refresh_token"] == "test_token"
    assert "service_account_json" not in config


def test_build_gam_config_prefers_service_account_when_auth_method_stale():
    """Service-account JSON wins when present, even if gam_auth_method='oauth'.

    Reproduces the embedded-mode provisioning bug: tenants created via
    src/admin/tenant_management_api.py before the fix had
    gam_auth_method='oauth' (the column server_default) but a populated
    service_account_json and no refresh_token. The config builder must
    detect from credential presence so the inventory + custom-targeting
    sync paths don't fall through to GoogleRefreshTokenClient.
    """
    config = build_gam_config_from_adapter(
        _make_adapter_config(
            auth_method="oauth",  # stale — does not match credentials present
            service_account_json='{"type": "service_account"}',
            refresh_token=None,
        )
    )

    assert config["service_account_json"] == '{"type": "service_account"}'
    assert "refresh_token" not in config


def test_build_gam_config_no_credentials_returns_no_auth_keys():
    """When no credentials are present, neither auth key appears in the config.

    Callers must check this explicitly before constructing GAMClientManager,
    which raises if neither is in the config dict.
    """
    config = build_gam_config_from_adapter(
        _make_adapter_config(auth_method="oauth", service_account_json=None, refresh_token=None)
    )

    assert "service_account_json" not in config
    assert "refresh_token" not in config


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

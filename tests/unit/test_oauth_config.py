"""Tests for generic OIDC configuration support.

These tests verify that the OAuth configuration system correctly handles:
- Generic OIDC providers (Okta, Auth0, Azure AD, Keycloak, etc.)
- Google OAuth (backwards compatibility)
- User info extraction from different providers
"""

import os
from unittest.mock import patch


class TestGetOAuthConfig:
    """Tests for get_oauth_config function."""

    def test_generic_oidc_takes_priority(self):
        """Generic OIDC config takes priority over Google config."""
        from src.admin.routers.auth import get_oauth_config

        env = {
            "OAUTH_DISCOVERY_URL": "https://okta.example.com/.well-known/openid-configuration",
            "OAUTH_CLIENT_ID": "okta-client-id",
            "OAUTH_CLIENT_SECRET": "okta-client-secret",
            "GOOGLE_CLIENT_ID": "google-client-id",
            "GOOGLE_CLIENT_SECRET": "google-client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            client_id, client_secret, discovery_url, scopes = get_oauth_config()

        assert client_id == "okta-client-id"
        assert client_secret == "okta-client-secret"
        assert discovery_url == "https://okta.example.com/.well-known/openid-configuration"
        assert scopes == "openid email profile"

    def test_google_oauth_fallback(self):
        """Google OAuth is used when generic OIDC not configured."""
        from src.admin.routers.auth import get_oauth_config

        env = {
            "GOOGLE_CLIENT_ID": "google-client-id",
            "GOOGLE_CLIENT_SECRET": "google-client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            client_id, client_secret, discovery_url, scopes = get_oauth_config()

        assert client_id == "google-client-id"
        assert client_secret == "google-client-secret"
        assert discovery_url == "https://accounts.google.com/.well-known/openid-configuration"
        assert scopes == "openid email profile"

    def test_custom_scopes(self):
        """Custom scopes are respected."""
        from src.admin.routers.auth import get_oauth_config

        env = {
            "OAUTH_DISCOVERY_URL": "https://provider.example.com/.well-known/openid-configuration",
            "OAUTH_CLIENT_ID": "client-id",
            "OAUTH_CLIENT_SECRET": "client-secret",
            "OAUTH_SCOPES": "openid email custom_scope",
        }

        with patch.dict(os.environ, env, clear=True):
            _, _, _, scopes = get_oauth_config()

        assert scopes == "openid email custom_scope"

    def test_named_provider_google(self):
        """Named provider 'google' uses Google discovery URL."""
        from src.admin.routers.auth import get_oauth_config

        env = {
            "OAUTH_PROVIDER": "google",
            "OAUTH_CLIENT_ID": "generic-client-id",
            "OAUTH_CLIENT_SECRET": "generic-client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            client_id, client_secret, discovery_url, _ = get_oauth_config()

        assert client_id == "generic-client-id"
        assert discovery_url == "https://accounts.google.com/.well-known/openid-configuration"

    def test_named_provider_microsoft(self):
        """Named provider 'microsoft' uses Microsoft discovery URL."""
        from src.admin.routers.auth import get_oauth_config

        env = {
            "OAUTH_PROVIDER": "microsoft",
            "OAUTH_CLIENT_ID": "ms-client-id",
            "OAUTH_CLIENT_SECRET": "ms-client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            _, _, discovery_url, _ = get_oauth_config()

        assert "login.microsoftonline.com" in discovery_url

    def test_no_config_returns_none(self):
        """Returns None when no OAuth is configured."""
        from src.admin.routers.auth import get_oauth_config

        with patch.dict(os.environ, {}, clear=True):
            result = get_oauth_config()

        assert result == (None, None, None, None)

    def test_partial_generic_config_falls_through(self):
        """Partial generic config falls through to Google."""
        from src.admin.routers.auth import get_oauth_config

        # Only discovery URL, no credentials
        env = {
            "OAUTH_DISCOVERY_URL": "https://provider.example.com/.well-known/openid-configuration",
            "GOOGLE_CLIENT_ID": "google-client-id",
            "GOOGLE_CLIENT_SECRET": "google-client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            client_id, _, discovery_url, _ = get_oauth_config()

        # Should fall through to Google
        assert client_id == "google-client-id"
        assert discovery_url == "https://accounts.google.com/.well-known/openid-configuration"


class TestExtractUserInfo:
    """Tests for extract_user_info function."""

    def test_google_format(self):
        """Extract user info from Google token format."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "user@gmail.com",
                "name": "Test User",
                "picture": "https://lh3.googleusercontent.com/photo.jpg",
            }
        }

        result = extract_user_info(token)

        assert result["email"] == "user@gmail.com"
        assert result["name"] == "Test User"
        assert result["picture"] == "https://lh3.googleusercontent.com/photo.jpg"

    def test_microsoft_format_with_preferred_username(self):
        """Extract user info from Microsoft token with preferred_username."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "preferred_username": "user@company.onmicrosoft.com",
                "name": "Test User",
                "picture": "https://graph.microsoft.com/photo",
            }
        }

        result = extract_user_info(token)

        assert result["email"] == "user@company.onmicrosoft.com"
        assert result["name"] == "Test User"

    def test_microsoft_format_with_upn(self):
        """Extract user info from Microsoft token with UPN."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "upn": "user@company.com",
                "name": "Test User",
            }
        }

        result = extract_user_info(token)

        assert result["email"] == "user@company.com"

    def test_email_normalized_to_lowercase(self):
        """Email is always normalized to lowercase."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "User@EXAMPLE.COM",
                "name": "Test User",
            }
        }

        result = extract_user_info(token)

        assert result["email"] == "user@example.com"

    def test_name_from_given_and_family(self):
        """Name constructed from given_name and family_name."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "user@example.com",
                "given_name": "John",
                "family_name": "Doe",
            }
        }

        result = extract_user_info(token)

        assert result["name"] == "John Doe"

    def test_name_fallback_to_email_prefix(self):
        """Name falls back to email prefix when not provided."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "john.doe@example.com",
            }
        }

        result = extract_user_info(token)

        assert result["name"] == "john.doe"

    def test_picture_fallback_to_empty_string(self):
        """Picture falls back to empty string when not provided."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "user@example.com",
                "name": "Test User",
            }
        }

        result = extract_user_info(token)

        assert result["picture"] == ""

    def test_avatar_url_as_picture(self):
        """avatar_url is used as picture (GitHub style)."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "email": "user@github.com",
                "name": "GitHub User",
                "avatar_url": "https://avatars.githubusercontent.com/u/12345",
            }
        }

        result = extract_user_info(token)

        assert result["picture"] == "https://avatars.githubusercontent.com/u/12345"

    def test_id_token_fallback(self):
        """User info extracted from ID token when userinfo not present."""
        import jwt

        from src.admin.routers.auth import extract_user_info

        # Create a simple JWT (unsigned for testing)
        id_token_payload = {
            "email": "user@example.com",
            "name": "ID Token User",
            "picture": "https://example.com/photo.jpg",
        }
        # Create unsigned token for testing
        id_token = jwt.encode(id_token_payload, key="", algorithm="HS256")

        token = {"id_token": id_token}

        result = extract_user_info(token)

        assert result["email"] == "user@example.com"
        assert result["name"] == "ID Token User"

    def test_no_user_info_returns_none(self):
        """Returns None when no user info available."""
        from src.admin.routers.auth import extract_user_info

        token = {}

        result = extract_user_info(token)

        assert result is None

    def test_no_email_returns_none(self):
        """Returns None when no email claim found."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "name": "No Email User",
            }
        }

        result = extract_user_info(token)

        assert result is None

    def test_sub_as_email_fallback(self):
        """Subject claim used as email fallback."""
        from src.admin.routers.auth import extract_user_info

        token = {
            "userinfo": {
                "sub": "user@example.com",
                "name": "Subject User",
            }
        }

        result = extract_user_info(token)

        assert result["email"] == "user@example.com"


class TestOAuthProviderName:
    """Tests for get_oauth_provider_name function."""

    def test_default_is_google(self):
        """Default provider is google when not set."""
        from src.admin.routers.auth import get_oauth_provider_name

        with patch.dict(os.environ, {}, clear=True):
            provider = get_oauth_provider_name()

        assert provider == "google"

    def test_provider_from_env(self):
        """Provider read from environment variable."""
        from src.admin.routers.auth import get_oauth_provider_name

        with patch.dict(os.environ, {"OAUTH_PROVIDER": "Okta"}, clear=True):
            provider = get_oauth_provider_name()

        assert provider == "okta"  # Normalized to lowercase


class TestInitOAuth:
    """Tests for init_oauth function."""

    def test_init_with_generic_oidc(self):
        """OAuth initialized with generic OIDC config."""
        from unittest.mock import MagicMock

        from src.admin.routers.auth import init_oauth

        env = {
            "OAUTH_DISCOVERY_URL": "https://provider.example.com/.well-known/openid-configuration",
            "OAUTH_CLIENT_ID": "client-id",
            "OAUTH_CLIENT_SECRET": "client-secret",
        }

        app = MagicMock()
        app.config = {}

        with patch.dict(os.environ, env, clear=True):
            result = init_oauth(app)

        assert result is not None
        assert hasattr(app, "oauth")
        assert hasattr(app, "oauth_provider")

    def test_init_without_config_returns_none(self):
        """OAuth returns None when not configured."""
        from unittest.mock import MagicMock

        from src.admin.routers.auth import init_oauth

        app = MagicMock()
        app.config = {}

        with patch.dict(os.environ, {}, clear=True):
            result = init_oauth(app)

        assert result is None

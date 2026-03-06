"""Tests for admin API authentication dependencies."""

from unittest.mock import MagicMock, patch

import pytest

from src.core.admin_auth import require_platform_api_key, require_tenant_admin
from src.core.exceptions import AdCPAuthenticationError, AdCPAuthorizationError, AdCPNotFoundError


class TestRequirePlatformApiKey:
    """Tests for the platform API key auth dependency."""

    def _make_request(self, api_key: str | None = None) -> MagicMock:
        request = MagicMock()
        headers = {}
        if api_key:
            headers["x-tenant-management-api-key"] = api_key
        request.headers = headers
        return request

    def test_missing_header_raises_401(self):
        request = self._make_request()
        with pytest.raises(AdCPAuthenticationError, match="Missing"):
            require_platform_api_key(request)

    @patch("src.core.admin_auth.get_db_session")
    def test_invalid_key_raises_401(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.first.return_value = None

        request = self._make_request(api_key="bad-key")
        with pytest.raises(AdCPAuthenticationError, match="Invalid"):
            require_platform_api_key(request)

    @patch("src.core.admin_auth.get_db_session")
    def test_valid_key_returns_key(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        config = MagicMock()
        config.config_value = "sk-valid-key"
        mock_session.scalars.return_value.first.return_value = config

        request = self._make_request(api_key="sk-valid-key")
        result = require_platform_api_key(request)
        assert result == "sk-valid-key"


class TestRequireTenantAdmin:
    """Tests for the tenant admin token auth dependency."""

    def _make_request(self, token: str | None = None) -> MagicMock:
        request = MagicMock()
        headers = {}
        if token:
            headers["authorization"] = f"Bearer {token}"
        request.headers = headers
        return request

    def test_missing_auth_header_raises_401(self):
        request = MagicMock()
        request.headers = {}
        with pytest.raises(AdCPAuthenticationError, match="Missing"):
            require_tenant_admin(request, "tenant_123")

    def test_non_bearer_header_raises_401(self):
        request = MagicMock()
        request.headers = {"authorization": "Basic dXNlcjpwYXNz"}
        with pytest.raises(AdCPAuthenticationError, match="Missing"):
            require_tenant_admin(request, "tenant_123")

    def test_empty_bearer_token_raises_401(self):
        request = MagicMock()
        request.headers = {"authorization": "Bearer "}
        with pytest.raises(AdCPAuthenticationError, match="Empty"):
            require_tenant_admin(request, "tenant_123")

    @patch("src.core.admin_auth.get_db_session")
    def test_tenant_not_found_raises_404(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.first.return_value = None

        request = self._make_request(token="some-token")
        with pytest.raises(AdCPNotFoundError, match="not found"):
            require_tenant_admin(request, "nonexistent")

    @patch("src.core.admin_auth.get_db_session")
    def test_wrong_token_raises_403(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        tenant = MagicMock()
        tenant.admin_token = "correct-token"
        mock_session.scalars.return_value.first.return_value = tenant

        request = self._make_request(token="wrong-token")
        with pytest.raises(AdCPAuthorizationError, match="Invalid admin token"):
            require_tenant_admin(request, "tenant_123")

    @patch("src.core.admin_auth.get_db_session")
    def test_valid_token_returns_tenant(self, mock_db):
        mock_session = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        tenant = MagicMock()
        tenant.admin_token = "valid-token"
        tenant.tenant_id = "tenant_123"
        mock_session.scalars.return_value.first.return_value = tenant

        request = self._make_request(token="valid-token")
        result = require_tenant_admin(request, "tenant_123")
        assert result.tenant_id == "tenant_123"

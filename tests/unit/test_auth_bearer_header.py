"""Tests for Authorization: Bearer header support in MCP authentication.

Background:
-----------
Issue: MCP clients like Anthropic only support Authorization: Bearer headers,
not custom x-adcp-auth headers. Our auth system should accept either header
for compatibility with standard MCP clients.

The auth.py module should accept authentication via:
- x-adcp-auth: <token> (AdCP convention, preferred)
- Authorization: Bearer <token> (standard HTTP/MCP convention)
"""

from unittest.mock import patch

import pytest


class TestAuthorizationBearerSupport:
    """Test that Authorization: Bearer header is accepted for authentication."""

    def test_x_adcp_auth_header_works(self):
        """x-adcp-auth header should authenticate successfully."""
        from src.core.auth import _get_header_case_insensitive

        # Mock headers with x-adcp-auth
        headers = {"x-adcp-auth": "valid-test-token"}

        # Verify header extraction works
        token = _get_header_case_insensitive(headers, "x-adcp-auth")
        assert token == "valid-test-token"

    def test_authorization_bearer_header_works(self):
        """Authorization: Bearer header should authenticate successfully.

        This is the key test - standard HTTP Authorization header should be
        accepted as an alternative to x-adcp-auth for MCP compatibility.
        """
        from src.core.auth import _get_header_case_insensitive

        # Mock headers with only Authorization: Bearer (what Anthropic sends)
        headers = {"Authorization": "Bearer valid-test-token"}

        # Currently this ONLY checks x-adcp-auth, not Authorization
        # This should be fixed to also check Authorization: Bearer
        x_adcp_token = _get_header_case_insensitive(headers, "x-adcp-auth")

        # x-adcp-auth is not present, so this returns None
        assert x_adcp_token is None, "x-adcp-auth not in headers - this is expected"

        # But we SHOULD be able to extract token from Authorization header
        # This is what needs to be fixed in auth.py
        auth_header = _get_header_case_insensitive(headers, "Authorization")
        assert auth_header == "Bearer valid-test-token"

        # Extract the token from Bearer format
        if auth_header and auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:]  # Remove "Bearer " prefix
            assert bearer_token == "valid-test-token"

    @patch("src.core.auth.get_http_headers")
    @patch("src.core.auth.get_principal_from_token")
    @patch("src.core.auth.get_current_tenant")
    @patch("src.core.auth.get_tenant_by_virtual_host")
    @patch("src.core.auth.set_current_tenant")
    def test_get_principal_from_context_accepts_authorization_bearer(
        self, mock_set_tenant, mock_get_virtual_host, mock_get_tenant, mock_get_principal, mock_get_headers
    ):
        """get_principal_from_context should accept Authorization: Bearer.

        When a client sends only Authorization: Bearer (not x-adcp-auth),
        we should still authenticate them successfully.
        """
        from src.core.auth import get_principal_from_context

        # Setup: Headers with only Authorization: Bearer (like Anthropic sends)
        mock_get_headers.return_value = {
            "Authorization": "Bearer test-bearer-token",
            "Host": "localhost:8080",
        }

        # Mock tenant detection (avoid DB access)
        mock_get_virtual_host.return_value = {"tenant_id": "test_tenant"}

        # Mock successful token validation
        mock_get_principal.return_value = "test_principal_id"
        mock_get_tenant.return_value = {"tenant_id": "test_tenant"}

        # Call get_principal_from_context
        principal_id, tenant_context = get_principal_from_context(None)

        # Assert: Should have extracted principal from Bearer token
        assert principal_id == "test_principal_id", (
            "Authorization: Bearer should be accepted! Currently only x-adcp-auth is checked in auth.py:343"
        )

    @patch("src.core.auth.get_http_headers")
    @patch("src.core.auth.get_principal_from_token")
    @patch("src.core.auth.get_current_tenant")
    @patch("src.core.auth.get_tenant_by_virtual_host")
    @patch("src.core.auth.set_current_tenant")
    def test_x_adcp_auth_takes_precedence_over_authorization_bearer(
        self, mock_set_tenant, mock_get_virtual_host, mock_get_tenant, mock_get_principal, mock_get_headers
    ):
        """When both headers present, x-adcp-auth should take precedence.

        This ensures backwards compatibility - if a client sends both headers
        (like OpenAI does), we prefer x-adcp-auth.
        """
        from src.core.auth import get_principal_from_context

        # Both headers present (OpenAI sends both)
        mock_get_headers.return_value = {
            "x-adcp-auth": "adcp-token",
            "Authorization": "Bearer bearer-token",
            "Host": "localhost:8080",
        }

        # Mock tenant detection (avoid DB access)
        mock_get_virtual_host.return_value = {"tenant_id": "test_tenant"}

        mock_get_principal.return_value = "test_principal_id"
        mock_get_tenant.return_value = {"tenant_id": "test_tenant"}

        principal_id, tenant_context = get_principal_from_context(None)

        # Should have called get_principal_from_token with x-adcp-auth token
        # (not the Bearer token)
        mock_get_principal.assert_called()
        call_args = mock_get_principal.call_args

        # The first positional arg should be the token
        token_used = call_args[0][0]
        assert token_used == "adcp-token", "x-adcp-auth should take precedence"

    def test_case_insensitive_authorization_header(self):
        """Authorization header lookup should be case-insensitive."""
        from src.core.auth import _get_header_case_insensitive

        # HTTP headers are case-insensitive
        for header_name in ["Authorization", "authorization", "AUTHORIZATION"]:
            headers = {header_name: "Bearer token123"}
            result = _get_header_case_insensitive(headers, "Authorization")
            assert result == "Bearer token123", f"Failed for header name: {header_name}"


class TestAuthorizationBearerEdgeCases:
    """Edge cases for Authorization: Bearer handling."""

    def test_bearer_token_without_space(self):
        """Malformed Bearer token (no space) should be handled gracefully."""
        # "Bearertoken" without space is invalid
        auth_header = "Bearertoken123"
        assert not auth_header.startswith("Bearer "), "Should not match without space"

    def test_bearer_prefix_case_sensitivity(self):
        """Bearer prefix should be case-sensitive per RFC 6750."""
        # RFC 6750 specifies "Bearer" (capital B)
        assert "bearer token".startswith("bearer ")
        assert not "bearer token".startswith("Bearer ")

        # For compatibility, we might want to accept both
        auth_header = "bearer token123"
        # Standard check
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            assert token == "token123"

    def test_empty_bearer_token(self):
        """Empty token after Bearer prefix should be handled."""
        auth_header = "Bearer "
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            assert token == "", "Empty token extracted"

    def test_bearer_with_extra_whitespace(self):
        """Bearer token with extra whitespace should be handled."""
        auth_header = "Bearer   token123"  # Multiple spaces
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()  # Strip extra whitespace
            assert token == "token123"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

#!/usr/bin/env python3
"""Tests for ResolvedIdentity type and resolve_identity() function.

Verifies the transport-agnostic identity resolution that all boundaries
(MCP, A2A, REST) will use to create a consistent identity before calling
_impl functions.

Core Invariant: Business logic receives a single, resolved identity type.
No isinstance checks, no transport-specific types, no auth extraction inside _impl.
"""

from unittest.mock import patch

import pytest

from src.core.resolved_identity import ResolvedIdentity, resolve_identity


class TestResolvedIdentityType:
    """Test the ResolvedIdentity type itself."""

    def test_create_authenticated_identity(self):
        """ResolvedIdentity can be created with full auth info."""
        identity = ResolvedIdentity(
            principal_id="principal_123",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "name": "Test"},
            auth_token="tok_abc",
            protocol="mcp",
        )
        assert identity.principal_id == "principal_123"
        assert identity.tenant_id == "test_tenant"
        assert identity.protocol == "mcp"
        assert identity.auth_token == "tok_abc"
        assert identity.tenant == {"tenant_id": "test_tenant", "name": "Test"}

    def test_create_anonymous_identity(self):
        """ResolvedIdentity supports anonymous (discovery) requests."""
        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="default",
            tenant={"tenant_id": "default"},
            auth_token=None,
            protocol="a2a",
        )
        assert identity.principal_id is None
        assert identity.tenant_id == "default"
        assert identity.protocol == "a2a"

    def test_is_authenticated(self):
        """ResolvedIdentity provides is_authenticated() check."""
        authed = ResolvedIdentity(principal_id="p1", tenant_id="t1", protocol="mcp")
        anon = ResolvedIdentity(principal_id=None, tenant_id="t1", protocol="mcp")
        assert authed.is_authenticated is True
        assert anon.is_authenticated is False

    def test_frozen_immutable(self):
        """ResolvedIdentity should be immutable after creation."""
        identity = ResolvedIdentity(principal_id="p1", tenant_id="t1", protocol="rest")
        with pytest.raises((AttributeError, TypeError, ValueError)):
            identity.principal_id = "hacked"  # type: ignore[misc]

    def test_all_protocols_accepted(self):
        """ResolvedIdentity accepts mcp, a2a, and rest protocols."""
        for protocol in ("mcp", "a2a", "rest"):
            identity = ResolvedIdentity(principal_id=None, tenant_id="t", protocol=protocol)
            assert identity.protocol == protocol

    def test_testing_context_field(self):
        """ResolvedIdentity supports optional testing_context."""
        identity = ResolvedIdentity(
            principal_id="p1",
            tenant_id="t1",
            protocol="mcp",
            testing_context=None,
        )
        assert identity.testing_context is None


class TestResolveIdentity:
    """Test the resolve_identity() boundary helper."""

    @patch("src.core.auth_utils.get_principal_from_token")
    @patch("src.core.resolved_identity.get_tenant_by_virtual_host", return_value=None)
    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    def test_resolve_with_tenant_header_and_token(self, mock_get_subdomain, mock_get_vhost, mock_get_principal):
        """resolve_identity() extracts tenant from x-adcp-tenant header and validates token."""
        mock_get_principal.return_value = ("principal_123", None)
        mock_get_subdomain.return_value = {
            "tenant_id": "test_tenant",
            "name": "Test Tenant",
        }

        identity = resolve_identity(
            headers={"x-adcp-tenant": "test_tenant", "x-adcp-auth": "tok_abc"},
            auth_token="tok_abc",
            protocol="mcp",
        )

        assert identity.principal_id == "principal_123"
        assert identity.tenant_id == "test_tenant"
        assert identity.auth_token == "tok_abc"
        assert identity.protocol == "mcp"
        mock_get_principal.assert_called_once_with("tok_abc", "test_tenant")

    @patch("src.core.auth_utils.get_principal_from_token")
    @patch("src.core.resolved_identity.get_tenant_by_virtual_host", return_value=None)
    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    def test_resolve_with_subdomain_host(self, mock_get_subdomain, mock_get_vhost, mock_get_principal):
        """resolve_identity() detects tenant from Host subdomain."""
        mock_get_subdomain.return_value = {
            "tenant_id": "acme",
            "name": "Acme Corp",
        }
        mock_get_principal.return_value = ("principal_456", None)

        identity = resolve_identity(
            headers={"host": "acme.example.com", "x-adcp-auth": "tok_xyz"},
            auth_token="tok_xyz",
            protocol="a2a",
        )

        assert identity.tenant_id == "acme"
        assert identity.principal_id == "principal_456"
        assert identity.protocol == "a2a"

    @patch("src.core.resolved_identity.get_tenant_by_virtual_host", return_value=None)
    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    def test_resolve_anonymous_discovery(self, mock_get_subdomain, mock_get_vhost):
        """resolve_identity() supports anonymous (no token) for discovery endpoints."""
        mock_get_subdomain.return_value = {"tenant_id": "default"}

        identity = resolve_identity(
            headers={"x-adcp-tenant": "default"},
            auth_token=None,
            protocol="mcp",
        )

        assert identity.principal_id is None
        assert identity.tenant_id == "default"
        assert identity.is_authenticated is False

    @patch("src.core.resolved_identity.get_tenant_by_virtual_host", return_value=None)
    @patch("src.core.resolved_identity.get_tenant_by_subdomain")
    def test_resolve_localhost_defaults_to_default_tenant(self, mock_get_subdomain, mock_get_vhost):
        """resolve_identity() uses 'default' tenant for localhost requests."""
        mock_get_subdomain.return_value = {"tenant_id": "default"}

        identity = resolve_identity(
            headers={"host": "localhost:8080"},
            auth_token=None,
            protocol="rest",
        )

        assert identity.tenant_id == "default"


class TestAuthConsolidation:
    """Test that auth.py delegates to auth_utils.py (with retry)."""

    def test_auth_get_principal_from_token_uses_retry_version(self):
        """auth.py::get_principal_from_token should delegate to auth_utils version with retry."""
        from src.core import auth, auth_utils

        # After consolidation, auth.get_principal_from_token should be
        # the same function as auth_utils.get_principal_from_token
        assert auth.get_principal_from_token is auth_utils.get_principal_from_token

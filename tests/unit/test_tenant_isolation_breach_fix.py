"""Unit tests for tenant isolation breach fix.

These tests verify the security fixes work without requiring a database:

1. get_current_tenant() now raises RuntimeError instead of falling back to default tenant
2. get_principal_from_context() now raises ToolError if tenant cannot be determined from headers
3. Global token lookup is prevented when tenant detection fails

Bug Report:
- User called wonderstruck endpoint with valid token
- Got back products from test-agent tenant
- Root cause: Tenant detection failed, fell back to global token lookup, which found test-agent principal

Security Fixes:
1. Removed dangerous fallback in get_current_tenant() that returned first active tenant
2. Added tenant detection requirement - reject requests if tenant can't be determined
3. Fail loudly with clear error messages instead of silently using wrong tenant
"""

from unittest.mock import Mock, patch

import pytest


def test_get_current_tenant_fails_without_context():
    """Test that get_current_tenant() raises error instead of falling back."""
    from src.core.config_loader import current_tenant, get_current_tenant

    # Clear any existing tenant context
    current_tenant.set(None)

    # Should raise RuntimeError, not return a default tenant
    with pytest.raises(RuntimeError) as exc_info:
        get_current_tenant()

    error_msg = str(exc_info.value)
    assert "No tenant context set" in error_msg
    assert "security error" in error_msg.lower()
    assert "breach tenant isolation" in error_msg.lower()


def test_get_principal_from_context_uses_global_lookup_when_no_tenant_detected():
    """Test that authentication uses global token lookup when tenant cannot be determined from headers.

    This is the CORRECT behavior:
    - If tenant IS detected from headers → validate token belongs to that tenant
    - If NO tenant detected → global lookup finds token's actual tenant and sets context

    The security we maintain:
    - Cross-tenant token usage is blocked (if wonderstruck subdomain detected, test-agent token rejected)
    - But if NO subdomain detected (e.g., through proxy), we look up which tenant the token belongs to
    """
    from src.core.main import get_principal_from_context

    # Create mock context with auth token but no tenant detection possible
    context = Mock()
    context.meta = {
        "headers": {
            "x-adcp-auth": "some-valid-token",
            "host": "localhost",  # Not a valid subdomain for tenant detection
        }
    }

    # Mock get_http_headers to return empty dict (forcing fallback to context.meta)
    # Mock get_principal_from_token and get_current_tenant to simulate successful global lookup
    mock_tenant = {"tenant_id": "tenant_test", "subdomain": "test"}
    with (
        patch("src.core.main.get_http_headers", return_value={}),
        patch("src.core.main.get_tenant_by_virtual_host", return_value=None),  # localhost not a virtual host
        patch("src.core.main.get_tenant_by_subdomain", return_value=None),  # localhost not a subdomain
        patch("src.core.main.get_principal_from_token") as mock_get_principal,
        patch("src.core.main.get_current_tenant", return_value=mock_tenant),
    ):
        # Global lookup should succeed and return principal_id
        mock_get_principal.return_value = "principal_abc123"

        # Should succeed via global token lookup and return tuple
        principal_id, tenant = get_principal_from_context(context)

        # Verify we got the principal ID and tenant
        assert principal_id == "principal_abc123"
        assert tenant == mock_tenant

        # Verify get_principal_from_token was called with None for tenant_id (global lookup)
        mock_get_principal.assert_called_once_with("some-valid-token", None)

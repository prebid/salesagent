"""Test tenant isolation fix for get_products.

This test verifies that when accessing a tenant via subdomain (e.g., wonderstruck.sales-agent.scope3.com),
the products returned belong to that tenant, not the tenant associated with the auth token.

Bug: Previously, get_principal_from_token() would overwrite the tenant context set from the subdomain
with the tenant associated with the principal's token, causing products from the wrong tenant to be returned.

Fix: get_principal_from_token() now only sets tenant context when doing global token lookup (no tenant_id specified).
When tenant_id is provided (from subdomain), it preserves the existing tenant context.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.main import get_principal_from_context


@pytest.mark.requires_db
def test_tenant_isolation_with_subdomain_and_cross_tenant_token(integration_db):
    """Test that cross-tenant tokens are rejected for security.

    When accessing a tenant via subdomain (e.g., wonderstruck.sales-agent.scope3.com),
    tokens from a different tenant should be rejected, not accepted with overridden context.
    This prevents principals from one tenant accessing another tenant's resources.
    """

    from fastmcp.exceptions import ToolError

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal as ModelPrincipal
    from src.core.database.models import Tenant

    # Create two tenants
    with get_db_session() as session:
        # Tenant 1: Wonderstruck (accessed via subdomain)
        wonderstruck = Tenant(
            tenant_id="tenant_wonderstruck",
            name="Wonderstruck",
            subdomain="wonderstruck",
            ad_server="mock",
            admin_token="wonderstruck_admin_token",
            is_active=True,
        )
        session.add(wonderstruck)

        # Tenant 2: Test Agent (principal's token belongs to this tenant)
        test_agent = Tenant(
            tenant_id="tenant_test_agent",
            name="Test Agent",
            subdomain="test-agent",
            ad_server="mock",
            admin_token="test_agent_admin_token",
            is_active=True,
        )
        session.add(test_agent)

        # Create a principal in test-agent tenant
        principal = ModelPrincipal(
            principal_id="principal_test_agent",
            tenant_id="tenant_test_agent",
            name="Test Agent Principal",
            access_token="test_agent_principal_token",
            platform_mappings={"mock": {"id": "principal_test_agent"}},
        )
        session.add(principal)
        session.commit()

    # Simulate request to wonderstruck.sales-agent.scope3.com with test-agent token
    # This should be REJECTED for security reasons
    mock_context = MagicMock()
    mock_context.meta = {
        "headers": {
            "host": "wonderstruck.sales-agent.scope3.com",
            "x-adcp-auth": "test_agent_principal_token",
        }
    }

    # Mock get_http_headers to return the headers
    with patch("src.core.auth.get_http_headers") as mock_get_headers:
        mock_get_headers.return_value = mock_context.meta["headers"]

        # Verify cross-tenant token is REJECTED
        with pytest.raises(ToolError) as exc_info:
            get_principal_from_context(mock_context)

        # Verify the error message mentions the tenant
        assert "INVALID_AUTH_TOKEN" in str(exc_info.value)
        assert "tenant_wonderstruck" in str(exc_info.value)


@pytest.mark.requires_db
def test_global_token_lookup_sets_tenant_from_principal(integration_db):
    """Test that global token lookup (no subdomain) correctly sets tenant context from principal."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal as ModelPrincipal
    from src.core.database.models import Tenant

    # Create tenant and principal
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="tenant_global",
            name="Global Tenant",
            subdomain="global",
            ad_server="mock",
            admin_token="global_admin_token",
            is_active=True,
        )
        session.add(tenant)

        principal = ModelPrincipal(
            principal_id="principal_global",
            tenant_id="tenant_global",
            name="Global Principal",
            access_token="global_principal_token",
            platform_mappings={"mock": {"id": "principal_global"}},
        )
        session.add(principal)
        session.commit()

    # Simulate request without subdomain (e.g., direct API call)
    mock_context = MagicMock()
    mock_context.meta = {
        "headers": {
            "x-adcp-auth": "global_principal_token",
        }
    }

    # Clear any existing tenant context
    set_current_tenant(None)

    with patch("src.core.auth.get_http_headers") as mock_get_headers:
        mock_get_headers.return_value = mock_context.meta["headers"]

        # Call get_principal_from_context
        principal_id, tenant_ctx = get_principal_from_context(mock_context)

        # Verify principal was found
        assert principal_id == "principal_global"

        # Verify tenant context was set from principal's tenant
        current_tenant = get_current_tenant()
        assert current_tenant is not None
        assert current_tenant["tenant_id"] == "tenant_global"
        assert current_tenant["subdomain"] == "global"


@pytest.mark.requires_db
def test_admin_token_with_subdomain_preserves_tenant_context(integration_db):
    """Test that admin token with subdomain preserves the subdomain tenant context."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    # Create tenant
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="tenant_admin_test",
            name="Admin Test Tenant",
            subdomain="admin-test",
            ad_server="mock",
            admin_token="admin_test_admin_token",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

    # Simulate request to admin-test.sales-agent.scope3.com with admin token
    mock_context = MagicMock()
    mock_context.meta = {
        "headers": {
            "host": "admin-test.sales-agent.scope3.com",
            "x-adcp-auth": "admin_test_admin_token",
        }
    }

    with patch("src.core.auth.get_http_headers") as mock_get_headers:
        mock_get_headers.return_value = mock_context.meta["headers"]

        # Call get_principal_from_context
        principal_id, tenant_ctx = get_principal_from_context(mock_context)

        # Verify admin token was recognized
        assert principal_id == "tenant_admin_test_admin"

        # Verify tenant context is correct
        current_tenant = get_current_tenant()
        assert current_tenant is not None
        assert current_tenant["tenant_id"] == "tenant_admin_test"
        assert current_tenant["subdomain"] == "admin-test"

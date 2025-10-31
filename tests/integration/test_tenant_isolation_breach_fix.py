"""Integration tests for tenant isolation breach fix.

These tests verify tenant isolation works correctly with a real database.
"""

from unittest.mock import Mock, patch

import pytest
from fastmcp.exceptions import ToolError


@pytest.mark.requires_db
def test_tenant_isolation_with_valid_subdomain(integration_db):
    """Test that tenant detection works correctly with valid subdomain."""
    from src.core.config_loader import current_tenant, get_current_tenant
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal, Tenant
    from src.core.main import get_principal_from_context

    # Clear tenant context
    current_tenant.set(None)

    # Create two tenants in database
    with get_db_session() as session:
        tenant1 = Tenant(
            tenant_id="tenant_wonderstruck",
            name="Wonderstruck",
            subdomain="wonderstruck",
            is_active=True,
        )
        tenant2 = Tenant(
            tenant_id="tenant_test_agent",
            name="Test Agent",
            subdomain="test-agent",
            is_active=True,
        )
        session.add_all([tenant1, tenant2])
        session.flush()

        # Create principals for each tenant
        principal1 = Principal(
            principal_id="adv_wonderstruck",
            tenant_id="tenant_wonderstruck",
            name="Wonderstruck Advertiser",
            access_token="wonderstruck_token_abc123",
            platform_mappings={"mock": {"id": "adv_wonderstruck"}},
        )
        principal2 = Principal(
            principal_id="adv_test_agent",
            tenant_id="tenant_test_agent",
            name="Test Agent Advertiser",
            access_token="test_agent_token_xyz789",
            platform_mappings={"mock": {"id": "adv_test_agent"}},
        )
        session.add_all([principal1, principal2])
        session.commit()

    # Test 1: Request with wonderstruck subdomain should set wonderstruck tenant
    context = Mock()
    context.meta = {
        "headers": {
            "x-adcp-auth": "wonderstruck_token_abc123",
            "host": "wonderstruck.sales-agent.scope3.com",
        }
    }

    with patch("src.core.auth.get_http_headers", return_value={}):
        principal_id, tenant = get_principal_from_context(context)

    assert principal_id == "adv_wonderstruck"

    # Verify tenant context was set correctly
    tenant = get_current_tenant()
    assert tenant["tenant_id"] == "tenant_wonderstruck"
    assert tenant["subdomain"] == "wonderstruck"

    # Clear context for next test
    current_tenant.set(None)

    # Test 2: Request with test-agent subdomain should set test-agent tenant
    context2 = Mock()
    context2.meta = {
        "headers": {
            "x-adcp-auth": "test_agent_token_xyz789",
            "host": "test-agent.sales-agent.scope3.com",
        }
    }

    with patch("src.core.auth.get_http_headers", return_value={}):
        principal_id2, tenant2 = get_principal_from_context(context2)

    assert principal_id2 == "adv_test_agent"

    # Verify tenant context was set correctly
    tenant2 = get_current_tenant()
    assert tenant2["tenant_id"] == "tenant_test_agent"
    assert tenant2["subdomain"] == "test-agent"


@pytest.mark.requires_db
def test_cross_tenant_token_rejected(integration_db):
    """Test that using tenant1's token with tenant2's subdomain is rejected."""
    from src.core.config_loader import current_tenant
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal, Tenant
    from src.core.main import get_principal_from_context

    # Clear tenant context
    current_tenant.set(None)

    # Create two tenants
    with get_db_session() as session:
        tenant1 = Tenant(
            tenant_id="tenant_wonderstruck",
            name="Wonderstruck",
            subdomain="wonderstruck",
            is_active=True,
        )
        tenant2 = Tenant(
            tenant_id="tenant_test_agent",
            name="Test Agent",
            subdomain="test-agent",
            is_active=True,
        )
        session.add_all([tenant1, tenant2])
        session.flush()

        # Create principal for tenant1 only
        principal1 = Principal(
            principal_id="adv_wonderstruck",
            tenant_id="tenant_wonderstruck",
            name="Wonderstruck Advertiser",
            access_token="wonderstruck_token_abc123",
            platform_mappings={"mock": {"id": "adv_wonderstruck"}},
        )
        session.add(principal1)
        session.commit()

    # Try to use tenant1's token with tenant2's subdomain
    context = Mock()
    context.meta = {
        "headers": {
            "x-adcp-auth": "wonderstruck_token_abc123",  # Wonderstruck token
            "host": "test-agent.sales-agent.scope3.com",  # Test-agent subdomain
        }
    }

    with patch("src.core.auth.get_http_headers", return_value={}):
        # Should raise ToolError because token doesn't belong to detected tenant
        with pytest.raises(ToolError) as exc_info:
            get_principal_from_context(context)

        error = exc_info.value
        assert error.args[0] == "INVALID_AUTH_TOKEN"
        assert "tenant_test_agent" in error.args[1]


@pytest.mark.requires_db
def test_no_fallback_to_first_tenant(integration_db):
    """Test that we never fall back to the first active tenant (the original bug)."""
    from src.core.config_loader import current_tenant, get_current_tenant
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    # Create multiple tenants
    with get_db_session() as session:
        tenant1 = Tenant(
            tenant_id="tenant_first",
            name="First Tenant",
            subdomain="first",
            is_active=True,
        )
        tenant2 = Tenant(
            tenant_id="tenant_second",
            name="Second Tenant",
            subdomain="second",
            is_active=True,
        )
        session.add_all([tenant1, tenant2])
        session.commit()

    # Clear tenant context
    current_tenant.set(None)

    # Try to get tenant without setting context
    # Should raise RuntimeError, not return tenant_first
    with pytest.raises(RuntimeError) as exc_info:
        get_current_tenant()

    assert "No tenant context set" in str(exc_info.value)
    assert "security error" in str(exc_info.value).lower()

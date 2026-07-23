"""Test tenant isolation fix for get_products.

This test verifies that when accessing a tenant via subdomain (e.g., wonderstruck.sales-agent.example.com),
the products returned belong to that tenant, not the tenant associated with the auth token.

Bug: Previously, get_principal_from_token() would overwrite the tenant context set from the subdomain
with the tenant associated with the principal's token, causing products from the wrong tenant to be returned.

Fix: get_principal_from_token() now only sets tenant context when doing global token lookup (no tenant_id specified).
When tenant_id is provided (from subdomain), it preserves the existing tenant context.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.auth import get_principal_from_context
from src.core.config_loader import get_current_tenant, set_current_tenant
from tests.helpers import assert_no_tenant_disclosure

# UUID tenant ids for the cross-tenant rejection: the buyer routes by subdomain
# (host header), so the id is purely the internal identifier the redaction
# protects — the host-routed deploy shape. See the breach-fix sibling.
WONDERSTRUCK_TENANT_ID = "c40b8e21-6f7a-4d35-b912-8e5c31a70df6"
TEST_AGENT_TENANT_ID = "1f8d90b5-3c46-42ae-9d07-b28fa5e61c94"


@pytest.mark.requires_db
def test_tenant_isolation_with_subdomain_and_cross_tenant_token(integration_db):
    """Test that cross-tenant tokens are rejected for security.

    When accessing a tenant via subdomain (e.g., wonderstruck.sales-agent.example.com),
    tokens from a different tenant should be rejected, not accepted with overridden context.
    This prevents principals from one tenant accessing another tenant's resources.

    In-process guard: this calls ``get_principal_from_context`` directly, so there
    is no wire here and the envelope is the one production WOULD build at the
    boundary. The buyer-facing wire is pinned by
    test_auth_suggestion_parity.py::TestInvalidTokenA2ANoDisclosure.
    """

    from fastmcp.exceptions import ToolError

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal as ModelPrincipal
    from src.core.database.models import Tenant
    from src.core.exceptions import (
        INVALID_TOKEN_MESSAGE,
        AdCPAuthenticationError,
    )

    # Create two tenants
    with get_db_session() as session:
        # Tenant 1: Wonderstruck (accessed via subdomain)
        wonderstruck = Tenant(
            tenant_id=WONDERSTRUCK_TENANT_ID,
            name="Wonderstruck",
            subdomain="wonderstruck",
            ad_server="mock",
            admin_token="wonderstruck_admin_token",
            is_active=True,
        )
        session.add(wonderstruck)

        # Tenant 2: Test Agent (principal's token belongs to this tenant)
        test_agent = Tenant(
            tenant_id=TEST_AGENT_TENANT_ID,
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
            tenant_id=TEST_AGENT_TENANT_ID,
            name="Test Agent Principal",
            access_token="test_agent_principal_token",
            platform_mappings={"mock": {"id": "principal_test_agent"}},
        )
        session.add(principal)
        session.commit()

    # Simulate request to wonderstruck.sales-agent.example.com with test-agent token
    # This should be REJECTED for security reasons
    mock_context = MagicMock()
    mock_context.meta = {
        "headers": {
            "host": "wonderstruck.sales-agent.example.com",
            "x-adcp-auth": "test_agent_principal_token",
        }
    }

    # Mock get_http_headers to return the headers
    with patch("src.core.auth.get_http_headers") as mock_get_headers:
        mock_get_headers.return_value = mock_context.meta["headers"]

        # Verify cross-tenant token is REJECTED
        with pytest.raises((ToolError, AdCPAuthenticationError)) as exc_info:
            get_principal_from_context(mock_context)

        # Neither tenant id may ride back to the caller: not the one detected from
        # the host, nor the token's own. Graded on the WHOLE envelope, not just the
        # message, so an id re-added under details/context cannot slip through.
        # (The sibling breach-fix test already checked both; this one checked only
        # the detected tenant.)
        # Pass the exception straight in — the helper builds the envelope — so this
        # matches the other three grading sites and drops the extra import.
        assert_no_tenant_disclosure(exc_info.value, WONDERSTRUCK_TENANT_ID)
        assert_no_tenant_disclosure(exc_info.value, TEST_AGENT_TENANT_ID)
        # Positive pin: the rejection still happened, with the shared wording.
        assert INVALID_TOKEN_MESSAGE in str(exc_info.value)


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

        # Verify tenant context was returned (caller sets it at transport boundary)
        assert tenant_ctx is not None
        assert tenant_ctx["tenant_id"] == "tenant_global"
        assert tenant_ctx["subdomain"] == "global"

        # Simulate transport boundary: caller sets ContextVar
        set_current_tenant(tenant_ctx)
        current_tenant = get_current_tenant()
        assert current_tenant is not None
        assert current_tenant["tenant_id"] == "tenant_global"


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

    # Simulate request to admin-test.sales-agent.example.com with admin token
    mock_context = MagicMock()
    mock_context.meta = {
        "headers": {
            "host": "admin-test.sales-agent.example.com",
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

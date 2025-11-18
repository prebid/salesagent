"""Integration tests for list_creatives authentication and authorization.

Tests verify that:
1. list_creatives requires authentication (unlike discovery endpoints)
2. Authenticated users only see their own creatives
3. Unauthenticated requests are rejected
"""

from unittest.mock import patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import Principal
from src.core.schema_adapters import ListCreativesResponse
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, auth_token=None):
        if auth_token is None:
            self.meta = {"headers": {}}  # No auth header
        else:
            # Include Host header for tenant detection (security requirement)
            self.meta = {
                "headers": {
                    "x-adcp-auth": auth_token,
                    "host": "auth-test.sales-agent.scope3.com",  # Matches subdomain in setup_test_data
                }
            }


class TestListCreativesAuthentication:
    """Integration tests for list_creatives authentication."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self, integration_db):
        """Create test tenant with multiple principals and their creatives."""
        with get_db_session() as session:
            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id="auth_test_tenant",
                name="Auth Test Tenant",
                subdomain="auth-test",
                is_active=True,
                ad_server="mock",
                enable_axe_signals=True,
                authorized_emails=[],
                authorized_domains=[],
                auto_approve_format_ids=["display_300x250"],
                human_review_required=False,
            )
            session.add(tenant)

            # Create two different principals (advertisers)
            principal_a = Principal(
                tenant_id="auth_test_tenant",
                principal_id="advertiser_a",
                name="Advertiser A",
                access_token="token-advertiser-a",
                platform_mappings={"mock": {"id": "advertiser_a"}},
            )
            principal_b = Principal(
                tenant_id="auth_test_tenant",
                principal_id="advertiser_b",
                name="Advertiser B",
                access_token="token-advertiser-b",
                platform_mappings={"mock": {"id": "advertiser_b"}},
            )
            session.add_all([principal_a, principal_b])

            # Commit tenant and principals before creating creatives (FK constraint requirement)
            session.commit()

            # Create creatives for advertiser A
            for i in range(3):
                creative_a = DBCreative(
                    tenant_id="auth_test_tenant",
                    creative_id=f"creative_a_{i}",
                    principal_id="advertiser_a",
                    name=f"Advertiser A Creative {i}",
                    format="display_300x250",
                    agent_url="https://creative.adcontextprotocol.org/",
                    status="approved",
                    data={
                        "assets": {
                            "image": {
                                "url": f"https://example.com/creative_a_{i}.jpg",
                                "width": 300,
                                "height": 250,
                            }
                        }
                    },
                )
                session.add(creative_a)

            # Create creatives for advertiser B
            for i in range(2):
                creative_b = DBCreative(
                    tenant_id="auth_test_tenant",
                    creative_id=f"creative_b_{i}",
                    principal_id="advertiser_b",
                    name=f"Advertiser B Creative {i}",
                    format="display_300x250",
                    agent_url="https://creative.adcontextprotocol.org/",
                    status="approved",
                    data={
                        "assets": {
                            "image": {
                                "url": f"https://example.com/creative_b_{i}.jpg",
                                "width": 300,
                                "height": 250,
                            }
                        }
                    },
                )
                session.add(creative_b)

            session.commit()

    def _import_mcp_tool(self):
        """Import MCP tool to avoid module-level database initialization."""
        from src.core.tools.creatives import list_creatives_raw

        return list_creatives_raw

    def test_unauthenticated_request_should_fail(self):
        """Test that list_creatives rejects requests without authentication.

        SECURITY: list_creatives returns sensitive creative data and should require auth.
        Unlike discovery endpoints (list_creative_formats), this endpoint exposes
        actual creative assets which are principal-specific.
        """
        core_list_creatives_tool = self._import_mcp_tool()

        # Mock context with NO auth token
        mock_context = MockContext(auth_token=None)

        # Mock get_http_headers to return empty headers (no auth)
        # Patch at the source where it's imported from
        with patch("fastmcp.server.dependencies.get_http_headers", return_value={}):
            # This should raise ToolError due to missing authentication
            from fastmcp.exceptions import ToolError

            with pytest.raises(ToolError, match="Missing x-adcp-auth header"):
                core_list_creatives_tool(ctx=mock_context)

    def test_authenticated_user_sees_only_own_creatives(self):
        """Test that authenticated users only see their own creatives.

        SECURITY: Creatives should be filtered by principal_id to prevent
        cross-principal data leakage within the same tenant.
        """
        core_list_creatives_tool = self._import_mcp_tool()

        # Mock context with advertiser A's token
        mock_context = MockContext(auth_token="token-advertiser-a")

        # Mock get_http_headers to return auth + host headers for tenant detection
        # Patch at the source where it's imported from
        with patch(
            "fastmcp.server.dependencies.get_http_headers",
            return_value={
                "x-adcp-auth": "token-advertiser-a",
                "host": "auth-test.sales-agent.scope3.com",
            },
        ):
            response = core_list_creatives_tool(ctx=mock_context)

            # Verify response structure
            assert isinstance(response, ListCreativesResponse)

            # Should only see advertiser A's 3 creatives (not advertiser B's 2 creatives)
            assert len(response.creatives) == 3
            assert response.query_summary.total_matching == 3

            # Verify all creatives belong to advertiser A
            for creative in response.creatives:
                assert creative.principal_id == "advertiser_a"
                assert "Advertiser A" in creative.name

    def test_different_principal_sees_different_creatives(self):
        """Test that different principals see different creative sets.

        SECURITY: Each principal should have isolated access to their own creatives.
        """
        core_list_creatives_tool = self._import_mcp_tool()

        # Test with advertiser B's token
        mock_context_b = MockContext(auth_token="token-advertiser-b")

        # Mock get_http_headers to return auth + host headers for tenant detection
        # Patch at the source where it's imported from
        with patch(
            "fastmcp.server.dependencies.get_http_headers",
            return_value={
                "x-adcp-auth": "token-advertiser-b",
                "host": "auth-test.sales-agent.scope3.com",
            },
        ):
            response = core_list_creatives_tool(ctx=mock_context_b)

            # Verify response structure
            assert isinstance(response, ListCreativesResponse)

            # Should only see advertiser B's 2 creatives (not advertiser A's 3 creatives)
            assert len(response.creatives) == 2
            assert response.query_summary.total_matching == 2

            # Verify all creatives belong to advertiser B
            for creative in response.creatives:
                assert creative.principal_id == "advertiser_b"
                assert "Advertiser B" in creative.name

    def test_invalid_token_should_fail(self):
        """Test that list_creatives rejects invalid authentication tokens.

        SECURITY: Invalid tokens should be rejected, not treated as anonymous.
        """
        core_list_creatives_tool = self._import_mcp_tool()

        # Mock context with invalid token
        mock_context = MockContext(auth_token="invalid-token-xyz")

        # Mock get_http_headers to return auth + host headers for tenant detection
        # Patch at the source where it's imported from
        with patch(
            "fastmcp.server.dependencies.get_http_headers",
            return_value={
                "x-adcp-auth": "invalid-token-xyz",
                "host": "auth-test.sales-agent.scope3.com",
            },
        ):
            from fastmcp.exceptions import ToolError

            # This should raise ToolError due to invalid authentication
            with pytest.raises(ToolError, match="INVALID_AUTH_TOKEN"):
                core_list_creatives_tool(ctx=mock_context)

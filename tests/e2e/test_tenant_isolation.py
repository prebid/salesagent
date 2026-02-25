"""
Multi-Tenant Isolation E2E Tests

Validates that tenant data isolation works correctly through the full HTTP stack
(nginx proxy -> FastAPI -> PostgreSQL -> MCP response).

Core invariant: A tenant's MCP tools must only return data belonging to that
tenant; cross-tenant tokens must be rejected at the transport boundary.

These tests require two tenants in the database:
- ci-test: The primary test tenant (created by init_database_ci.py)
- iso-test: The isolation test tenant (created by init_database_ci.py)

Note: These tests use x-adcp-tenant header for tenant selection because
DNS-based subdomain routing is not available in the CI Docker stack.
Integration tests in test_tenant_isolation_breach_fix.py cover Host-based
subdomain routing separately.
"""

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import parse_tool_result


class TestMultiTenantIsolation:
    """Verify that MCP tool responses are scoped to the requesting tenant."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tenant_a_only_sees_own_products(self, docker_services_e2e, live_server):
        """ci-test tenant should only see products belonging to ci-test, not iso-test.

        Exercises: x-adcp-tenant header -> tenant resolution -> get_products -> tenant-scoped query.
        """
        headers = {
            "x-adcp-auth": "ci-test-token",
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers=headers,
        )

        async with Client(transport=transport) as client:
            result = await client.call_tool(
                "get_products",
                {"brief": "all products", "context": {"e2e": "tenant_isolation"}},
            )
            data = parse_tool_result(result)

            assert "products" in data, "Response must contain products key"
            products = data["products"]
            assert len(products) > 0, "ci-test tenant must have at least one product"

            # Verify ALL returned products belong to ci-test (product IDs start with prod_)
            product_ids = [p["product_id"] for p in products]
            for pid in product_ids:
                assert not pid.startswith("iso_"), (
                    f"ci-test tenant received iso-test product: {pid}. Tenant isolation breach detected."
                )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tenant_b_only_sees_own_products(self, docker_services_e2e, live_server):
        """iso-test tenant should only see products belonging to iso-test, not ci-test.

        Exercises: x-adcp-tenant header -> tenant resolution -> get_products -> tenant-scoped query.
        """
        headers = {
            "x-adcp-auth": "iso-test-token",
            "x-adcp-tenant": "iso-test",
        }
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers=headers,
        )

        async with Client(transport=transport) as client:
            result = await client.call_tool(
                "get_products",
                {"brief": "all products", "context": {"e2e": "tenant_isolation"}},
            )
            data = parse_tool_result(result)

            assert "products" in data, "Response must contain products key"
            products = data["products"]
            assert len(products) > 0, "iso-test tenant must have at least one product"

            # Verify ALL returned products belong to iso-test (product IDs start with iso_)
            product_ids = [p["product_id"] for p in products]
            for pid in product_ids:
                assert pid.startswith("iso_"), (
                    f"iso-test tenant received non-iso product: {pid}. Tenant isolation breach detected."
                )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cross_tenant_token_rejected(self, docker_services_e2e, live_server):
        """ci-test token must be rejected when targeting iso-test tenant.

        This prevents a principal from one tenant accessing another tenant's
        resources by manipulating the tenant header.

        Exercises: x-adcp-tenant header -> tenant resolution -> token validation
        -> cross-tenant rejection.
        """
        headers = {
            "x-adcp-auth": "ci-test-token",  # Token belongs to ci-test
            "x-adcp-tenant": "iso-test",  # But targeting iso-test tenant
        }
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers=headers,
        )

        # The MCP client should either fail during session init or tool call
        # because the token doesn't belong to the targeted tenant
        with pytest.raises(Exception) as exc_info:
            async with Client(transport=transport) as client:
                await client.call_tool(
                    "get_products",
                    {"brief": "should fail", "context": {"e2e": "cross_tenant"}},
                )

        # Verify the error is auth-related, not a random failure
        error_str = str(exc_info.value).lower()
        assert any(
            keyword in error_str
            for keyword in ["auth", "token", "invalid", "tenant", "denied", "forbidden", "unauthorized"]
        ), f"Expected authentication/authorization error, got: {exc_info.value}"

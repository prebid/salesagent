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
                assert not pid.startswith(
                    "iso_"
                ), f"ci-test tenant received iso-test product: {pid}. Tenant isolation breach detected."

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
                assert pid.startswith(
                    "iso_"
                ), f"iso-test tenant received non-iso product: {pid}. Tenant isolation breach detected."

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_misleading_tenant_header_does_not_grant_cross_tenant_access(self, docker_services_e2e, live_server):
        """Cross-tenant header injection must not grant access to another tenant.

        Contract: tenant scope is derived from ``Principal.access_token``
        (see ``adcp/server/auth.py:121-145`` and ``auth.py:308-309``); the
        ``x-adcp-tenant`` header is not part of that derivation. A buyer
        sending a ci-test token + misleading ``x-adcp-tenant: iso-test``
        must receive ci-test data, never iso-test data.

        Compares two real catalogs (one per tenant) and asserts the cross-
        tenant call returns the ci-test catalog disjoint from iso-test —
        catches a regression that wires the header into tenant selection
        even if seed-data IDs change shape.
        """

        # First, fetch each tenant's authoritative catalog via the matching
        # token+header pair. These are the ground-truth product-ID sets
        # the cross-tenant call's response must be disjoint with.
        async def _fetch_catalog(token: str, tenant: str) -> set[str]:
            transport = StreamableHttpTransport(
                url=f"{live_server['mcp']}/mcp/",
                headers={"x-adcp-auth": token, "x-adcp-tenant": tenant},
            )
            async with Client(transport=transport) as client:
                result = await client.call_tool(
                    "get_products",
                    {"brief": "all products", "context": {"e2e": "cross_tenant_baseline"}},
                )
                data = parse_tool_result(result)
                return {p["product_id"] for p in data.get("products", [])}

        ci_test_catalog = await _fetch_catalog("ci-test-token", "ci-test")
        iso_test_catalog = await _fetch_catalog("iso-test-token", "iso-test")
        assert ci_test_catalog, "ci-test must have a non-empty seed catalog for this test"
        assert iso_test_catalog, "iso-test must have a non-empty seed catalog for this test"
        assert ci_test_catalog.isdisjoint(iso_test_catalog), (
            "Test seed bug: ci-test and iso-test catalogs overlap, so this test "
            "cannot prove tenant scoping. Fix init_database_ci.py."
        )

        # Now the actual injection attempt: ci-test token + iso-test header.
        injection_transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={
                "x-adcp-auth": "ci-test-token",  # Token belongs to ci-test
                "x-adcp-tenant": "iso-test",  # Misleading header — must not grant access
            },
        )
        async with Client(transport=injection_transport) as client:
            result = await client.call_tool(
                "get_products",
                {"brief": "header injection should not change scope", "context": {"e2e": "cross_tenant"}},
            )
            injected_catalog = {p["product_id"] for p in parse_tool_result(result).get("products", [])}

            # Positive: the response IS the ci-test catalog (token's tenant won).
            assert injected_catalog == ci_test_catalog, (
                f"Token-bound scope broken: ci-test token returned products "
                f"{injected_catalog}, not the expected ci-test catalog "
                f"{ci_test_catalog}."
            )
            # Negative: zero overlap with iso-test (header's claim was ignored).
            assert injected_catalog.isdisjoint(iso_test_catalog), (
                f"Cross-tenant breach: ci-test token + x-adcp-tenant: iso-test "
                f"surfaced iso-test products {injected_catalog & iso_test_catalog}. "
                f"The modern stack must derive tenant from the bearer token only."
            )

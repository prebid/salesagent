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

Assertion rigor (salesagent-18h.14): these tests must FAIL loudly on a real
cross-tenant data leak. The product-listing tests assert exact product-id set
equality (not a naming-prefix heuristic), and the cross-tenant-token test fails
explicitly if the call returns data instead of being rejected.
"""

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ClientError, FastMCPError, ToolError
from mcp.shared.exceptions import McpError

from tests.e2e.adcp_request_builder import parse_tool_result

# Canonical product-id sets, kept in sync with scripts/setup/init_database_ci.py.
# ci-test products are defined in init_database_ci.py `products_data` (~line 306).
# iso-test products are defined in init_database_ci.py `iso_products_data` (~line 596).
# If you change those product lists, update these sets (and vice versa) — a
# reciprocal comment in init_database_ci.py points back here.
CI_TEST_PRODUCT_IDS = {"prod_display_premium", "prod_video_premium"}
ISO_TEST_PRODUCT_IDS = {"iso_display_standard"}

# Exception types that represent a transport/protocol-level rejection of the
# request. A cross-tenant token must surface as one of these (raised by the
# FastMCP client during session init or tool call), never as a successful
# response carrying another tenant's data.
TRANSPORT_REJECTION_ERRORS = (ToolError, ClientError, FastMCPError, McpError)

# Substrings that indicate the rejection is auth/authorization-related rather
# than an unrelated failure (network, framing, JSON, etc.).
AUTH_ERROR_KEYWORDS = (
    "auth",
    "token",
    "invalid",
    "tenant",
    "denied",
    "forbidden",
    "unauthorized",
    "principal",
    "no tenant configuration",
)


class TestMultiTenantIsolation:
    """Verify that MCP tool responses are scoped to the requesting tenant."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tenant_a_only_sees_own_products(self, docker_services_e2e, live_server):
        """ci-test get_products returns EXACTLY the ci-test product set, nothing else.

        Verifies: the response product-id set equals CI_TEST_PRODUCT_IDS exactly.
        This fails if (a) any iso-test product leaks in, (b) any product from a
        third tenant leaks in, or (c) a ci-test product is missing. It does not
        rely on the accident that ci-test product ids lack an "iso_" prefix.

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
            returned_ids = {p["product_id"] for p in products}

            assert returned_ids == CI_TEST_PRODUCT_IDS, (
                f"ci-test tenant must see EXACTLY {sorted(CI_TEST_PRODUCT_IDS)}, "
                f"got {sorted(returned_ids)}. "
                f"Unexpected (leaked) products: {sorted(returned_ids - CI_TEST_PRODUCT_IDS)}. "
                f"Missing products: {sorted(CI_TEST_PRODUCT_IDS - returned_ids)}. "
                "Tenant isolation breach or product-set drift detected."
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_tenant_b_only_sees_own_products(self, docker_services_e2e, live_server):
        """iso-test get_products returns EXACTLY the iso-test product set, nothing else.

        Verifies: the response product-id set equals ISO_TEST_PRODUCT_IDS exactly.
        This fails if any ci-test (or third-tenant) product leaks into iso-test's
        listing, without relying on the "iso_" naming prefix.

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
            returned_ids = {p["product_id"] for p in products}

            assert returned_ids == ISO_TEST_PRODUCT_IDS, (
                f"iso-test tenant must see EXACTLY {sorted(ISO_TEST_PRODUCT_IDS)}, "
                f"got {sorted(returned_ids)}. "
                f"Unexpected (leaked) products: {sorted(returned_ids - ISO_TEST_PRODUCT_IDS)}. "
                f"Missing products: {sorted(ISO_TEST_PRODUCT_IDS - returned_ids)}. "
                "Tenant isolation breach or product-set drift detected."
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cross_tenant_token_rejected(self, docker_services_e2e, live_server):
        """ci-test token targeting iso-test tenant must be REJECTED, not served.

        This prevents a principal from one tenant accessing another tenant's
        resources by manipulating the tenant header.

        Verifies (the security-critical part): the call does NOT return a
        successful response. If it returns at all, the test fails explicitly —
        a successful (or partially successful) response means the isolation
        boundary was bypassed, which the previous `pytest.raises(Exception)`
        form would have silently passed. Only when the call is rejected do we
        additionally assert the rejection is a transport-level auth error
        (not an unrelated network/framing failure).

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

        raised: Exception | None = None
        data = None
        try:
            async with Client(transport=transport) as client:
                result = await client.call_tool(
                    "get_products",
                    {"brief": "should fail", "context": {"e2e": "cross_tenant"}},
                )
                data = parse_tool_result(result)
        except Exception as exc:  # noqa: BLE001 - we re-classify below
            raised = exc

        # Fail-fast on the actual isolation-bypass scenario: the cross-tenant
        # call completed and returned data. This is the case the old
        # pytest.raises(Exception) assertion could never catch.
        if raised is None:
            leaked = sorted({p["product_id"] for p in (data or {}).get("products", [])})
            pytest.fail(
                "Cross-tenant call (ci-test token -> iso-test tenant) returned a "
                f"successful response instead of being rejected. Tenant isolation "
                f"bypass detected. Returned products: {leaked or '<none>'}, "
                f"response keys: {sorted((data or {}).keys())}."
            )

        # A rejection occurred. It must be a transport/protocol-level error
        # (FastMCP/MCP), not an arbitrary client-side bug. Narrowing away from
        # bare Exception ensures a network timeout or JSON decode error no
        # longer counts as "isolation enforced".
        assert isinstance(raised, TRANSPORT_REJECTION_ERRORS), (
            f"Cross-tenant call was rejected, but with an unexpected error type "
            f"{type(raised).__name__}: {raised!r}. Expected a FastMCP/MCP "
            f"transport rejection ({', '.join(e.__name__ for e in TRANSPORT_REJECTION_ERRORS)})."
        )

        # And the rejection must be auth/authorization-related, not an unrelated
        # transport failure that happens to be a ToolError/McpError.
        error_str = str(raised).lower()
        assert any(keyword in error_str for keyword in AUTH_ERROR_KEYWORDS), (
            f"Expected an authentication/authorization rejection, got {type(raised).__name__}: {raised}"
        )

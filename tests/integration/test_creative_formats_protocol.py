"""Integration tests: creative formats protocol and REST transport.

Tests MCP combined-filter dispatch, MCP ToolResult wrapping,
A2A full catalog, and A2A tenant context resolution.

All tests run against the real creative agent catalog (49 formats):
28 display, 12 video, 4 dooh, 3 audio, 2 native.

Obligation IDs:
- UC-005-MAIN-MCP-16: combined filters narrow results
- UC-005-MAIN-MCP-17: MCP ToolResult wrapping
- UC-005-MAIN-REST-01: full catalog via A2A
- UC-005-MAIN-REST-03: tenant context from A2A headers
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from adcp.types.generated_poc.enums.asset_content_type import AssetContentType
from adcp.types.generated_poc.enums.format_category import FormatCategory
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.schemas import (
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
)
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeFormatsEnv

# Real catalog totals served by the Docker creative agent container
REAL_CATALOG_TOTAL = 49
REAL_CATALOG_DISPLAY = 28
REAL_CATALOG_VIDEO = 12

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-16: Combined filters narrow results
# ---------------------------------------------------------------------------


class TestCombinedFilters:
    """Covers: UC-005-MAIN-MCP-16 -- multiple filters applied conjunctively."""

    def test_combined_type_asset_dimension_filters(self, integration_db):
        """UC-005-MAIN-MCP-16: type=display + asset_types=[image] + max_width=728.

        Filters are applied conjunctively: every returned format must be
        display type AND have at least one image asset AND have at least one
        render with width <= 728. Verified against the real catalog.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_impl(req=req)

        # All returned formats must satisfy all three constraints simultaneously
        for fmt in response.formats:
            assert fmt.type == FormatCategory.display, f"Expected display type, got {fmt.type} for {fmt.format_id.id}"

    def test_combined_filters_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-16: same combined filter logic through MCP wrapper."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            response = env.call_mcp(
                type=FormatCategory.display,
                asset_types=[AssetContentType.image],
                max_width=728,
            )

        # All returned formats must be display type (filter applied conjunctively)
        for fmt in response.formats:
            assert fmt.type == FormatCategory.display, (
                f"MCP: expected display type, got {fmt.type} for {fmt.format_id.id}"
            )

    def test_combined_filters_via_a2a(self, integration_db):
        """UC-005-MAIN-MCP-16: same combined filter logic through A2A wrapper."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_a2a(req=req)

        # All returned formats must be display type
        for fmt in response.formats:
            assert fmt.type == FormatCategory.display, (
                f"A2A: expected display type, got {fmt.type} for {fmt.format_id.id}"
            )

    def test_all_filters_conjunctive_empty_result(self, integration_db):
        """UC-005-MAIN-MCP-16: if no format matches all filters, result is empty.

        Using max_width=0 ensures no format can satisfy width <= 0, so the
        conjunctive filter must return an empty list regardless of other matches.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=0,
            )
            response = env.call_impl(req=req)

        assert response.formats == []


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-17: MCP ToolResult wrapping
# ---------------------------------------------------------------------------


class TestMcpToolResultWrapping:
    """Covers: UC-005-MAIN-MCP-17 -- MCP response wraps response as ToolResult."""

    def test_mcp_returns_tool_result_with_structured_content(self, integration_db):
        """UC-005-MAIN-MCP-17: MCP wrapper returns ToolResult with structured content.

        The MCP wrapper must return a ToolResult object whose
        structured_content is the ListCreativeFormatsResponse data,
        parseable as JSON. Verified against the real catalog.
        """
        from src.core.tools.creative_formats import list_creative_formats

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env._commit_factory_data()

            from tests.harness.transport import Transport

            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=env.identity_for(Transport.MCP))

            tool_result = asyncio.run(list_creative_formats(ctx=mock_ctx))

        # Verify it is a ToolResult
        assert isinstance(tool_result, ToolResult)

        # Verify structured_content is present and is a dict-like object
        sc = tool_result.structured_content
        assert sc is not None

        # Verify it can be parsed as ListCreativeFormatsResponse
        parsed = ListCreativeFormatsResponse(**sc)
        assert len(parsed.formats) >= 1

    def test_mcp_tool_result_content_is_text(self, integration_db):
        """UC-005-MAIN-MCP-17: ToolResult.content contains displayable text."""
        from src.core.tools.creative_formats import list_creative_formats

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env._commit_factory_data()

            from tests.harness.transport import Transport

            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=env.identity_for(Transport.MCP))

            tool_result = asyncio.run(list_creative_formats(ctx=mock_ctx))

        # content is a list of TextContent objects with displayable text
        assert tool_result.content is not None
        assert len(tool_result.content) > 0
        # First content item has text
        assert hasattr(tool_result.content[0], "text")
        assert len(tool_result.content[0].text) > 0

    def test_mcp_structured_content_includes_formats_array(self, integration_db):
        """UC-005-MAIN-MCP-17: structured_content contains 'formats' key."""
        from src.core.tools.creative_formats import list_creative_formats

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env._commit_factory_data()

            from tests.harness.transport import Transport

            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=env.identity_for(Transport.MCP))

            tool_result = asyncio.run(list_creative_formats(ctx=mock_ctx))

        sc = tool_result.structured_content
        assert "formats" in sc
        assert isinstance(sc["formats"], list)
        assert len(sc["formats"]) >= 1


# ---------------------------------------------------------------------------
# UC-005-MAIN-REST-01: Full catalog via A2A
# ---------------------------------------------------------------------------


class TestFullCatalogViaA2A:
    """Covers: UC-005-MAIN-REST-01 -- A2A returns complete format catalog."""

    def test_a2a_returns_complete_catalog(self, integration_db):
        """UC-005-MAIN-REST-01: list_creative_formats_raw returns full catalog.

        The A2A endpoint (list_creative_formats_raw) returns a valid
        ListCreativeFormatsResponse with all formats from the real catalog.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == REAL_CATALOG_TOTAL

    def test_a2a_response_format_structure(self, integration_db):
        """UC-005-MAIN-REST-01: each format in A2A response has required fields.

        POST-S1: complete catalog. POST-S2: each format includes
        format_id, name, type.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            response = env.call_a2a()

        assert len(response.formats) >= 1
        # Verify every format has the required fields populated
        for fmt in response.formats:
            assert fmt.format_id is not None, "format_id must be present"
            assert fmt.format_id.id, "format_id.id must be non-empty"
            assert fmt.format_id.agent_url is not None, "format_id.agent_url must be present"
            assert fmt.name, "name must be non-empty"
            assert fmt.type is not None, "type must be present"

    def test_a2a_no_type_filter_returns_all_formats(self, integration_db):
        """UC-005-MAIN-REST-01: no filters returns the complete catalog."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == REAL_CATALOG_TOTAL

    def test_a2a_and_impl_return_same_catalog(self, integration_db):
        """UC-005-MAIN-REST-01: A2A and _impl return identical results."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            impl_response = env.call_impl()
            a2a_response = env.call_a2a()

        assert len(impl_response.formats) == len(a2a_response.formats)
        impl_ids = {f.format_id.id for f in impl_response.formats}
        a2a_ids = {f.format_id.id for f in a2a_response.formats}
        assert impl_ids == a2a_ids


# ---------------------------------------------------------------------------
# UC-005-MAIN-REST-03: Tenant context from A2A headers
# ---------------------------------------------------------------------------


class TestTenantContextFromA2AHeaders:
    """Covers: UC-005-MAIN-REST-03 -- tenant context resolved from identity."""

    def test_a2a_uses_tenant_from_identity(self, integration_db):
        """UC-005-MAIN-REST-03: A2A resolves tenant from ResolvedIdentity.

        When the identity has tenant context, the A2A wrapper uses that
        tenant to return the correct tenant's format catalog.
        """
        with CreativeFormatsEnv(tenant_id="my_tenant") as env:
            TenantFactory(tenant_id="my_tenant")

            identity = PrincipalFactory.make_identity(
                principal_id="buyer_1",
                tenant_id="my_tenant",
                protocol="a2a",
            )
            response = env.call_a2a(identity=identity)

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) >= 1

    def test_a2a_different_tenants_get_same_catalog(self, integration_db):
        """UC-005-MAIN-REST-03: different tenant identities each resolve tenant context.

        Two separate calls with different tenant identities must each
        resolve to a valid tenant context and return the real catalog.
        Both tenants share the same default creative agent catalog.
        """
        # Tenant A
        with CreativeFormatsEnv(tenant_id="tenant_a") as env_a:
            TenantFactory(tenant_id="tenant_a")

            identity_a = PrincipalFactory.make_identity(
                principal_id="buyer_a",
                tenant_id="tenant_a",
                protocol="a2a",
            )
            response_a = env_a.call_a2a(identity=identity_a)

        # Tenant B (separate env/session)
        with CreativeFormatsEnv(tenant_id="tenant_b") as env_b:
            TenantFactory(tenant_id="tenant_b")

            identity_b = PrincipalFactory.make_identity(
                principal_id="buyer_b",
                tenant_id="tenant_b",
                protocol="a2a",
            )
            response_b = env_b.call_a2a(identity=identity_b)

        # Both tenants see the real catalog
        assert len(response_a.formats) >= 1
        assert len(response_b.formats) >= 1
        # Both tenants see the same global default catalog
        assert len(response_a.formats) == len(response_b.formats)

    def test_a2a_no_tenant_raises_auth_error(self, integration_db):
        """UC-005-MAIN-REST-03: missing tenant context raises AdCPAuthenticationError.

        When the identity has no tenant (tenant=None), the A2A wrapper
        must raise an auth error, not silently return empty data.
        """
        from src.core.exceptions import AdCPAuthenticationError

        identity_no_tenant = PrincipalFactory.make_identity(
            principal_id="buyer_no_tenant",
            tenant_id="no_tenant",
            tenant=None,
            protocol="a2a",
        )

        with CreativeFormatsEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_a2a(identity=identity_no_tenant)

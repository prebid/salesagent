"""Integration tests: creative formats protocol and REST transport.

Tests MCP combined-filter dispatch, MCP ToolResult wrapping,
A2A full catalog, and A2A tenant context resolution.

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
from adcp.types.generated_poc.core.format import (
    Assets,
    Assets5,
    Dimensions,
    Renders,
)
from adcp.types.generated_poc.enums.asset_content_type import AssetContentType
from adcp.types.generated_poc.enums.format_category import FormatCategory
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.schemas import (
    Format,
    FormatId,
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
)
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness import CreativeFormatsEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_format(
    format_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Build a Format with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        type=type,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-16: Combined filters narrow results
# ---------------------------------------------------------------------------


class TestCombinedFilters:
    """Covers: UC-005-MAIN-MCP-16 -- multiple filters applied conjunctively."""

    def test_combined_type_asset_dimension_filters(self, integration_db):
        """UC-005-MAIN-MCP-16: type=display + asset_types=[image] + max_width=728.

        Given diverse formats, only display formats with image assets and
        at least one render width <= 728 are returned.
        """
        # Display with image, width 300 -- SHOULD MATCH
        display_small_image = _make_format(
            "d_small",
            "Small Display Banner",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets(item_type="individual", asset_id="hero_image", required=True)],
        )
        # Display with image, width 970 -- should NOT match (too wide)
        display_wide_image = _make_format(
            "d_wide",
            "Wide Display Billboard",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=970, height=250))],
            assets=[Assets(item_type="individual", asset_id="billboard_image", required=True)],
        )
        # Display with video asset, width 300 -- should NOT match (wrong asset type)
        display_video = _make_format(
            "d_video",
            "Display Video Unit",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets5(item_type="individual", asset_id="hero_video", required=True)],
        )
        # Video with image, width 300 -- should NOT match (wrong type)
        video_image = _make_format(
            "v_image",
            "Video Companion",
            type=FormatCategory.video,
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets(item_type="individual", asset_id="companion_image", required=True)],
        )

        all_formats = [display_small_image, display_wide_image, display_video, video_image]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(all_formats)

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "d_small"

    def test_combined_filters_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-16: same combined filter logic through MCP wrapper."""
        display_match = _make_format(
            "d_match",
            "Matching Display",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
            assets=[Assets(item_type="individual", asset_id="banner_image", required=True)],
        )
        display_no_match = _make_format(
            "d_nomatch",
            "Non-Matching Display",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=970, height=250))],
            assets=[Assets(item_type="individual", asset_id="wide_image", required=True)],
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([display_match, display_no_match])

            response = env.call_mcp(
                type=FormatCategory.display,
                asset_types=[AssetContentType.image],
                max_width=728,
            )

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "d_match"

    def test_combined_filters_via_a2a(self, integration_db):
        """UC-005-MAIN-MCP-16: same combined filter logic through A2A wrapper."""
        display_match = _make_format(
            "d_a2a_match",
            "A2A Display Match",
            type=FormatCategory.display,
            renders=[Renders(role="primary", dimensions=Dimensions(width=320, height=50))],
            assets=[Assets(item_type="individual", asset_id="mobile_image", required=True)],
        )
        audio_format = _make_format(
            "a_nomatch",
            "Audio Ad",
            type=FormatCategory.audio,
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([display_match, audio_format])

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_a2a(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "d_a2a_match"

    def test_all_filters_conjunctive_empty_result(self, integration_db):
        """UC-005-MAIN-MCP-16: if no format matches all filters, result is empty."""
        # Video format -- fails type=display filter
        only_video = _make_format(
            "v1",
            "Video Only",
            type=FormatCategory.video,
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets(item_type="individual", asset_id="vid", required=True)],
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([only_video])

            req = ListCreativeFormatsRequest(
                type="display",
                asset_types=["image"],
                max_width=728,
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
        parseable as JSON.
        """
        from src.core.tools.creative_formats import list_creative_formats

        formats = [
            _make_format("display_300", "Medium Rectangle"),
            _make_format("video_15s", "Pre-roll 15s", type=FormatCategory.video),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
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
        assert len(parsed.formats) == 2

    def test_mcp_tool_result_content_is_text(self, integration_db):
        """UC-005-MAIN-MCP-17: ToolResult.content contains displayable text."""
        from src.core.tools.creative_formats import list_creative_formats

        formats = [_make_format("test_fmt", "Test Format")]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
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

        formats = [
            _make_format("fmt_a", "Format A"),
            _make_format("fmt_b", "Format B", type=FormatCategory.video),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            env._commit_factory_data()

            from tests.harness.transport import Transport

            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=env.identity_for(Transport.MCP))

            tool_result = asyncio.run(list_creative_formats(ctx=mock_ctx))

        sc = tool_result.structured_content
        assert "formats" in sc
        assert isinstance(sc["formats"], list)
        assert len(sc["formats"]) == 2


# ---------------------------------------------------------------------------
# UC-005-MAIN-REST-01: Full catalog via A2A
# ---------------------------------------------------------------------------


class TestFullCatalogViaA2A:
    """Covers: UC-005-MAIN-REST-01 -- A2A returns complete format catalog."""

    def test_a2a_returns_complete_catalog(self, integration_db):
        """UC-005-MAIN-REST-01: list_creative_formats_raw returns full catalog.

        The A2A endpoint (list_creative_formats_raw) returns a valid
        ListCreativeFormatsResponse with all registered formats.
        """
        formats = [
            _make_format("display_300x250", "Medium Rectangle"),
            _make_format("display_728x90", "Leaderboard"),
            _make_format("video_preroll", "Pre-roll 15s", type=FormatCategory.video),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == 3

    def test_a2a_response_format_structure(self, integration_db):
        """UC-005-MAIN-REST-01: each format in A2A response has required fields.

        POST-S1: complete catalog. POST-S2: each format includes
        format_id, name, type.
        """
        fmt = _make_format(
            "display_standard",
            "Standard Display",
            assets=[Assets(item_type="individual", asset_id="hero", required=True)],
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([fmt])

            response = env.call_a2a()

        assert len(response.formats) == 1
        result_fmt = response.formats[0]
        # FormatId is a structured object
        assert result_fmt.format_id.id == "display_standard"
        assert str(result_fmt.format_id.agent_url).rstrip("/") == DEFAULT_AGENT_URL
        assert result_fmt.name == "Standard Display"
        assert result_fmt.type == FormatCategory.display

    def test_a2a_empty_catalog_returns_empty_formats(self, integration_db):
        """UC-005-MAIN-REST-01: empty registry returns empty formats list, not error."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)
        assert response.formats == []

    def test_a2a_and_impl_return_same_catalog(self, integration_db):
        """UC-005-MAIN-REST-01: A2A and _impl return identical results."""
        formats = [
            _make_format("d1", "Display One"),
            _make_format("v1", "Video One", type=FormatCategory.video),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

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
        formats = [_make_format("tenant_fmt", "Tenant-Specific Format")]

        with CreativeFormatsEnv(tenant_id="my_tenant") as env:
            TenantFactory(tenant_id="my_tenant")
            env.set_registry_formats(formats)

            identity = PrincipalFactory.make_identity(
                principal_id="buyer_1",
                tenant_id="my_tenant",
                protocol="a2a",
            )
            response = env.call_a2a(identity=identity)

        assert isinstance(response, ListCreativeFormatsResponse)
        assert len(response.formats) == 1

    def test_a2a_different_tenants_get_different_catalogs(self, integration_db):
        """UC-005-MAIN-REST-03: different tenant identities see different catalogs.

        Two separate calls with different tenant identities should each
        resolve to the correct tenant context. We simulate this by running
        two env contexts with different tenant IDs and verifying independent
        format lists.
        """
        tenant_a_formats = [_make_format("fmtA", "Format for Tenant A")]
        tenant_b_formats = [
            _make_format("fmtB1", "Format B1 for Tenant B"),
            _make_format("fmtB2", "Format B2 for Tenant B"),
        ]

        # Tenant A
        with CreativeFormatsEnv(tenant_id="tenant_a") as env_a:
            TenantFactory(tenant_id="tenant_a")
            env_a.set_registry_formats(tenant_a_formats)

            identity_a = PrincipalFactory.make_identity(
                principal_id="buyer_a",
                tenant_id="tenant_a",
                protocol="a2a",
            )
            response_a = env_a.call_a2a(identity=identity_a)

        # Tenant B (separate env/session)
        with CreativeFormatsEnv(tenant_id="tenant_b") as env_b:
            TenantFactory(tenant_id="tenant_b")
            env_b.set_registry_formats(tenant_b_formats)

            identity_b = PrincipalFactory.make_identity(
                principal_id="buyer_b",
                tenant_id="tenant_b",
                protocol="a2a",
            )
            response_b = env_b.call_a2a(identity=identity_b)

        assert len(response_a.formats) == 1
        assert response_a.formats[0].format_id.id == "fmtA"
        assert len(response_b.formats) == 2
        b_ids = {f.format_id.id for f in response_b.formats}
        assert b_ids == {"fmtB1", "fmtB2"}

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

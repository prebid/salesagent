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

import pytest
from adcp.types import AssetContentType, ImageFormatAsset, VideoFormatAsset
from adcp.types.generated_poc.core.format import Dimensions, Renders  # TODO: no stable alias in adcp.types

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
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Build a Format with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
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
        """UC-005-MAIN-MCP-16: asset_types=[image] + max_width=728.

        Given diverse formats, only formats with image assets and
        at least one render width <= 728 are returned.
        The type filter was removed in adcp 3.12.
        """
        # Image asset, width 300 -- SHOULD MATCH
        display_small_image = _make_format(
            "d_small",
            "Small Display Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="hero_image", required=True)],
        )
        # Image asset, width 970 -- should NOT match (too wide)
        display_wide_image = _make_format(
            "d_wide",
            "Wide Display Billboard",
            renders=[Renders(role="primary", dimensions=Dimensions(width=970, height=250))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="billboard_image", required=True)],
        )
        # Video asset, width 300 -- should NOT match (wrong asset type)
        display_video = _make_format(
            "d_video",
            "Display Video Unit",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[VideoFormatAsset(item_type="individual", asset_id="hero_video", required=True)],
        )
        # Image asset, width 300 -- SHOULD MATCH (type filter no longer applies)
        video_image = _make_format(
            "v_image",
            "Video Companion",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="companion_image", required=True)],
        )

        all_formats = [display_small_image, display_wide_image, display_video, video_image]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(all_formats)

            req = ListCreativeFormatsRequest(
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_impl(req=req)

        assert len(response.formats) == 2
        returned_ids = {f.format_id.id for f in response.formats}
        assert returned_ids == {"d_small", "v_image"}

    def test_combined_filters_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-16: same combined filter logic through MCP wrapper."""
        display_match = _make_format(
            "d_match",
            "Matching Display",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="banner_image", required=True)],
        )
        display_no_match = _make_format(
            "d_nomatch",
            "Non-Matching Display",
            renders=[Renders(role="primary", dimensions=Dimensions(width=970, height=250))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="wide_image", required=True)],
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([display_match, display_no_match])

            response = env.call_mcp(
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
            renders=[Renders(role="primary", dimensions=Dimensions(width=320, height=50))],
            assets=[ImageFormatAsset(item_type="individual", asset_id="mobile_image", required=True)],
        )
        audio_format = _make_format(
            "a_nomatch",
            "Audio Ad",
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([display_match, audio_format])

            req = ListCreativeFormatsRequest(
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_a2a(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "d_a2a_match"

    def test_all_filters_conjunctive_empty_result(self, integration_db):
        """UC-005-MAIN-MCP-16: if no format matches all filters, result is empty."""
        # Video asset format -- fails asset_types=["image"] filter
        only_video = _make_format(
            "v1",
            "Video Only",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[VideoFormatAsset(item_type="individual", asset_id="vid", required=True)],
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([only_video])

            req = ListCreativeFormatsRequest(
                asset_types=["image"],
                max_width=728,
            )
            response = env.call_impl(req=req)

        assert response.formats == []


# UC-005-MAIN-MCP-17 (MCP ToolResult wrapping) is graded on the wire: every BR-UC-005
# scenario parametrized over mcp reads the tool's structured_content through the
# harness's MCP leg, so no direct-call test of the wrapper is kept here.


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
            _make_format("video_preroll", "Pre-roll 15s"),
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
            assets=[ImageFormatAsset(item_type="individual", asset_id="hero", required=True)],
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
            _make_format("v1", "Video One"),
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
            )
            response_a = env_a.call_a2a(identity=identity_a)

        # Tenant B (separate env/session)
        with CreativeFormatsEnv(tenant_id="tenant_b") as env_b:
            TenantFactory(tenant_id="tenant_b")
            env_b.set_registry_formats(tenant_b_formats)

            identity_b = PrincipalFactory.make_identity(
                principal_id="buyer_b",
                tenant_id="tenant_b",
            )
            response_b = env_b.call_a2a(identity=identity_b)

        assert len(response_a.formats) == 1
        assert response_a.formats[0].format_id.id == "fmtA"
        assert len(response_b.formats) == 2
        b_ids = {f.format_id.id for f in response_b.formats}
        assert b_ids == {"fmtB1", "fmtB2"}

    # (Deleted) test_a2a_no_tenant_raises_auth_error: it built an identity with a resolved
    # principal and ``tenant=None`` and asserted the A2A wrapper refuses it. The resolver
    # never produces that pairing -- it looks a credential up only inside the tenant the
    # request reached -- and ``ResolvedIdentity`` declares ``tenant`` required, so the
    # construction itself now fails. The reachable case, an anonymous tenant-less
    # discovery request, is answered with an empty catalog by design (76c2a96fb), which
    # is the opposite of what this asserted. Its `pytest.raises((AdCPAuthenticationError,
    # Exception))` could not have told the two apart anyway: every exception matches.

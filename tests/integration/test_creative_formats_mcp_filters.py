"""Integration tests for creative formats MCP filter parameters.

Tests asset_types, name_search, and wcag_level filters using
CreativeFormatsEnv harness with real PostgreSQL.

Covers:
- salesagent-hr96: UC-005-MAIN-MCP-07 (asset_types filter)
- salesagent-vam8: UC-005-MAIN-MCP-11 (name_search case-insensitive)
- salesagent-h7wx: UC-005-MAIN-MCP-12 (wcag_level filter)
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.format import (
    Accessibility,
    Assets,
    Assets5,
    Assets9,
)
from adcp.types.generated_poc.enums.wcag_level import WcagLevel

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _fmt(
    fmt_id: str,
    name: str,
    type: str | None = "display",
    **kwargs,
) -> Format:
    """Shorthand for creating a Format object."""
    return Format(
        format_id=FormatId(agent_url=AGENT_URL, id=fmt_id),
        type=type,
        name=name,
        is_standard=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-07: asset_types filter
# ---------------------------------------------------------------------------


class TestAssetTypesFilter:
    """UC-005-MAIN-MCP-07: asset_types filter returns only matching formats.

    Covers: UC-005-MAIN-MCP-07

    BR-6: Asset type filters match formats containing at least one of the
    requested types.
    """

    def test_asset_types_image_filter(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types=[image] returns only formats with image assets."""
        formats = [
            _fmt(
                "img_banner",
                "Image Banner",
                assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
            ),
            _fmt(
                "vid_player",
                "Video Player",
                type="video",
                assets=[Assets5(asset_id="video", asset_type="video", item_type="individual", required=True)],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(asset_types=["image"])
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "img_banner"

    def test_asset_types_video_filter(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types=[video] excludes image-only formats."""
        formats = [
            _fmt(
                "img_banner",
                "Image Banner",
                assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
            ),
            _fmt(
                "vid_player",
                "Video Player",
                type="video",
                assets=[Assets5(asset_id="video", asset_type="video", item_type="individual", required=True)],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "vid_player"

    def test_asset_types_multiple_match_any(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types=[video, html] matches ANY requested type."""
        formats = [
            _fmt(
                "img_only",
                "Image Only",
                assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
            ),
            _fmt(
                "vid_player",
                "Video Player",
                type="video",
                assets=[Assets5(asset_id="video", asset_type="video", item_type="individual", required=True)],
            ),
            _fmt(
                "html_widget",
                "HTML Widget",
                assets=[Assets9(asset_id="code", asset_type="html", item_type="individual", required=True)],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(asset_types=["video", "html"])
            response = env.call_impl(req=req)

        assert len(response.formats) == 2
        ids = {f.format_id.id for f in response.formats}
        assert ids == {"vid_player", "html_widget"}

    def test_asset_types_no_match_returns_empty(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types with no matching formats returns empty."""
        formats = [
            _fmt(
                "img_banner",
                "Image Banner",
                assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)

        assert response.formats == []


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-11: name_search case-insensitive
# ---------------------------------------------------------------------------


class TestNameSearchFilter:
    """UC-005-MAIN-MCP-11: name_search is case-insensitive partial match.

    Covers: UC-005-MAIN-MCP-11

    BR-7: Name search is case-insensitive partial match.
    """

    def test_name_search_case_insensitive(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search='banner' matches 'Standard Banner 728x90'."""
        formats = [
            _fmt("banner_728", "Standard Banner 728x90"),
            _fmt("skyscraper", "Skyscraper 160x600"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="banner")
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].name == "Standard Banner 728x90"

    def test_name_search_uppercase_query(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search='BANNER' matches lowercase 'banner' in name."""
        formats = [
            _fmt("banner_300", "Medium banner 300x250"),
            _fmt("video_pre", "Video Pre-roll", type="video"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="BANNER")
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].name == "Medium banner 300x250"

    def test_name_search_mixed_case_in_name(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search matches names with mixed case."""
        formats = [
            _fmt("lb_728", "Leaderboard 728x90"),
            _fmt("lb_mobile", "Mobile LEADERBOARD"),
            _fmt("sky_160", "Skyscraper 160x600"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="leaderboard")
            response = env.call_impl(req=req)

        assert len(response.formats) == 2
        names = {f.name for f in response.formats}
        assert names == {"Leaderboard 728x90", "Mobile LEADERBOARD"}

    def test_name_search_partial_match(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search='vid' matches 'Video Pre-roll'."""
        formats = [
            _fmt("vid_pre", "Video Pre-roll", type="video"),
            _fmt("display_300", "Display 300x250"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="vid")
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].name == "Video Pre-roll"

    def test_name_search_no_match_returns_empty(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search with no matches returns empty."""
        formats = [
            _fmt("banner_728", "Standard Banner 728x90"),
            _fmt("skyscraper", "Skyscraper 160x600"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="nonexistent")
            response = env.call_impl(req=req)

        assert response.formats == []


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-12: wcag_level filter
# ---------------------------------------------------------------------------


class TestWcagLevelFilter:
    """UC-005-MAIN-MCP-12: wcag_level filter returns formats meeting at least that level.

    Covers: UC-005-MAIN-MCP-12

    BR-1: Filter semantics — hierarchical: A < AA < AAA.
    wcag_level=AA returns formats with AA or AAA.
    """

    def test_wcag_level_aa_returns_aa_and_aaa(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=AA returns formats with AA and AAA levels."""
        formats = [
            _fmt(
                "fmt_a",
                "Format Level A",
                accessibility=Accessibility(wcag_level=WcagLevel.A),
            ),
            _fmt(
                "fmt_aa",
                "Format Level AA",
                accessibility=Accessibility(wcag_level=WcagLevel.AA),
            ),
            _fmt(
                "fmt_aaa",
                "Format Level AAA",
                accessibility=Accessibility(wcag_level=WcagLevel.AAA),
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(wcag_level="AA")
            response = env.call_impl(req=req)

        assert len(response.formats) == 2
        ids = {f.format_id.id for f in response.formats}
        assert ids == {"fmt_aa", "fmt_aaa"}

    def test_wcag_level_a_returns_all_levels(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=A returns all formats with any WCAG level."""
        formats = [
            _fmt(
                "fmt_a",
                "Format Level A",
                accessibility=Accessibility(wcag_level=WcagLevel.A),
            ),
            _fmt(
                "fmt_aa",
                "Format Level AA",
                accessibility=Accessibility(wcag_level=WcagLevel.AA),
            ),
            _fmt(
                "fmt_aaa",
                "Format Level AAA",
                accessibility=Accessibility(wcag_level=WcagLevel.AAA),
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(wcag_level="A")
            response = env.call_impl(req=req)

        assert len(response.formats) == 3

    def test_wcag_level_aaa_returns_only_aaa(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=AAA returns only AAA formats."""
        formats = [
            _fmt(
                "fmt_a",
                "Format Level A",
                accessibility=Accessibility(wcag_level=WcagLevel.A),
            ),
            _fmt(
                "fmt_aa",
                "Format Level AA",
                accessibility=Accessibility(wcag_level=WcagLevel.AA),
            ),
            _fmt(
                "fmt_aaa",
                "Format Level AAA",
                accessibility=Accessibility(wcag_level=WcagLevel.AAA),
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(wcag_level="AAA")
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "fmt_aaa"

    def test_wcag_level_excludes_formats_without_accessibility(self, integration_db):
        """UC-005-MAIN-MCP-12: formats without accessibility field are excluded."""
        formats = [
            _fmt(
                "fmt_aa",
                "Format Level AA",
                accessibility=Accessibility(wcag_level=WcagLevel.AA),
            ),
            _fmt("fmt_none", "Format No Accessibility"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(wcag_level="A")
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "fmt_aa"

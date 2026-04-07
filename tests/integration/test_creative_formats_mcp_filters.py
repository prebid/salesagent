"""Integration tests for creative formats MCP filter parameters.

Tests asset_types, name_search, and wcag_level filters against the real
creative agent catalog (49 formats served by Docker container).

Real catalog stats: 28 display, 12 video, 4 dooh, 3 audio, 2 native = 49 total
Asset types in catalog: audio, html, image, javascript, text, url, vast, video
WCAG levels in catalog: none (all WCAG-filter tests verify exclusion behaviour)

Covers:
- salesagent-hr96: UC-005-MAIN-MCP-07 (asset_types filter)
- salesagent-vam8: UC-005-MAIN-MCP-11 (name_search case-insensitive)
- salesagent-h7wx: UC-005-MAIN-MCP-12 (wcag_level filter)
"""

from __future__ import annotations

import pytest

from src.core.schemas import ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


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
        """UC-005-MAIN-MCP-07: asset_types=[image] returns only formats with image assets.

        Real catalog has 16 formats with image assets.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["image"])
            response = env.call_impl(req=req)

        assert len(response.formats) > 0
        assert all(
            any(
                getattr(a, "asset_type", None) == "image"
                or any(getattr(s, "asset_type", None) == "image" for s in getattr(a, "assets", []))
                for a in (fmt.assets or [])
            )
            for fmt in response.formats
        )

    def test_asset_types_video_filter(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types=[video] excludes non-video formats.

        Real catalog has 10 formats with video assets (all video/* formats).
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)

        assert len(response.formats) > 0
        assert all(
            any(
                getattr(a, "asset_type", None) == "video"
                or any(getattr(s, "asset_type", None) == "video" for s in getattr(a, "assets", []))
                for a in (fmt.assets or [])
            )
            for fmt in response.formats
        )

    def test_asset_types_multiple_match_any(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types=[video, html] matches ANY requested type.

        Real catalog has 17 formats with video OR html assets.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["video", "html"])
            response = env.call_impl(req=req)

        assert len(response.formats) == 17
        for fmt in response.formats:
            asset_types = {getattr(a, "asset_type", None) for a in (fmt.assets or [])} | {
                getattr(s, "asset_type", None) for a in (fmt.assets or []) for s in getattr(a, "assets", [])
            }
            assert asset_types & {"video", "html"}, (
                f"Format {fmt.format_id.id} matched but has no video/html assets: {asset_types}"
            )

    def test_asset_types_no_match_returns_empty(self, integration_db):
        """UC-005-MAIN-MCP-07: asset_types with no matching formats returns empty.

        'daast' is a valid AssetContentType enum value but no real catalog format uses it.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["daast"])
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
        """UC-005-MAIN-MCP-11: name_search='banner' matches formats containing 'banner'.

        Real catalog has 6 formats with 'banner' in the name.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(name_search="banner")
            response = env.call_impl(req=req)

        assert len(response.formats) == 6
        assert all("banner" in fmt.name.lower() for fmt in response.formats)

    def test_name_search_uppercase_query(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search='BANNER' matches same formats as lowercase.

        Case-insensitive: 'BANNER' and 'banner' must return identical results.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req_lower = ListCreativeFormatsRequest(name_search="banner")
            response_lower = env.call_impl(req=req_lower)

            req_upper = ListCreativeFormatsRequest(name_search="BANNER")
            response_upper = env.call_impl(req=req_upper)

        assert len(response_upper.formats) == len(response_lower.formats)
        lower_ids = {f.format_id.id for f in response_lower.formats}
        upper_ids = {f.format_id.id for f in response_upper.formats}
        assert upper_ids == lower_ids

    def test_name_search_mixed_case_in_name(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search matches names regardless of case.

        Search 'leaderboard' — real catalog has 3 Leaderboard formats.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(name_search="leaderboard")
            response = env.call_impl(req=req)

        assert len(response.formats) == 3
        assert all("leaderboard" in fmt.name.lower() for fmt in response.formats)

    def test_name_search_partial_match(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search='vid' matches all names containing 'vid'.

        Real catalog has 10 formats with 'video' in the name.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(name_search="vid")
            response = env.call_impl(req=req)

        assert len(response.formats) == 10
        assert all("vid" in fmt.name.lower() for fmt in response.formats)

    def test_name_search_no_match_returns_empty(self, integration_db):
        """UC-005-MAIN-MCP-11: name_search with no matches returns empty."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
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

    The real catalog contains no formats with accessibility/wcag_level set.
    All wcag_level filter requests therefore return empty, which verifies
    that the filter correctly excludes formats that do not meet the
    requested accessibility standard.
    """

    def test_wcag_level_aa_returns_only_aa_and_above(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=AA excludes formats without WCAG data.

        Real catalog has no WCAG-annotated formats, so result is empty.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(wcag_level="AA")
            response = env.call_impl(req=req)

        assert response.formats == []

    def test_wcag_level_a_returns_only_accessible_formats(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=A excludes formats without any WCAG level.

        Real catalog has no WCAG-annotated formats, so result is empty.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(wcag_level="A")
            response = env.call_impl(req=req)

        assert response.formats == []

    def test_wcag_level_aaa_returns_only_aaa(self, integration_db):
        """UC-005-MAIN-MCP-12: wcag_level=AAA returns only AAA formats.

        Real catalog has no WCAG-annotated formats, so result is empty.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(wcag_level="AAA")
            response = env.call_impl(req=req)

        assert response.formats == []

    def test_wcag_level_excludes_formats_without_accessibility(self, integration_db):
        """UC-005-MAIN-MCP-12: any wcag_level filter excludes formats without accessibility field.

        Verifies the filter is strict: no accessibility data means not included.
        The full catalog (49 formats) has no accessibility field on any format,
        so any wcag_level filter produces an empty result.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            # Unfiltered gives all 49
            full_response = env.call_impl()
            # wcag_level=A gives zero
            req = ListCreativeFormatsRequest(wcag_level="A")
            filtered_response = env.call_impl(req=req)

        assert len(full_response.formats) == 49
        assert filtered_response.formats == []

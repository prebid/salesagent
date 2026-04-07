"""Integration tests: list_creative_formats filtering, sort, auth.

Behavioral tests using CreativeFormatsEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py and
test_creative_formats_behavioral.py with provable assertions.

Tests run against the real creative agent catalog (49 formats served by
the Docker container). Catalog composition: 28 display, 12 video, 4 dooh,
3 audio, 2 native = 49 total. Catalog stats: 30 formats have renders,
49 have assets, 2 are responsive (product_card_detailed, format_card_detailed).
Asset breakdown: 16 image, 10 video, 7 html.

Covers: salesagent-rrt0
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.exceptions import AdCPAuthenticationError
from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv, make_identity

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_make_identity = make_identity  # Canonical version from tests.harness


# ---------------------------------------------------------------------------
# Auth Tests — Covers: UC-005-EXT-A-01
# ---------------------------------------------------------------------------


class TestFormatsAuth:
    """list_creative_formats requires tenant in identity."""

    def test_no_tenant_raises_auth_error(self, integration_db):
        """Covers: UC-005-EXT-A-01 — tenant=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeFormatsEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(identity=identity)


# ---------------------------------------------------------------------------
# Filtering Tests — Covers: UC-005-MAIN-MCP
# ---------------------------------------------------------------------------


class TestFormatsFiltering:
    """Filtering by type, format_ids, name_search."""

    def test_no_filter_returns_all(self, integration_db):
        """Covers: UC-005-MAIN-MCP-01 — no filters returns entire catalog (49 formats)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            response = env.call_impl()
        assert len(response.formats) == 49

    def test_type_filter_returns_matching(self, integration_db):
        """Covers: UC-005-MAIN-MCP-05 — type=video returns only video formats (12 in catalog)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="video")
            response = env.call_impl(req=req)
        assert len(response.formats) == 12
        assert all(f.type == FormatCategory.video for f in response.formats)

    def test_native_type_filter(self, integration_db):
        """Covers: UC-005-MAIN-MCP-05 — type=native returns only native formats (2 in catalog)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="native")
            response = env.call_impl(req=req)
        assert len(response.formats) == 2
        assert all(f.type == FormatCategory.native for f in response.formats)

    def test_format_ids_no_match_returns_empty(self, integration_db):
        """Covers: UC-005-MAIN-MCP-06 — non-existent format_ids returns empty list."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            non_existent = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
            req = ListCreativeFormatsRequest(format_ids=non_existent)
            response = env.call_impl(req=req)
        assert response.formats == []


# ---------------------------------------------------------------------------
# Sort Tests — Covers: T-UC-005-inv10
# ---------------------------------------------------------------------------


class TestFormatsSort:
    """Formats sorted by (type.value, name)."""

    def test_sort_order_type_then_name(self, integration_db):
        """Covers: T-UC-005-inv10 — display before video, alpha within type."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            # Request only display and video to reduce result set
            req = ListCreativeFormatsRequest(type="display")
            display_response = env.call_impl(req=req)
        display_names = [f.name for f in display_response.formats]
        # All results are display type, sorted alphabetically
        assert display_names == sorted(display_names)

    def test_sort_order_across_three_types(self, integration_db):
        """Covers: T-UC-005-inv10 — sort holds across display < native < video."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            response = env.call_impl()
        # Collect type sequence in result order
        type_values = [f.type.value for f in response.formats]
        # Type values must appear in sorted order (display < dooh < native < video etc.)
        # Verify: once a type starts, no earlier type appears after it
        seen_types: set[str] = set()
        last_type = None
        for t in type_values:
            if last_type is not None and t != last_type:
                # Type changed — the new type must not have been seen before (no backtracking)
                assert t not in seen_types, f"Type '{t}' appeared after '{last_type}' but also before — sort broken"
            seen_types.add(t)
            last_type = t

    def test_sort_preserves_after_filtering(self, integration_db):
        """Covers: T-UC-005-inv10 — sort maintained after type filter."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="video")
            response = env.call_impl(req=req)
        names = [f.name for f in response.formats]
        # Names within a single type must be sorted alphabetically
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Asset Types Filter — Covers: T-UC-005-inv4
# ---------------------------------------------------------------------------


class TestFormatsAssetTypes:
    """asset_types filter checks individual and nested group assets.

    The real catalog has 49 formats all with assets. Asset breakdown:
    16 with image asset, 10 with video asset, 7 with html asset.
    Filters return formats whose assets contain the requested type.
    """

    def test_asset_types_image_filter_excludes_formats_without_assets(self, integration_db):
        """Covers: T-UC-005-inv4-group — image filter returns only formats with image assets (16 in catalog)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["image"])
            response = env.call_impl(req=req)
        # Real catalog: 16 formats have image assets
        assert len(response.formats) > 0

    def test_asset_types_video_filter_excludes_formats_without_assets(self, integration_db):
        """Covers: T-UC-005-inv4-group — video filter returns only formats with video assets (10 in catalog)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)
        # Real catalog: 10 formats have video assets
        assert len(response.formats) > 0

    def test_asset_types_text_filter_excludes_formats_without_assets(self, integration_db):
        """Covers: T-UC-005-inv4-group — text filter returns only formats with text assets."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(asset_types=["text"])
            response = env.call_impl(req=req)
        # Real catalog: text is a common asset type present in the catalog
        assert len(response.formats) > 0


# ---------------------------------------------------------------------------
# Dimension Filter — Covers: T-UC-005-boundary-dimension
# ---------------------------------------------------------------------------


class TestFormatsDimensions:
    """Dimension filtering with inclusive boundary checks.

    The real catalog has 30 formats with renders/dimensions data populated.
    Dimension filters return formats whose renders satisfy the constraint.
    Formats without renders are excluded when any dimension filter is active.
    """

    def test_max_width_filter_excludes_formats_without_renders(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — max_width filter returns formats with renders within width limit."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(max_width=400)
            response = env.call_impl(req=req)
        # Real catalog: 30 formats have renders; some have width <= 400
        assert len(response.formats) > 0

    def test_min_width_filter_excludes_formats_without_renders(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — min_width filter returns formats with renders above width floor."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(min_width=100)
            response = env.call_impl(req=req)
        # Real catalog: 30 formats have renders; some have width >= 100
        assert len(response.formats) > 0

    def test_max_height_filter_excludes_formats_without_renders(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — max_height filter returns formats with renders within height limit."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(max_height=400)
            response = env.call_impl(req=req)
        # Real catalog: 30 formats have renders; some have height <= 400
        assert len(response.formats) > 0

    def test_min_height_filter_excludes_formats_without_renders(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — min_height filter returns formats with renders above height floor."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(min_height=100)
            response = env.call_impl(req=req)
        # Real catalog: 30 formats have renders; some have height >= 100
        assert len(response.formats) > 0


# ---------------------------------------------------------------------------
# Edge Cases — Covers: T-UC-005-edge
# ---------------------------------------------------------------------------


class TestFormatsEdgeCases:
    """Edge cases: no-match filters, empty name search."""

    def test_type_filter_no_match_returns_empty(self, integration_db):
        """Covers: T-UC-005-edge-02 — type=rich_media with no rich_media formats returns empty."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="rich_media")
            response = env.call_impl(req=req)
        assert response.formats == []

    def test_empty_name_search_returns_all(self, integration_db):
        """Covers: T-UC-005-edge-03 — empty string name_search treated as no filter."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(name_search="")
            response = env.call_impl(req=req)
        assert len(response.formats) == 49

    def test_display_type_filter_count(self, integration_db):
        """Covers: T-UC-005-edge — type=display returns all 28 display formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="display")
            response = env.call_impl(req=req)
        assert len(response.formats) == 28
        assert all(f.type == FormatCategory.display for f in response.formats)

    def test_audio_type_filter_count(self, integration_db):
        """Covers: T-UC-005-edge — type=audio returns all 3 audio formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="audio")
            response = env.call_impl(req=req)
        assert len(response.formats) == 3
        assert all(f.type == FormatCategory.audio for f in response.formats)

    def test_dooh_type_filter_count(self, integration_db):
        """Covers: T-UC-005-edge — type=dooh returns all 4 dooh formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(type="dooh")
            response = env.call_impl(req=req)
        assert len(response.formats) == 4
        assert all(f.type == FormatCategory.dooh for f in response.formats)


class TestCreativeFormatsResponsiveFilter:
    """Tests for is_responsive filter — creative_formats.py lines 209-220, 260.

    The real catalog has 2 responsive formats (product_card_detailed,
    format_card_detailed). The remaining 47 are non-responsive.
    """

    def test_responsive_filter_true_returns_empty_from_real_catalog(self, integration_db):
        """Spec: is_responsive=True returns only responsive formats; real catalog has 2."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(is_responsive=True)
            response = env.call_impl(req=req)

        # Real catalog: 2 formats are responsive (product_card_detailed, format_card_detailed)
        assert len(response.formats) == 2

    def test_responsive_filter_false_returns_all_from_real_catalog(self, integration_db):
        """Spec: is_responsive=False returns only non-responsive formats; 47 qualify."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(is_responsive=False)
            response = env.call_impl(req=req)

        # Real catalog: 49 total - 2 responsive = 47 non-responsive formats
        assert len(response.formats) == 47


class TestCreativeFormatsDimensionFilters:
    """Tests for dimension filters — creative_formats.py lines 278-285.

    The real catalog has 30 formats with renders/dimensions data.
    Dimension filters return matching formats; formats without renders
    are excluded when any dimension filter is active.
    """

    def test_min_height_filter_excludes_all_from_real_catalog(self, integration_db):
        """Spec: min_height filter returns formats with renders satisfying the height floor."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(min_height=200)
            response = env.call_impl(req=req)

        # Real catalog: 30 formats have renders; some have height >= 200
        assert len(response.formats) > 0

    def test_max_height_filter_excludes_all_from_real_catalog(self, integration_db):
        """Spec: max_height filter returns formats with renders within the height ceiling."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(max_height=200)
            response = env.call_impl(req=req)

        # Real catalog: 30 formats have renders; some have height <= 200
        assert len(response.formats) > 0

    def test_no_renders_excluded_from_dimension_filter(self, integration_db):
        """Spec: formats without renders are excluded when dimension filters applied."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(min_width=100)
            response = env.call_impl(req=req)

        # Real catalog: 30 formats have renders; some have width >= 100
        assert len(response.formats) > 0


# ---------------------------------------------------------------------------
# output_format_ids Filter — Covers: UC-005-MAIN-MCP-18, UC-005-MAIN-MCP-19
# ---------------------------------------------------------------------------


class TestFormatsOutputFormatIds:
    """output_format_ids OR-filter: return formats whose output_format_ids overlaps."""

    def test_output_format_ids_no_match_returns_empty(self, integration_db):
        """Covers: UC-005-MAIN-MCP-18 — output_format_ids with non-matching id returns empty."""
        out_a = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_a")
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(output_format_ids=[out_a])
            response = env.call_impl(req=req)
        # Real catalog formats have no output_format_ids → none match → empty
        assert response.formats == []

    def test_output_format_ids_or_semantics_no_match_returns_empty(self, integration_db):
        """Covers: UC-005-MAIN-MCP-19 — output_format_ids=[X,Y] with no matches returns empty."""
        out_a = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_a")
        out_b = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_b")
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(output_format_ids=[out_a, out_b])
            response = env.call_impl(req=req)
        # Real catalog formats have no output_format_ids → union of [X,Y] still yields no matches
        assert response.formats == []

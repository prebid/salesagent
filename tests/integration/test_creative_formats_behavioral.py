"""Integration tests: list_creative_formats filtering, sort, auth.

Behavioral tests using CreativeFormatsEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py and
test_creative_formats_behavioral.py with provable assertions.

Covers: salesagent-rrt0
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.format import (
    Assets5,
    Assets18,
    Assets19,
    Assets22,
    Dimensions,
    Renders,
    Responsive,
)

from src.core.exceptions import AdCPAuthenticationError
from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv, make_identity

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_format(
    format_id: str,
    name: str,
    renders: list | None = None,
    assets: list | None = None,
    **kwargs,
) -> Format:
    """Helper to create a Format object with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


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
        """Covers: UC-005-MAIN-MCP-01 — no filters returns entire catalog."""
        formats = [
            _make_format("d1", "Display Banner"),
            _make_format("v1", "Video Pre-roll"),
            _make_format("n1", "Native Feed"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        assert len(response.formats) == 3

    def test_name_search_filter_returns_matching(self, integration_db):
        """Covers: UC-005-MAIN-MCP-05 — name_search returns matching formats."""
        formats = [
            _make_format("d1", "Display Banner"),
            _make_format("v1", "Video Pre-roll"),
            _make_format("n1", "Native Feed"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="Video")
            response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Video Pre-roll"

    def test_name_search_multiple_matches(self, integration_db):
        """Covers: UC-005-MAIN-MCP-05 — name_search with multiple matches."""
        formats = [
            _make_format("d1", "Display Banner"),
            _make_format("n1", "Native Feed"),
            _make_format("v1", "Video Pre-roll"),
            _make_format("n2", "Native Recommendation"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="Native")
            response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = {f.name for f in response.formats}
        assert names == {"Native Feed", "Native Recommendation"}

    def test_format_ids_no_match_returns_empty(self, integration_db):
        """Covers: UC-005-MAIN-MCP-06 — non-existent format_ids returns empty list."""
        formats = [
            _make_format("display_300x250", "Display 300x250"),
            _make_format("display_728x90", "Display 728x90"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            non_existent = [FormatId(agent_url=DEFAULT_AGENT_URL, id="nonexistent")]
            req = ListCreativeFormatsRequest(format_ids=non_existent)
            response = env.call_impl(req=req)
        assert response.formats == []


# ---------------------------------------------------------------------------
# Sort Tests — Covers: T-UC-005-inv10
# ---------------------------------------------------------------------------


class TestFormatsSort:
    """Formats sorted by name."""

    def test_sort_order_by_name(self, integration_db):
        """Covers: T-UC-005-inv10 — formats sorted alphabetically by name."""
        formats = [
            _make_format("v_zebra", "Zebra Ad"),
            _make_format("d_alpha", "Alpha Banner"),
            _make_format("v_alpha", "Alpha Video"),
            _make_format("d_zebra", "Zebra Banner"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        names = [f.name for f in response.formats]
        assert names == sorted(names)

    def test_sort_order_across_all_formats(self, integration_db):
        """Covers: T-UC-005-inv10 — sort holds across all formats."""
        formats = [
            _make_format("n1", "Native B"),
            _make_format("d1", "Display A"),
            _make_format("v1", "Video C"),
            _make_format("n2", "Native A"),
            _make_format("d2", "Display B"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        names = [f.name for f in response.formats]
        assert names == sorted(names)

    def test_sort_preserves_after_filtering(self, integration_db):
        """Covers: T-UC-005-inv10 — sort maintained after name_search filter."""
        formats = [
            _make_format("v2", "Zebra Video"),
            _make_format("v1", "Alpha Video"),
            _make_format("d1", "Display Ad"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="Video")
            response = env.call_impl(req=req)
        names = [f.name for f in response.formats]
        assert names == ["Alpha Video", "Zebra Video"]


# ---------------------------------------------------------------------------
# Asset Types Filter — Covers: T-UC-005-inv4
# ---------------------------------------------------------------------------


class TestFormatsAssetTypes:
    """asset_types filter checks individual and nested group assets."""

    def test_group_assets_match(self, integration_db):
        """Covers: T-UC-005-inv4-group — group assets with image match image filter."""
        group_asset = Assets18(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=True,
            min_count=1,
            max_count=5,
            assets=[
                Assets19(asset_id="product_image", required=True),
                Assets22(asset_id="product_title", required=True),
            ],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="native_carousel"),
            name="Native Carousel",
            is_standard=True,
            assets=[group_asset],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([fmt])
            req = ListCreativeFormatsRequest(asset_types=["image"])
            response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Native Carousel"

    def test_group_assets_no_match_excluded(self, integration_db):
        """Covers: T-UC-005-inv4-group — group with only text excluded by video filter."""
        group_asset = Assets18(
            item_type="repeatable_group",
            asset_group_id="text_group",
            required=True,
            min_count=1,
            max_count=3,
            assets=[Assets22(asset_id="headline", required=True)],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="text_only"),
            name="Text Only Native",
            is_standard=True,
            assets=[group_asset],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([fmt])
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)
        assert response.formats == []

    def test_mixed_individual_and_group_assets(self, integration_db):
        """Covers: T-UC-005-inv4-group — mixed format matches both asset types."""
        individual = Assets5(item_type="individual", asset_id="hero_video", required=True)
        group = Assets18(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=False,
            min_count=0,
            max_count=5,
            assets=[Assets19(asset_id="product_image", required=True)],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="mixed"),
            name="Mixed Format",
            is_standard=True,
            assets=[individual, group],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([fmt])

            # image matches via group
            req = ListCreativeFormatsRequest(asset_types=["image"])
            response = env.call_impl(req=req)
            assert len(response.formats) == 1

            # video matches via individual
            req = ListCreativeFormatsRequest(asset_types=["video"])
            response = env.call_impl(req=req)
            assert len(response.formats) == 1

            # html matches neither
            req = ListCreativeFormatsRequest(asset_types=["html"])
            response = env.call_impl(req=req)
            assert response.formats == []


# ---------------------------------------------------------------------------
# Dimension Filter — Covers: T-UC-005-boundary-dimension
# ---------------------------------------------------------------------------


class TestFormatsDimensions:
    """Dimension filtering with inclusive boundary checks."""

    def test_exact_max_width_included(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — width=300 included by max_width=300."""
        formats = [
            _make_format(
                "rect",
                "Medium Rectangle",
                renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(max_width=300)
            response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Medium Rectangle"

    def test_off_by_one_max_width_excluded(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — width=301 excluded by max_width=300."""
        formats = [
            _make_format(
                "wide",
                "Slightly Wide",
                renders=[Renders(role="primary", dimensions=Dimensions(width=301, height=250))],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(max_width=300)
            response = env.call_impl(req=req)
        assert response.formats == []

    def test_exact_min_width_included(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — width=300 included by min_width=300."""
        formats = [
            _make_format(
                "rect",
                "Medium Rectangle",
                renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(min_width=300)
            response = env.call_impl(req=req)
        assert len(response.formats) == 1

    def test_off_by_one_min_width_excluded(self, integration_db):
        """Covers: T-UC-005-boundary-dimension — width=299 excluded by min_width=300."""
        formats = [
            _make_format(
                "narrow",
                "Slightly Narrow",
                renders=[Renders(role="primary", dimensions=Dimensions(width=299, height=250))],
            ),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(min_width=300)
            response = env.call_impl(req=req)
        assert response.formats == []


# ---------------------------------------------------------------------------
# Edge Cases — Covers: T-UC-005-edge
# ---------------------------------------------------------------------------


class TestFormatsEdgeCases:
    """Edge cases: empty registry, no-match filters, empty name search."""

    def test_empty_registry_returns_empty(self, integration_db):
        """Covers: T-UC-005-edge-01 — empty format catalog returns empty list."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])
            response = env.call_impl()
        assert response.formats == []

    def test_name_search_no_match_returns_empty(self, integration_db):
        """Covers: T-UC-005-edge-02 — name_search with no matches returns empty."""
        formats = [
            _make_format("d1", "Display Banner"),
            _make_format("d2", "Display Rectangle"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="nonexistent_xyz")
            response = env.call_impl(req=req)
        assert response.formats == []

    def test_empty_name_search_returns_all(self, integration_db):
        """Covers: T-UC-005-edge-03 — empty string name_search treated as no filter."""
        formats = [
            _make_format("d1", "Alpha Display"),
            _make_format("v1", "Beta Video"),
            _make_format("n1", "Gamma Native"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="")
            response = env.call_impl(req=req)
        assert len(response.formats) == 3


class TestCreativeFormatsResponsiveFilter:
    """Tests for is_responsive filter — creative_formats.py lines 209-220, 260."""

    def test_responsive_filter_true(self, integration_db):
        """Spec: is_responsive=True returns only responsive formats."""
        responsive_format = _make_format(
            "resp1",
            "Responsive Banner",
            renders=[
                Renders(
                    role="primary",
                    dimensions=Dimensions(
                        width=300,
                        height=250,
                        responsive=Responsive(width=True, height=False),
                    ),
                )
            ],
        )
        fixed_format = _make_format(
            "fixed1",
            "Fixed Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([responsive_format, fixed_format])
            req = ListCreativeFormatsRequest(is_responsive=True)
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "resp1"

    def test_responsive_filter_false(self, integration_db):
        """Spec: is_responsive=False returns only non-responsive formats."""
        responsive_format = _make_format(
            "resp1",
            "Responsive Banner",
            renders=[
                Renders(
                    role="primary",
                    dimensions=Dimensions(
                        width=300,
                        height=250,
                        responsive=Responsive(width=True, height=False),
                    ),
                )
            ],
        )
        fixed_format = _make_format(
            "fixed1",
            "Fixed Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([responsive_format, fixed_format])
            req = ListCreativeFormatsRequest(is_responsive=False)
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "fixed1"


class TestCreativeFormatsDimensionFilters:
    """Tests for dimension filters — creative_formats.py lines 278-285."""

    def test_min_height_filter(self, integration_db):
        """Spec: min_height filter returns formats with height >= threshold."""
        tall = _make_format(
            "tall1",
            "Tall Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=160, height=600))],
        )
        short = _make_format(
            "short1",
            "Short Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([tall, short])
            req = ListCreativeFormatsRequest(min_height=200)
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "tall1"

    def test_max_height_filter(self, integration_db):
        """Spec: max_height filter returns formats with height <= threshold."""
        tall = _make_format(
            "tall1",
            "Tall Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=160, height=600))],
        )
        short = _make_format(
            "short1",
            "Short Banner",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([tall, short])
            req = ListCreativeFormatsRequest(max_height=200)
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "short1"

    def test_no_renders_excluded_from_dimension_filter(self, integration_db):
        """Spec: formats without renders are excluded when dimension filters applied."""
        no_renders = _make_format("nr1", "No Renders Format")
        with_renders = _make_format(
            "wr1",
            "With Renders",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        )
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([no_renders, with_renders])
            req = ListCreativeFormatsRequest(min_width=100)
            response = env.call_impl(req=req)

        assert len(response.formats) == 1
        assert response.formats[0].format_id.id == "wr1"


# ---------------------------------------------------------------------------
# output_format_ids Filter — Covers: UC-005-MAIN-MCP-18, UC-005-MAIN-MCP-19
# Regression for beads-xlu: filter NOT implemented yet — this test must FAIL
# ---------------------------------------------------------------------------


class TestFormatsOutputFormatIds:
    """output_format_ids OR-filter: return formats whose output_format_ids overlaps."""

    def test_output_format_ids_single_match(self, integration_db):
        """Covers: UC-005-MAIN-MCP-18 — output_format_ids=[X] returns formats that produce X."""
        out_a = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_a")
        out_b = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_b")
        formats = [
            _make_format("fmt_1", "Multi-output A"),
            _make_format("fmt_2", "Multi-output B"),
            _make_format("fmt_3", "No output ids"),
        ]
        formats[0].output_format_ids = [out_a]
        formats[1].output_format_ids = [out_b]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(output_format_ids=[out_a])
            response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Multi-output A"

    def test_output_format_ids_or_semantics(self, integration_db):
        """Covers: UC-005-MAIN-MCP-19 — output_format_ids=[X,Y] returns union (OR semantics)."""
        out_a = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_a")
        out_b = FormatId(agent_url=DEFAULT_AGENT_URL, id="out_b")
        formats = [
            _make_format("fmt_1", "Produces A"),
            _make_format("fmt_2", "Produces B"),
            _make_format("fmt_3", "No output ids"),
        ]
        formats[0].output_format_ids = [out_a]
        formats[1].output_format_ids = [out_b]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(output_format_ids=[out_a, out_b])
            response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = {f.name for f in response.formats}
        assert names == {"Produces A", "Produces B"}

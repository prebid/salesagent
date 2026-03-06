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
    Assets16,
    Dimensions,
    Renders,
)
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from src.core.testing_hooks import AdCPTestContext
from tests.factories import TenantFactory
from tests.harness.creative_formats import CreativeFormatsEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_format(
    format_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Helper to create a Format object with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        type=type,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


def _make_identity(principal_id=None, tenant_id=None, tenant=None, **kwargs):
    """Build a ResolvedIdentity with explicit control over all fields."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id or "test_tenant",
        tenant=tenant,
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        **kwargs,
    )


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
# Filtering Tests — Covers: UC-005-FILTER
# ---------------------------------------------------------------------------


class TestFormatsFiltering:
    """Filtering by type, format_ids, name_search."""

    def test_no_filter_returns_all(self, integration_db):
        """Covers: UC-005-FILTER-01 — no filters returns entire catalog."""
        formats = [
            _make_format("d1", "Display Banner"),
            _make_format("v1", "Video Pre-roll", type=FormatCategory.video),
            _make_format("n1", "Native Feed", type=FormatCategory.native),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        assert len(response.formats) == 3

    def test_type_filter_returns_matching(self, integration_db):
        """Covers: UC-005-FILTER-02 — type=video returns only video formats."""
        formats = [
            _make_format("d1", "Display Banner", type=FormatCategory.display),
            _make_format("v1", "Video Pre-roll", type=FormatCategory.video),
            _make_format("n1", "Native Feed", type=FormatCategory.native),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(type="video")
            response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Video Pre-roll"

    def test_native_type_filter(self, integration_db):
        """Covers: UC-005-FILTER-03 — type=native returns only native formats."""
        formats = [
            _make_format("d1", "Display Banner", type=FormatCategory.display),
            _make_format("n1", "Native Feed", type=FormatCategory.native),
            _make_format("v1", "Video Pre-roll", type=FormatCategory.video),
            _make_format("n2", "Native Recommendation", type=FormatCategory.native),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(type="native")
            response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = {f.name for f in response.formats}
        assert names == {"Native Feed", "Native Recommendation"}

    def test_format_ids_no_match_returns_empty(self, integration_db):
        """Covers: UC-005-FILTER-04 — non-existent format_ids returns empty list."""
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
    """Formats sorted by (type.value, name)."""

    def test_sort_order_type_then_name(self, integration_db):
        """Covers: T-UC-005-inv10 — display before video, alpha within type."""
        formats = [
            _make_format("v_zebra", "Zebra Ad", type=FormatCategory.video),
            _make_format("d_alpha", "Alpha Banner", type=FormatCategory.display),
            _make_format("v_alpha", "Alpha Video", type=FormatCategory.video),
            _make_format("d_zebra", "Zebra Banner", type=FormatCategory.display),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        names = [f.name for f in response.formats]
        assert names == [
            "Alpha Banner",
            "Zebra Banner",
            "Alpha Video",
            "Zebra Ad",
        ]

    def test_sort_order_across_three_types(self, integration_db):
        """Covers: T-UC-005-inv10 — sort holds across display < native < video."""
        formats = [
            _make_format("n1", "Native B", type=FormatCategory.native),
            _make_format("d1", "Display A", type=FormatCategory.display),
            _make_format("v1", "Video C", type=FormatCategory.video),
            _make_format("n2", "Native A", type=FormatCategory.native),
            _make_format("d2", "Display B", type=FormatCategory.display),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()
        names = [f.name for f in response.formats]
        assert names == [
            "Display A",
            "Display B",
            "Native A",
            "Native B",
            "Video C",
        ]

    def test_sort_preserves_after_filtering(self, integration_db):
        """Covers: T-UC-005-inv10 — sort maintained after type filter."""
        formats = [
            _make_format("v2", "Zebra Video", type=FormatCategory.video),
            _make_format("v1", "Alpha Video", type=FormatCategory.video),
            _make_format("d1", "Display Ad", type=FormatCategory.display),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(type="video")
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
        from adcp.types.generated_poc.core.format import Assets17, Assets20

        group_asset = Assets16(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=True,
            min_count=1,
            max_count=5,
            assets=[
                Assets17(asset_id="product_image", required=True),
                Assets20(asset_id="product_title", required=True),
            ],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="native_carousel"),
            name="Native Carousel",
            type=FormatCategory.native,
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
        from adcp.types.generated_poc.core.format import Assets20

        group_asset = Assets16(
            item_type="repeatable_group",
            asset_group_id="text_group",
            required=True,
            min_count=1,
            max_count=3,
            assets=[Assets20(asset_id="headline", required=True)],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="text_only"),
            name="Text Only Native",
            type=FormatCategory.native,
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
        from adcp.types.generated_poc.core.format import Assets17

        individual = Assets5(item_type="individual", asset_id="hero_video", required=True)
        group = Assets16(
            item_type="repeatable_group",
            asset_group_id="product_group",
            required=False,
            min_count=0,
            max_count=5,
            assets=[Assets17(asset_id="product_image", required=True)],
        )
        fmt = Format(
            format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id="mixed"),
            name="Mixed Format",
            type=FormatCategory.display,
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

    def test_type_filter_no_match_returns_empty(self, integration_db):
        """Covers: T-UC-005-edge-02 — type=audio with no audio formats returns empty."""
        formats = [
            _make_format("d1", "Display Banner", type=FormatCategory.display),
            _make_format("d2", "Display Rectangle", type=FormatCategory.display),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(type="audio")
            response = env.call_impl(req=req)
        assert response.formats == []

    def test_empty_name_search_returns_all(self, integration_db):
        """Covers: T-UC-005-edge-03 — empty string name_search treated as no filter."""
        formats = [
            _make_format("d1", "Alpha Display"),
            _make_format("v1", "Beta Video", type=FormatCategory.video),
            _make_format("n1", "Gamma Native", type=FormatCategory.native),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(name_search="")
            response = env.call_impl(req=req)
        assert len(response.formats) == 3

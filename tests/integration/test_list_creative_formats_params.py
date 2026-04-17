"""Integration tests for list_creative_formats filtering parameters.

Tests the full filtering logic using CreativeFormatsEnv harness with real PostgreSQL.
Each test sets up mock format data via the registry and verifies filtering behavior.

Refactored from raw ToolContext + manual patches to harness pattern.
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.format import (
    Assets,
    Assets5,
    Assets9,
    Dimensions,
    Renders,
    Responsive,
)

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_list_creative_formats_request_minimal():
    """Test that ListCreativeFormatsRequest works with no params (all defaults)."""
    req = ListCreativeFormatsRequest()
    assert req.format_ids is None


def test_list_creative_formats_request_with_all_params():
    """Test that ListCreativeFormatsRequest accepts all optional filter parameters."""
    format_ids = [
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_16x9"),
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_4x3"),
    ]
    req = ListCreativeFormatsRequest(
        format_ids=format_ids,
        is_responsive=True,
        name_search="video",
        min_width=640,
        max_height=480,
    )
    assert len(req.format_ids) == 2
    assert req.format_ids[0].id == "video_16x9"
    assert req.format_ids[1].id == "video_4x3"
    assert req.is_responsive is True
    assert req.name_search == "video"
    assert req.min_width == 640
    assert req.max_height == 480


AGENT_URL = "https://creative.adcontextprotocol.org"


def _fmt(fmt_id: str, name: str, **kwargs) -> Format:
    """Shorthand for creating a Format object."""
    return Format(
        format_id=FormatId(agent_url=AGENT_URL, id=fmt_id),
        name=name,
        is_standard=kwargs.pop("is_standard", True),
        **kwargs,
    )


def test_filtering_by_type(integration_db):
    """Test that type filter works correctly."""
    formats = [
        _fmt("video_16x9", "Video 16:9"),
        _fmt("display_300x250", "Display 300x250"),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)
        req = ListCreativeFormatsRequest(name_search="Video")
        response = env.call_impl(req=req)

    assert len(response.formats) == 1
    assert response.formats[0].format_id.id == "video_16x9"


def test_filtering_by_format_ids(integration_db):
    """Test that format_ids filter works correctly."""
    formats = [
        _fmt("display_300x250", "Display 300x250"),
        _fmt("display_728x90", "Display 728x90"),
        _fmt("video_16x9", "Video 16:9"),
    ]
    target_ids = [
        FormatId(agent_url=AGENT_URL, id="display_300x250"),
        FormatId(agent_url=AGENT_URL, id="display_728x90"),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)
        req = ListCreativeFormatsRequest(format_ids=target_ids)
        response = env.call_impl(req=req)

    assert len(response.formats) == 2
    returned_ids = {f.format_id.id for f in response.formats}
    assert returned_ids == {"display_300x250", "display_728x90"}


def test_filtering_combined(integration_db):
    """Test that multiple filters work together.

    The type filter was removed in adcp 3.12, so min_width=500
    now returns all formats with width >= 500 regardless of type.
    """
    formats = [
        _fmt(
            "display_300x250",
            "Display 300x250",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        ),
        _fmt(
            "display_728x90",
            "Display 728x90",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
        ),
        _fmt(
            "video_16x9",
            "Video 16:9",
            renders=[Renders(role="primary", dimensions=Dimensions(width=640, height=360))],
        ),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)
        req = ListCreativeFormatsRequest(min_width=500)
        response = env.call_impl(req=req)

    assert len(response.formats) == 2
    names = {f.name for f in response.formats}
    assert names == {"Display 728x90", "Video 16:9"}


def test_filtering_by_is_responsive(integration_db):
    """Test that is_responsive filter returns only responsive/non-responsive formats."""
    formats = [
        _fmt(
            "responsive_banner",
            "Responsive Banner",
            renders=[
                Renders(
                    role="primary",
                    dimensions=Dimensions(
                        min_width=300, max_width=970, height=250, responsive=Responsive(width=True, height=False)
                    ),
                )
            ],
        ),
        _fmt(
            "fixed_300x250",
            "Fixed 300x250",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        ),
        _fmt("no_renders", "No Renders"),  # No renders
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)

        # is_responsive=True
        req = ListCreativeFormatsRequest(is_responsive=True)
        response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Responsive Banner"

        # is_responsive=False
        req = ListCreativeFormatsRequest(is_responsive=False)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Fixed 300x250" in names
        assert "No Renders" in names


def test_filtering_by_name_search(integration_db):
    """Test that name_search filter performs case-insensitive partial match."""
    formats = [
        _fmt("leaderboard_728x90", "Leaderboard 728x90"),
        _fmt("mobile_leaderboard", "Mobile LEADERBOARD"),
        _fmt("skyscraper", "Skyscraper 160x600"),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)

        # Search for "leaderboard" (case-insensitive)
        req = ListCreativeFormatsRequest(name_search="leaderboard")
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Leaderboard 728x90" in names
        assert "Mobile LEADERBOARD" in names

        # Search with no matches
        req = ListCreativeFormatsRequest(name_search="nonexistent")
        response = env.call_impl(req=req)
        assert len(response.formats) == 0


def test_filtering_by_asset_types(integration_db):
    """Test that asset_types filter returns formats supporting any of the requested types."""
    formats = [
        _fmt(
            "image_banner",
            "Image Banner",
            assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
        ),
        _fmt(
            "video_player",
            "Video Player",
            assets=[Assets5(asset_id="video", asset_type="video", item_type="individual", required=True)],
        ),
        _fmt(
            "rich_media",
            "Rich Media",
            assets=[
                Assets(asset_id="image", asset_type="image", item_type="individual", required=True),
                Assets9(asset_id="code", asset_type="html", item_type="individual", required=True),
            ],
        ),
        _fmt("no_assets", "No Asset Types"),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)

        # Filter for image formats
        req = ListCreativeFormatsRequest(asset_types=["image"])
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Image Banner" in names
        assert "Rich Media" in names

        # Filter for multiple asset types (matches ANY)
        req = ListCreativeFormatsRequest(asset_types=["video", "html"])
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Video Player" in names
        assert "Rich Media" in names


def test_filtering_by_dimensions(integration_db):
    """Test that dimension filters correctly include/exclude formats."""
    formats = [
        _fmt(
            "medium_rectangle",
            "Medium Rectangle",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
        ),
        _fmt(
            "leaderboard", "Leaderboard", renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))]
        ),
        _fmt(
            "skyscraper", "Skyscraper", renders=[Renders(role="primary", dimensions=Dimensions(width=160, height=600))]
        ),
        _fmt("no_renders", "No Renders"),  # Excluded by dimension filters
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)

        # Filter by min_width
        req = ListCreativeFormatsRequest(min_width=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Medium Rectangle" in names
        assert "Leaderboard" in names

        # Filter by max_width
        req = ListCreativeFormatsRequest(max_width=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Medium Rectangle" in names
        assert "Skyscraper" in names

        # Filter by height range
        req = ListCreativeFormatsRequest(min_height=200, max_height=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 1
        assert response.formats[0].name == "Medium Rectangle"

        # Combine width and height filters
        req = ListCreativeFormatsRequest(min_width=100, max_width=400, min_height=200, max_height=700)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Medium Rectangle" in names
        assert "Skyscraper" in names


def test_new_filters_combined_with_existing(integration_db):
    """Test that new filters work correctly with existing filters."""
    formats = [
        _fmt(
            "display_300x250",
            "Display 300x250",
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
        ),
        _fmt(
            "display_728x90",
            "Display 728x90",
            renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
            assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
        ),
        _fmt(
            "video_16x9",
            "Video 16:9",
            renders=[Renders(role="primary", dimensions=Dimensions(width=640, height=360))],
            assets=[Assets5(asset_id="video", asset_type="video", item_type="individual", required=True)],
        ),
        _fmt(
            "custom_display",
            "Custom Display",
            is_standard=False,
            renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
            assets=[Assets(asset_id="main", asset_type="image", item_type="individual", required=True)],
        ),
    ]
    # Override agent_url for custom format
    formats[3].format_id = FormatId(agent_url="https://custom.example.com", id="custom_display")

    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        env.set_registry_formats(formats)

        # Combine dimension filter (type filter removed in adcp 3.12)
        req = ListCreativeFormatsRequest(min_width=500)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Display 728x90" in names
        assert "Video 16:9" in names

        # Combine name_search + dimension
        req = ListCreativeFormatsRequest(name_search="display", max_width=400)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Display 300x250" in names
        assert "Custom Display" in names

        # Combine type + asset_types + dimensions
        req = ListCreativeFormatsRequest(asset_types=["image"], max_width=400)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        names = [f.name for f in response.formats]
        assert "Display 300x250" in names
        assert "Custom Display" in names

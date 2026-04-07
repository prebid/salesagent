"""Integration tests for list_creative_formats filtering parameters.

Tests the full filtering logic using CreativeFormatsEnv harness with the real
creative agent catalog (49 formats: 28 display, 12 video, 4 dooh, 3 audio, 2 native).

Each test exercises a specific filter parameter against the real catalog served
by the Docker-based creative agent container.

Requires: Docker stack running (creative agent + Postgres).
"""

from __future__ import annotations

import pytest

from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_list_creative_formats_request_minimal():
    """Test that ListCreativeFormatsRequest works with no params (all defaults)."""
    req = ListCreativeFormatsRequest()
    assert req.type is None
    assert req.format_ids is None


def test_list_creative_formats_request_with_all_params():
    """Test that ListCreativeFormatsRequest accepts all optional filter parameters."""
    from adcp.types import FormatCategory

    format_ids = [
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_standard"),
        FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_vast"),
    ]
    req = ListCreativeFormatsRequest(
        type="video",
        format_ids=format_ids,
        is_responsive=True,
        name_search="video",
        min_width=640,
        max_height=480,
    )
    assert req.type == FormatCategory.video or req.type.value == "video"
    assert len(req.format_ids) == 2
    assert req.format_ids[0].id == "video_standard"
    assert req.format_ids[1].id == "video_vast"
    assert req.is_responsive is True
    assert req.name_search == "video"
    assert req.min_width == 640
    assert req.max_height == 480


AGENT_URL = "https://creative.adcontextprotocol.org"


def test_filtering_by_type(integration_db):
    """Test that type filter returns only formats of the requested type.

    The real catalog has 12 video formats.
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        req = ListCreativeFormatsRequest(type="video")
        response = env.call_impl(req=req)

    assert len(response.formats) == 12
    assert all(f.type.value == "video" for f in response.formats)


def test_filtering_by_format_ids(integration_db):
    """Test that format_ids filter returns only the requested formats.

    Requesting two known display format IDs from the real catalog.
    """
    target_ids = [
        FormatId(agent_url=AGENT_URL, id="display_300x250_image"),
        FormatId(agent_url=AGENT_URL, id="display_728x90_image"),
    ]
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        req = ListCreativeFormatsRequest(format_ids=target_ids)
        response = env.call_impl(req=req)

    assert len(response.formats) == 2
    returned_ids = {f.format_id.id for f in response.formats}
    assert returned_ids == {"display_300x250_image", "display_728x90_image"}


def test_filtering_combined(integration_db):
    """Test that type + dimension filters work together.

    type='display' AND min_width=500 matches display formats with width >= 500:
    display_728x90_generative, display_970x250_generative,
    display_728x90_image, display_970x250_image,
    display_728x90_html, display_970x250_html — 6 formats total.
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")
        req = ListCreativeFormatsRequest(type="display", min_width=500)
        response = env.call_impl(req=req)

    assert len(response.formats) == 6
    returned_ids = {f.format_id.id for f in response.formats}
    assert "display_728x90_image" in returned_ids
    assert "display_970x250_image" in returned_ids
    # All returned formats must be display type
    assert all(f.type.value == "display" for f in response.formats)


def test_filtering_by_is_responsive(integration_db):
    """Test that is_responsive filter returns only responsive/non-responsive formats.

    The real catalog has exactly 2 truly responsive formats (responsive.width=True
    or responsive.height=True): product_card_detailed and format_card_detailed.
    is_responsive=False returns all non-responsive formats (47 = 49 total - 2 responsive).
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")

        # is_responsive=True: only product_card_detailed and format_card_detailed
        req = ListCreativeFormatsRequest(is_responsive=True)
        response = env.call_impl(req=req)
        assert len(response.formats) == 2
        responsive_ids = {f.format_id.id for f in response.formats}
        assert "product_card_detailed" in responsive_ids
        assert "format_card_detailed" in responsive_ids

        # is_responsive=False: all non-responsive formats (49 - 2 = 47)
        req = ListCreativeFormatsRequest(is_responsive=False)
        response = env.call_impl(req=req)
        assert len(response.formats) == 47


def test_filtering_by_name_search(integration_db):
    """Test that name_search filter performs case-insensitive partial match.

    The real catalog has 3 formats with 'leaderboard' in the name:
    'Leaderboard - AI Generated', 'Leaderboard - Image', 'Leaderboard - HTML5'.
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")

        # Search for "leaderboard" (case-insensitive) — 3 matches in real catalog
        req = ListCreativeFormatsRequest(name_search="leaderboard")
        response = env.call_impl(req=req)
        assert len(response.formats) == 3
        names = {f.name for f in response.formats}
        assert "Leaderboard - AI Generated" in names
        assert "Leaderboard - Image" in names
        assert "Leaderboard - HTML5" in names

        # Search with no matches
        req = ListCreativeFormatsRequest(name_search="nonexistent_zzz")
        response = env.call_impl(req=req)
        assert len(response.formats) == 0


def test_filtering_by_asset_types(integration_db):
    """Test that asset_types filter returns formats supporting any of the requested types.

    From the real catalog:
    - image: 16 formats (display_*_image, dooh_*, native_*, product_card_standard)
    - video: 10 formats (video_standard, video_dimensions, video_1*x*, video_ctv_*)
    - html: 7 formats (display_*_html, display_html)
    Combined video|html: 17 formats (no overlap).
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")

        # Filter for image formats — 16 in real catalog
        req = ListCreativeFormatsRequest(asset_types=["image"])
        response = env.call_impl(req=req)
        assert len(response.formats) == 16

        # Filter for video OR html asset types — 17 formats (10 video + 7 html, no overlap)
        req = ListCreativeFormatsRequest(asset_types=["video", "html"])
        response = env.call_impl(req=req)
        assert len(response.formats) == 17


def test_filtering_by_dimensions(integration_db):
    """Test that dimension filters correctly include/exclude formats.

    Real catalog dimension facts:
    - Formats with width >= 300: 25 (excludes display_160x600_* and no-render formats)
    - Formats with width <= 300: 11 (includes 300-wide and 160-wide formats + product/format cards)
    - Formats with height in [200,300]: 9 (250-height and 280-height formats)
    - min=100..400, min_h=200, max_h=700: 14 formats
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")

        # Filter by min_width=300 — 25 formats have at least one render with width >= 300
        req = ListCreativeFormatsRequest(min_width=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 25

        # Filter by max_width=300 — 11 formats have at least one render with width <= 300
        req = ListCreativeFormatsRequest(max_width=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 11
        returned_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250_image" in returned_ids
        assert "display_160x600_image" in returned_ids

        # Filter by height range [200, 300] — 9 formats
        req = ListCreativeFormatsRequest(min_height=200, max_height=300)
        response = env.call_impl(req=req)
        assert len(response.formats) == 9

        # Combine width and height filters
        req = ListCreativeFormatsRequest(min_width=100, max_width=400, min_height=200, max_height=700)
        response = env.call_impl(req=req)
        assert len(response.formats) == 14
        returned_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250_image" in returned_ids
        assert "display_160x600_image" in returned_ids


def test_new_filters_combined_with_existing(integration_db):
    """Test that multiple filters work correctly in combination.

    Uses real catalog data:
    - type='display' + min_width=500: 6 formats
    - type='display' + asset_types=['image'] + max_width=400: 6 formats
    """
    with CreativeFormatsEnv() as env:
        TenantFactory(tenant_id="test_tenant")

        # Combine type + dimension
        req = ListCreativeFormatsRequest(type="display", min_width=500)
        response = env.call_impl(req=req)
        assert len(response.formats) == 6
        returned_ids = {f.format_id.id for f in response.formats}
        assert "display_728x90_image" in returned_ids
        assert "display_970x250_image" in returned_ids

        # Combine type + asset_types + dimensions
        # type=display, asset_types=[image], max_width=400 → 6 formats:
        # display_300x250_image, display_320x50_image, display_160x600_image,
        # display_336x280_image, display_300x600_image, product_card_standard
        req = ListCreativeFormatsRequest(type="display", asset_types=["image"], max_width=400)
        response = env.call_impl(req=req)
        assert len(response.formats) == 6
        returned_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250_image" in returned_ids
        assert "product_card_standard" in returned_ids

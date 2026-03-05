"""Integration test for get_products filtering with FormatId objects.

This test verifies that the format_ids filter in ProductFilters correctly handles
FormatId objects with .id attribute (not .format_id).

Regression test for: "unhashable type: 'FormatReference'" bug.

MIGRATED: Uses ProductEnv harness + factory-based setup.
"""

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def product_env(integration_db):
    """ProductEnv with two products: display and video formats."""
    with ProductEnv(tenant_id="fmt-filter-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="fmt-filter-test")
        PrincipalFactory(
            tenant=tenant,
            principal_id="test_principal",
        )

        display_product = ProductFactory(
            tenant=tenant,
            product_id="display_product",
            name="Display Product",
            description="Has display formats",
            delivery_type="guaranteed",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
        )
        PricingOptionFactory(
            product=display_product,
            pricing_model="cpm",
            rate="15.00",
            is_fixed=True,
            currency="USD",
        )

        video_product = ProductFactory(
            tenant=tenant,
            product_id="video_product",
            name="Video Product",
            description="Has video formats",
            delivery_type="guaranteed",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_1280x720"},
            ],
        )
        PricingOptionFactory(
            product=video_product,
            pricing_model="cpm",
            rate="20.00",
            is_fixed=True,
            currency="USD",
        )

        yield env


@pytest.mark.asyncio
async def test_filter_by_format_ids_with_formatid_objects(product_env):
    """Test that filtering by format_ids works with FormatId objects.

    This is the actual code path that was broken - when a client sends:
    filters: {
      format_ids: [
        {agent_url: "https://...", id: "display_300x250"}
      ]
    }

    The server was checking for .format_id attribute instead of .id attribute.
    """
    result = await product_env.call_impl(
        brief="display ads",
        filters={"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]},
    )

    assert len(result.products) == 1
    assert result.products[0].product_id == "display_product"

    # Verify the product has the requested format
    product_format_ids = []
    for fmt in result.products[0].format_ids:
        if isinstance(fmt, dict):
            product_format_ids.append(fmt.get("id"))
        elif hasattr(fmt, "id"):
            product_format_ids.append(fmt.id)
        elif isinstance(fmt, str):
            product_format_ids.append(fmt)

    assert "display_300x250" in product_format_ids


@pytest.mark.asyncio
async def test_filter_by_format_ids_no_matches(product_env):
    """Test that filtering returns empty when no products match."""
    result = await product_env.call_impl(
        brief="audio ads",
        filters={"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_30s"}]},
    )

    assert len(result.products) == 0


@pytest.mark.asyncio
async def test_filter_by_format_ids_video_format(product_env):
    """Test filtering for video format returns correct product."""
    result = await product_env.call_impl(
        brief="video ads",
        filters={"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_1280x720"}]},
    )

    assert len(result.products) == 1
    assert result.products[0].product_id == "video_product"


@pytest.mark.asyncio
async def test_filter_by_multiple_format_ids(product_env):
    """Test filtering with multiple format IDs returns products matching any."""
    result = await product_env.call_impl(
        brief="all ads",
        filters={
            "format_ids": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_1280x720"},
            ]
        },
    )

    assert len(result.products) == 2
    product_ids = {p.product_id for p in result.products}
    assert "display_product" in product_ids
    assert "video_product" in product_ids

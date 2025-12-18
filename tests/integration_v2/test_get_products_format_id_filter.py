"""Integration test for get_products filtering with FormatId objects.

This test verifies that the format_ids filter in ProductFilters correctly handles
FormatId objects with .id attribute (not .format_id).

Regression test for: "unhashable type: 'FormatReference'" bug.

MIGRATED: Uses new pricing_options model instead of legacy Product pricing fields.
"""

from unittest.mock import Mock

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal
from src.core.schemas import FormatId, GetProductsRequest, ProductFilters
from tests.integration_v2.conftest import create_test_product_with_pricing
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def mock_context():
    """Create mock context with auth token."""
    context = Mock(spec=["meta"])
    context.meta = {"headers": {"x-adcp-auth": "format_id_filter_token"}}
    return context


@pytest.fixture(autouse=True)
def setup_products_with_formatid_objects(integration_db):
    """Create products with FormatId-style format storage."""
    with get_db_session() as session:
        tenant = create_tenant_with_timestamps(
            tenant_id="format_id_filter_test",
            name="FormatId Filter Test",
            subdomain="format-filter",
            is_active=True,
            ad_server="mock",
        )
        session.add(tenant)

        principal = Principal(
            tenant_id="format_id_filter_test",
            principal_id="test_principal",
            name="Test Advertiser",
            access_token="format_id_filter_token",
            platform_mappings={"mock": {"id": "test"}},
            created_at=get_utc_now(),
        )
        session.add(principal)
        session.flush()

        # Create products with FormatId-style dicts and proper pricing
        display_product = create_test_product_with_pricing(
            session=session,
            tenant_id="format_id_filter_test",
            product_id="display_product",
            name="Display Product",
            description="Has display formats",
            pricing_model="CPM",
            rate="15.00",
            is_fixed=True,
            currency="USD",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
            targeting_template={},
            delivery_type="guaranteed",
            is_custom=False,
            countries=["US"],
            property_tags=["all_inventory"],
        )

        video_product = create_test_product_with_pricing(
            session=session,
            tenant_id="format_id_filter_test",
            product_id="video_product",
            name="Video Product",
            description="Has video formats",
            pricing_model="CPM",
            rate="20.00",
            is_fixed=True,
            currency="USD",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_1280x720"},
            ],
            targeting_template={},
            delivery_type="guaranteed",
            is_custom=False,
            countries=["US"],
            property_tags=["all_inventory"],
        )

        session.commit()


def _import_get_products_impl():
    """Import the actual implementation function."""
    from src.core.tools.products import _get_products_impl

    return _get_products_impl


@pytest.mark.asyncio
async def test_filter_by_format_ids_with_formatid_objects(mock_context):
    """Test that filtering by format_ids works with FormatId objects.

    This is the actual code path that was broken - when a client sends:
    filters: {
      format_ids: [
        {agent_url: "https://...", id: "display_300x250"}
      ]
    }

    The server was checking for .format_id attribute instead of .id attribute.
    """
    get_products_impl = _import_get_products_impl()

    # Create request with FormatId filter (how the client sends it)
    request = GetProductsRequest(
        brand_manifest={"name": "Test campaign"},
        filters=ProductFilters(
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")]
        ),
    )

    # Call the implementation directly
    result = await get_products_impl(request, mock_context)

    # Should return only the display_product (has display_300x250)
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
async def test_filter_by_format_ids_no_matches(mock_context):
    """Test that filtering returns empty when no products match."""
    get_products_impl = _import_get_products_impl()

    # Request a format that doesn't exist
    request = GetProductsRequest(
        brand_manifest={"name": "Test campaign"},
        filters=ProductFilters(
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="audio_30s")]
        ),
    )

    result = await get_products_impl(request, mock_context)

    # Should return empty - no products have audio formats
    assert len(result.products) == 0


@pytest.mark.asyncio
async def test_filter_by_format_ids_video_format(mock_context):
    """Test filtering for video format returns correct product."""
    get_products_impl = _import_get_products_impl()

    request = GetProductsRequest(
        brand_manifest={"name": "Test campaign"},
        filters=ProductFilters(
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_1280x720")]
        ),
    )

    result = await get_products_impl(request, mock_context)

    # Should return only video_product
    assert len(result.products) == 1
    assert result.products[0].product_id == "video_product"


@pytest.mark.asyncio
async def test_filter_by_multiple_format_ids(mock_context):
    """Test filtering with multiple format IDs returns products matching any."""
    get_products_impl = _import_get_products_impl()

    request = GetProductsRequest(
        brand_manifest={"name": "Test campaign"},
        filters=ProductFilters(
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_1280x720"),
            ]
        ),
    )

    result = await get_products_impl(request, mock_context)

    # Should return both products (OR logic)
    assert len(result.products) == 2
    product_ids = {p.product_id for p in result.products}
    assert "display_product" in product_ids
    assert "video_product" in product_ids

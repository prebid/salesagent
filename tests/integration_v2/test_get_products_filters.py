"""Integration tests for get_products filtering behavior (v2 pricing model).

Tests that AdCP filters parameter correctly filters products from database.
This tests the actual filter logic implementation in main.py, not just schema validation.

MIGRATION NOTE: This file migrates tests from tests/integration/test_get_products_filters.py
to use the new pricing_options model instead of legacy Product pricing fields.
"""

from unittest.mock import Mock

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal
from tests.integration_v2.conftest import (
    add_required_setup_data,
    create_auction_product,
    create_test_product_with_pricing,
)
from tests.utils.database_helpers import create_tenant_with_timestamps, get_utc_now

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def mock_context():
    """Create mock context with filter_test_token for TestGetProductsFilterBehavior."""
    context = Mock(spec=["meta"])
    context.meta = {"headers": {"x-adcp-auth": "filter_test_token"}}
    return context


@pytest.fixture
def mock_context_filter_logic():
    """Create mock context with filter_logic_token for TestProductFilterLogic."""
    context = Mock(spec=["meta"])
    context.meta = {"headers": {"x-adcp-auth": "filter_logic_token"}}
    return context


@pytest.fixture
def mock_context_edge_case():
    """Create mock context with edge_case_token for TestFilterEdgeCases."""
    context = Mock(spec=["meta"])
    context.meta = {"headers": {"x-adcp-auth": "edge_case_token"}}
    return context


@pytest.mark.requires_db
class TestGetProductsFilterBehavior:
    """Test that filters actually filter products correctly with real database."""

    def _import_get_products_tool(self):
        """Import get_products tool and extract underlying function."""
        from src.core.tools.products import get_products_raw

        return get_products_raw

    @pytest.fixture(autouse=True)
    def setup_diverse_products(self, integration_db):
        """Create products with diverse characteristics for filtering."""
        with get_db_session() as session:
            # Create tenant and principal
            tenant = create_tenant_with_timestamps(
                tenant_id="filter_test",
                name="Filter Test Publisher",
                subdomain="filter-test",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant)
            session.flush()

            # Add required setup data for tenant
            add_required_setup_data(session, "filter_test")

            principal = Principal(
                tenant_id="filter_test",
                principal_id="test_principal",
                name="Test Advertiser",
                access_token="filter_test_token",
                platform_mappings={"mock": {"id": "test_advertiser"}},
                created_at=get_utc_now(),
            )
            session.add(principal)

            # Create products with different characteristics using new pricing model
            # Guaranteed, fixed-price CPM, display only
            guaranteed_display = create_test_product_with_pricing(
                session=session,
                tenant_id="filter_test",
                product_id="guaranteed_display",
                name="Premium Display - Fixed CPM",
                description="Guaranteed display inventory",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                    {"agent_url": "https://test.com", "id": "display_728x90"},
                ],
                targeting_template={},
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="15.0",
                is_fixed=True,
                currency="USD",
                countries=["US"],
                is_custom=False,
            )

            # Non-guaranteed, auction pricing, video only
            programmatic_video = create_auction_product(
                session=session,
                tenant_id="filter_test",
                product_id="programmatic_video",
                name="Programmatic Video - Dynamic CPM",
                description="Real-time bidding video inventory",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "video_15s"},
                    {"agent_url": "https://test.com", "id": "video_30s"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                floor_cpm="10.0",
                currency="USD",
                countries=["US", "CA"],
                is_custom=False,
            )

            # Guaranteed, fixed-price CPM, mixed formats (display + video)
            multiformat_guaranteed = create_test_product_with_pricing(
                session=session,
                tenant_id="filter_test",
                product_id="multiformat_guaranteed",
                name="Multi-Format Package - Fixed",
                description="Display + Video combo",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                    {"agent_url": "https://test.com", "id": "video_15s"},
                ],
                targeting_template={},
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="12.0",
                is_fixed=True,
                currency="USD",
                countries=["US"],
                is_custom=False,
            )

            # Non-guaranteed, auction pricing, display only
            programmatic_display = create_auction_product(
                session=session,
                tenant_id="filter_test",
                product_id="programmatic_display",
                name="Programmatic Display - Dynamic CPM",
                description="Real-time bidding display",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                floor_cpm="8.0",
                currency="USD",
                countries=["US"],
                is_custom=False,
            )

            # Guaranteed, fixed-price CPM, audio only
            guaranteed_audio = create_test_product_with_pricing(
                session=session,
                tenant_id="filter_test",
                product_id="guaranteed_audio",
                name="Guaranteed Audio - Fixed CPM",
                description="Podcast advertising",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "audio_30s"},
                ],
                targeting_template={},
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="20.0",
                is_fixed=True,
                currency="USD",
                countries=["US"],
                is_custom=False,
            )

            session.commit()

    @pytest.mark.asyncio
    async def test_filter_by_delivery_type_guaranteed(self):
        """Test filtering for guaranteed delivery products only."""
        get_products = self._import_get_products_tool()

        # Mock context with authentication
        context = Mock()
        context.meta = {"headers": {"x-adcp-auth": "filter_test_token"}}

        # Call get_products (currently no direct filter param support, will add)
        result = await get_products(
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            brief="",
            ctx=context,
        )

        # Verify we got products (baseline test)
        assert len(result.products) > 0

        # Count products by delivery_type for manual verification
        guaranteed_count = sum(1 for p in result.products if p.delivery_type.value == "guaranteed")
        non_guaranteed_count = sum(1 for p in result.products if p.delivery_type.value == "non_guaranteed")

        # Should have both types before filtering
        assert guaranteed_count >= 3  # guaranteed_display, multiformat_guaranteed, guaranteed_audio
        assert non_guaranteed_count >= 2  # programmatic_video, programmatic_display

    @pytest.mark.asyncio
    async def test_no_filter_returns_all_products(self, mock_context):
        """Test that calling without filters returns all products."""
        get_products = self._import_get_products_tool()

        context = mock_context

        result = await get_products(
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            brief="",
            ctx=context,
        )

        # Should return all 5 products created in fixture
        assert len(result.products) == 5

        # Verify diversity of products
        product_ids = {p.product_id for p in result.products}
        assert "guaranteed_display" in product_ids
        assert "programmatic_video" in product_ids
        assert "multiformat_guaranteed" in product_ids
        assert "programmatic_display" in product_ids
        assert "guaranteed_audio" in product_ids

    @pytest.mark.asyncio
    async def test_products_have_correct_structure(self, mock_context):
        """Test that returned products have all required AdCP fields."""
        get_products = self._import_get_products_tool()

        context = mock_context

        result = await get_products(
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            brief="",
            ctx=context,
        )

        # Check first product has all required fields
        product = result.products[0]
        assert hasattr(product, "product_id")
        assert hasattr(product, "name")
        assert hasattr(product, "description")
        assert hasattr(product, "format_ids")
        assert hasattr(product, "delivery_type")

        # Check pricing_options field (new v2 model)
        assert hasattr(product, "pricing_options")
        assert len(product.pricing_options) > 0

        pricing = product.pricing_options[0]
        assert hasattr(pricing, "pricing_model")
        assert hasattr(pricing, "rate")
        assert hasattr(pricing, "is_fixed")
        assert hasattr(pricing, "currency")

        # Check formats structure
        assert len(product.format_ids) > 0

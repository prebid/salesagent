"""Integration tests for get_products filtering behavior (v2 pricing model).

Tests that AdCP filters parameter correctly filters products from database.
This tests the actual filter logic implementation, not just schema validation.

MIGRATED: Uses ProductEnv harness + factory-based setup.
"""

from decimal import Decimal

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestGetProductsFilterBehavior:
    """Test that filters actually filter products correctly with real database."""

    @pytest.fixture
    def env(self, integration_db):
        """ProductEnv with diverse products for filter testing."""
        with ProductEnv(tenant_id="filter-test", principal_id="test-principal") as env:
            tenant = TenantFactory(tenant_id="filter-test", subdomain="filter-test")
            PrincipalFactory(tenant=tenant, principal_id="test-principal")

            # Guaranteed, fixed-price CPM, display only
            p1 = ProductFactory(
                tenant=tenant,
                product_id="guaranteed_display",
                name="Premium Display - Fixed CPM",
                description="Guaranteed display inventory",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                    {"agent_url": "https://test.com", "id": "display_728x90"},
                ],
                delivery_type="guaranteed",
                countries=["US"],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("15.0"), is_fixed=True)

            # Non-guaranteed, auction pricing, video only
            p2 = ProductFactory(
                tenant=tenant,
                product_id="programmatic_video",
                name="Programmatic Video - Dynamic CPM",
                description="Real-time bidding video inventory",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "video_15s"},
                    {"agent_url": "https://test.com", "id": "video_30s"},
                ],
                delivery_type="non_guaranteed",
                countries=["US", "CA"],
            )
            PricingOptionFactory(
                product=p2,
                pricing_model="cpm",
                rate=Decimal("10.0"),
                is_fixed=False,
                price_guidance={"floor": 10.0, "p50": 15.0, "p75": 20.0, "p90": 25.0},
            )

            # Guaranteed, fixed-price CPM, mixed formats
            p3 = ProductFactory(
                tenant=tenant,
                product_id="multiformat_guaranteed",
                name="Multi-Format Package - Fixed",
                description="Display + Video combo",
                format_ids=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                    {"agent_url": "https://test.com", "id": "video_15s"},
                ],
                delivery_type="guaranteed",
                countries=["US"],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            # Non-guaranteed, auction pricing, display only
            p4 = ProductFactory(
                tenant=tenant,
                product_id="programmatic_display",
                name="Programmatic Display - Dynamic CPM",
                description="Real-time bidding display",
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
                delivery_type="non_guaranteed",
                countries=["US"],
            )
            PricingOptionFactory(
                product=p4,
                pricing_model="cpm",
                rate=Decimal("8.0"),
                is_fixed=False,
                price_guidance={"floor": 8.0, "p50": 12.0, "p75": 16.0, "p90": 20.0},
            )

            # Guaranteed, fixed-price CPM, audio only
            p5 = ProductFactory(
                tenant=tenant,
                product_id="guaranteed_audio",
                name="Guaranteed Audio - Fixed CPM",
                description="Podcast advertising",
                format_ids=[{"agent_url": "https://test.com", "id": "audio_30s"}],
                delivery_type="guaranteed",
                countries=["US"],
            )
            PricingOptionFactory(product=p5, pricing_model="cpm", rate=Decimal("20.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    async def test_filter_by_delivery_type_guaranteed(self, env):
        """Test filtering for guaranteed delivery products only."""
        result = await env.call_impl(brief="")

        assert len(result.products) > 0

        guaranteed_count = sum(1 for p in result.products if p.delivery_type.value == "guaranteed")
        non_guaranteed_count = sum(1 for p in result.products if p.delivery_type.value == "non_guaranteed")

        assert guaranteed_count >= 3
        assert non_guaranteed_count >= 2

    @pytest.mark.asyncio
    async def test_no_filter_returns_all_products(self, env):
        """Test that calling without filters returns all products."""
        result = await env.call_impl(brief="")

        assert len(result.products) == 5

        product_ids = {p.product_id for p in result.products}
        assert "guaranteed_display" in product_ids
        assert "programmatic_video" in product_ids
        assert "multiformat_guaranteed" in product_ids
        assert "programmatic_display" in product_ids
        assert "guaranteed_audio" in product_ids

    @pytest.mark.asyncio
    async def test_products_have_correct_structure(self, env):
        """Test that returned products have all required AdCP fields."""
        result = await env.call_impl(brief="")

        product = result.products[0]
        assert hasattr(product, "product_id")
        assert hasattr(product, "name")
        assert hasattr(product, "description")
        assert hasattr(product, "format_ids")
        assert hasattr(product, "delivery_type")

        assert hasattr(product, "pricing_options")
        assert len(product.pricing_options) > 0

        pricing = product.pricing_options[0]
        pricing_inner = pricing.root if hasattr(pricing, "root") else pricing
        assert hasattr(pricing_inner, "pricing_model")
        assert hasattr(pricing_inner, "pricing_option_id")
        assert hasattr(pricing_inner, "currency")

        assert len(product.format_ids) > 0


@pytest.mark.requires_db
class TestNewGetProductsFilters:
    """Test the new AdCP 2.5 filters: countries and channels."""

    @pytest.fixture
    def env(self, integration_db):
        """ProductEnv with diverse products for country/channel filter testing."""
        with ProductEnv(tenant_id="new-filter-test", principal_id="new-filter-principal") as env:
            tenant = TenantFactory(tenant_id="new-filter-test", subdomain="new-filter-test")
            PrincipalFactory(tenant=tenant, principal_id="new-filter-principal")

            # Product 1: US only, display channel
            p1 = ProductFactory(
                tenant=tenant,
                product_id="us_display",
                name="US Display",
                delivery_type="guaranteed",
                countries=["US"],
                channels=["display"],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("15.0"), is_fixed=True)

            # Product 2: US + CA, olv channel
            p2 = ProductFactory(
                tenant=tenant,
                product_id="us_ca_video",
                name="US/CA Video",
                delivery_type="guaranteed",
                countries=["US", "CA"],
                channels=["olv"],
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("25.0"), is_fixed=True)

            # Product 3: Global, streaming_audio channel
            p3 = ProductFactory(
                tenant=tenant,
                product_id="global_audio",
                name="Global Audio",
                delivery_type="guaranteed",
                countries=None,
                channels=["streaming_audio"],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("20.0"), is_fixed=True)

            # Product 4: UK only, display channel
            p4 = ProductFactory(
                tenant=tenant,
                product_id="uk_display",
                name="UK Display",
                delivery_type="guaranteed",
                countries=["GB"],
                channels=["display"],
            )
            PricingOptionFactory(product=p4, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True, currency="GBP")

            # Product 5: US, social channel
            p5 = ProductFactory(
                tenant=tenant,
                product_id="us_native",
                name="US Social",
                delivery_type="non_guaranteed",
                countries=["US"],
                channels=["social"],
            )
            PricingOptionFactory(product=p5, pricing_model="cpm", rate=Decimal("8.0"), is_fixed=True)

            # Product 6: Global, no channels set
            p6 = ProductFactory(
                tenant=tenant,
                product_id="global_no_channel",
                name="Global No Channel",
                delivery_type="guaranteed",
                countries=None,
                channels=None,
            )
            PricingOptionFactory(product=p6, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    async def test_filter_by_countries_single_country(self, env):
        """Test filtering products by a single country."""
        result = await env.call_impl(brief="", filters={"countries": ["US"]})

        product_ids = {p.product_id for p in result.products}
        assert "us_display" in product_ids
        assert "us_ca_video" in product_ids
        assert "global_audio" in product_ids
        assert "us_native" in product_ids
        assert "global_no_channel" in product_ids
        assert "uk_display" not in product_ids

    @pytest.mark.asyncio
    async def test_filter_by_countries_multiple_countries(self, env):
        """Test filtering products by multiple countries."""
        result = await env.call_impl(brief="", filters={"countries": ["CA", "GB"]})

        product_ids = {p.product_id for p in result.products}
        assert "us_ca_video" in product_ids
        assert "uk_display" in product_ids
        assert "global_audio" in product_ids
        assert "global_no_channel" in product_ids
        assert "us_display" not in product_ids
        assert "us_native" not in product_ids

    @pytest.mark.asyncio
    async def test_filter_by_channels_display(self, env):
        """Test filtering products by display channel."""
        result = await env.call_impl(brief="", filters={"channels": ["display"]})

        product_ids = {p.product_id for p in result.products}
        assert "us_display" in product_ids
        assert "uk_display" in product_ids
        assert "global_no_channel" in product_ids
        assert "us_ca_video" not in product_ids
        assert "global_audio" not in product_ids
        assert "us_native" not in product_ids

    @pytest.mark.asyncio
    async def test_filter_by_channels_video(self, env):
        """Test filtering products by olv (online video) channel."""
        result = await env.call_impl(brief="", filters={"channels": ["olv"]})

        product_ids = {p.product_id for p in result.products}
        assert "us_ca_video" in product_ids
        assert "global_no_channel" in product_ids

    @pytest.mark.asyncio
    async def test_filter_by_channels_multiple(self, env):
        """Test filtering products by multiple channels."""
        result = await env.call_impl(brief="", filters={"channels": ["streaming_audio", "social"]})

        product_ids = {p.product_id for p in result.products}
        assert "global_audio" in product_ids
        assert "us_native" in product_ids
        assert "global_no_channel" in product_ids

    @pytest.mark.asyncio
    async def test_filter_by_channels_retail_excludes_mock_products(self, env):
        """Test that retail_media channel filter excludes products without explicit retail channel."""
        result = await env.call_impl(brief="", filters={"channels": ["retail_media"]})

        product_ids = {p.product_id for p in result.products}
        assert "global_no_channel" not in product_ids

    @pytest.mark.asyncio
    async def test_combined_filters_country_and_channel(self, env):
        """Test combining country and channel filters."""
        result = await env.call_impl(
            brief="",
            filters={"countries": ["US"], "channels": ["display"]},
        )

        product_ids = {p.product_id for p in result.products}
        assert "us_display" in product_ids
        assert "global_no_channel" in product_ids
        assert "uk_display" not in product_ids
        assert "us_ca_video" not in product_ids

    @pytest.mark.asyncio
    async def test_combined_filters_strict_match(self, env):
        """Test combining country and channel filters with strict matching."""
        result = await env.call_impl(
            brief="",
            filters={"countries": ["CA"], "channels": ["olv"]},
        )

        product_ids = {p.product_id for p in result.products}
        assert "us_ca_video" in product_ids
        assert "global_no_channel" in product_ids
        assert "us_display" not in product_ids

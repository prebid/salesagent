"""Integration tests for min_exposures filter in _get_products_impl.

Tests the filtering logic at src/core/tools/products.py lines 740-764:
- Guaranteed products: filtered by estimated_exposures vs min_exposures threshold
- Non-guaranteed products: included if price_guidance present (via get_recommended_cpm)

The DynamicPricingService mock is configured to set estimated_exposures on
products during enrichment, simulating real dynamic pricing behavior.

Obligations covered:
- UC-001-ALT-FILTERED-DISCOVERY-22: min_exposures with guaranteed product (forecast meets threshold)
- UC-001-ALT-FILTERED-DISCOVERY-23: min_exposures with guaranteed product (forecast below threshold)
- UC-001-ALT-FILTERED-DISCOVERY-24: min_exposures with non-guaranteed product (price_guidance present)
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_enrichment_mock(exposures_by_product_id: dict[str, int | None]):
    """Create a DynamicPricingService mock that sets estimated_exposures on products.

    Args:
        exposures_by_product_id: Mapping of product_id -> estimated_exposures value.
            Products not in the dict are passed through unchanged.
    """
    mock_instance = MagicMock()

    def enrich_side_effect(products, **kwargs):
        for product in products:
            if product.product_id in exposures_by_product_id:
                product.estimated_exposures = exposures_by_product_id[product.product_id]
        return products

    mock_instance.enrich_products_with_pricing.side_effect = enrich_side_effect
    return mock_instance


@pytest.mark.requires_db
class TestMinExposuresFilter:
    """Integration tests for min_exposures filtering in _get_products_impl."""

    @pytest.fixture
    def env(self, integration_db):
        """ProductEnv with guaranteed and non-guaranteed products.

        Re-patches DynamicPricingService because these tests need to control
        estimated_exposures values deterministically (the real service would
        compute from FormatPerformanceMetrics which is not the focus here).
        """
        with (
            ProductEnv(tenant_id="min-exp-test", principal_id="test-principal") as env,
            patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_cls,
        ):
            # Default pass-through until tests configure specific enrichment
            mock_pricing_cls.return_value.enrich_products_with_pricing.side_effect = lambda products, **kw: products
            env.mock["dynamic_pricing"] = mock_pricing_cls  # expose for test methods

            tenant = TenantFactory(tenant_id="min-exp-test", subdomain="min-exp-test")
            PrincipalFactory(tenant=tenant, principal_id="test-principal")

            # Guaranteed product — will have estimated_exposures set by enrichment mock
            p_guaranteed = ProductFactory(
                tenant=tenant,
                product_id="guaranteed_high",
                name="Guaranteed High Volume",
                delivery_type="guaranteed",
            )
            PricingOptionFactory(
                product=p_guaranteed,
                pricing_model="cpm",
                rate=Decimal("15.0"),
                is_fixed=True,
            )

            # Another guaranteed product — low volume
            p_guaranteed_low = ProductFactory(
                tenant=tenant,
                product_id="guaranteed_low",
                name="Guaranteed Low Volume",
                delivery_type="guaranteed",
            )
            PricingOptionFactory(
                product=p_guaranteed_low,
                pricing_model="cpm",
                rate=Decimal("20.0"),
                is_fixed=True,
            )

            # Non-guaranteed product WITH price_guidance (has recommended CPM)
            p_non_guaranteed = ProductFactory(
                tenant=tenant,
                product_id="non_guaranteed_with_pg",
                name="Non-Guaranteed With Price Guidance",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=p_non_guaranteed,
                pricing_model="cpm",
                rate=Decimal("8.0"),
                is_fixed=False,
                price_guidance={
                    "floor": 8.0,
                    "p50": 12.0,
                    "p75": 16.0,
                    "p90": 20.0,
                },
            )

            # Non-guaranteed product WITHOUT CPM price_guidance
            # Uses CPC pricing so get_recommended_cpm() returns None
            p_non_guaranteed_no_pg = ProductFactory(
                tenant=tenant,
                product_id="non_guaranteed_no_pg",
                name="Non-Guaranteed No CPM Price Guidance",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=p_non_guaranteed_no_pg,
                pricing_model="cpc",
                rate=Decimal("0.50"),
                is_fixed=True,
            )

            yield env

    async def test_guaranteed_product_meets_threshold_is_kept(self, env):
        """UC-001-ALT-FILTERED-DISCOVERY-22: Guaranteed product with
        estimated_exposures >= min_exposures is included in results."""
        # Configure enrichment: guaranteed_high has 100k exposures
        mock_pricing = _make_enrichment_mock(
            {
                "guaranteed_high": 100_000,
                "guaranteed_low": 5_000,
            }
        )
        env.mock["dynamic_pricing"].return_value = mock_pricing

        response = await env.call_impl(
            brief="high volume ads",
            filters={"min_exposures": 50_000},
        )

        product_ids = [p.product_id for p in response.products]
        assert "guaranteed_high" in product_ids

    async def test_guaranteed_product_below_threshold_is_excluded(self, env):
        """UC-001-ALT-FILTERED-DISCOVERY-23: Guaranteed product with
        estimated_exposures < min_exposures is excluded from results."""
        # Configure enrichment: guaranteed_low has only 5k exposures
        mock_pricing = _make_enrichment_mock(
            {
                "guaranteed_high": 100_000,
                "guaranteed_low": 5_000,
            }
        )
        env.mock["dynamic_pricing"].return_value = mock_pricing

        response = await env.call_impl(
            brief="high volume ads",
            filters={"min_exposures": 50_000},
        )

        product_ids = [p.product_id for p in response.products]
        assert "guaranteed_low" not in product_ids

    async def test_non_guaranteed_with_price_guidance_is_kept(self, env):
        """UC-001-ALT-FILTERED-DISCOVERY-24: Non-guaranteed product with
        price_guidance present is included regardless of min_exposures threshold."""
        mock_pricing = _make_enrichment_mock(
            {
                "guaranteed_high": 100_000,
                "guaranteed_low": 5_000,
            }
        )
        env.mock["dynamic_pricing"].return_value = mock_pricing

        response = await env.call_impl(
            brief="any ads",
            filters={"min_exposures": 50_000},
        )

        product_ids = [p.product_id for p in response.products]
        assert "non_guaranteed_with_pg" in product_ids

    async def test_non_guaranteed_without_price_guidance_is_kept(self, env):
        """Non-guaranteed product without price_guidance is still included
        (code includes all non-guaranteed products per current logic)."""
        mock_pricing = _make_enrichment_mock(
            {
                "guaranteed_high": 100_000,
                "guaranteed_low": 5_000,
            }
        )
        env.mock["dynamic_pricing"].return_value = mock_pricing

        response = await env.call_impl(
            brief="any ads",
            filters={"min_exposures": 50_000},
        )

        product_ids = [p.product_id for p in response.products]
        assert "non_guaranteed_no_pg" in product_ids

    async def test_no_min_exposures_returns_all_products(self, env):
        """When min_exposures is not set, all products are returned unfiltered."""
        response = await env.call_impl(brief="all products")

        product_ids = [p.product_id for p in response.products]
        assert "guaranteed_high" in product_ids
        assert "guaranteed_low" in product_ids
        assert "non_guaranteed_with_pg" in product_ids
        assert "non_guaranteed_no_pg" in product_ids

    async def test_guaranteed_with_none_exposures_is_excluded(self, env):
        """Guaranteed product where estimated_exposures is None is excluded
        when min_exposures is set (cannot verify it meets threshold)."""
        # Only set exposures for guaranteed_high, leave guaranteed_low without
        mock_pricing = _make_enrichment_mock(
            {
                "guaranteed_high": 100_000,
            }
        )
        env.mock["dynamic_pricing"].return_value = mock_pricing

        response = await env.call_impl(
            brief="volume ads",
            filters={"min_exposures": 10_000},
        )

        product_ids = [p.product_id for p in response.products]
        assert "guaranteed_high" in product_ids
        # guaranteed_low has no estimated_exposures set -> excluded
        assert "guaranteed_low" not in product_ids

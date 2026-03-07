"""Integration tests for dynamic pricing enrichment (un-mocked).

Verifies that DynamicPricingService runs against real DB in ProductEnv,
enriching products with price_guidance from FormatPerformanceMetrics data.

When no metrics exist, products pass through unchanged (graceful no-op).
When metrics exist, CPM pricing options get floor_price and price_guidance.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.factories import (
    FormatPerformanceMetricsFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    TenantFactory,
)
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestDynamicPricingUnmocked:
    """Verify DynamicPricingService runs for real in integration tests."""

    @pytest.mark.asyncio
    async def test_no_metrics_products_unchanged(self, integration_db):
        """Products pass through unchanged when no FormatPerformanceMetrics exist."""
        with ProductEnv(tenant_id="pricing-noop", principal_id="pricing-principal") as env:
            tenant = TenantFactory(tenant_id="pricing-noop", subdomain="pricing-noop")
            PrincipalFactory(tenant=tenant, principal_id="pricing-principal")
            p = ProductFactory(tenant=tenant, product_id="no_metrics_product", name="No Metrics")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            response = await env.call_impl(brief="test ads")

            assert len(response.products) == 1
            product = response.products[0]
            # Original fixed pricing should be intact
            assert len(product.pricing_options) >= 1
            cpm_option = product.pricing_options[0].root
            assert cpm_option.fixed_price == 10.0

    @pytest.mark.asyncio
    async def test_with_metrics_pricing_enriched(self, integration_db):
        """Products get price_guidance updated when FormatPerformanceMetrics exist.

        The dynamic pricing service updates the CPM option's floor_price to the
        median_cpm from metrics (5.50) and adds p75 to price_guidance (8.25).
        If the service is mocked (pass-through), floor_price stays at original (5.0).
        """
        with ProductEnv(tenant_id="pricing-enrich", principal_id="pricing-enrich-p") as env:
            tenant = TenantFactory(tenant_id="pricing-enrich", subdomain="pricing-enrich")
            PrincipalFactory(tenant=tenant, principal_id="pricing-enrich-p")

            # Product with display_300x250 format — original floor is 5.0
            p = ProductFactory(
                tenant=tenant,
                product_id="enriched_product",
                name="Enriched Product",
            )
            PricingOptionFactory(
                product=p,
                pricing_model="cpm",
                rate=Decimal("10.0"),
                is_fixed=False,
                price_guidance={"floor": 5.0, "p25": 4.0, "p50": 6.0, "p75": 8.0, "p90": 11.0},
            )

            # Create matching metrics for 300x250 with DIFFERENT values than original
            FormatPerformanceMetricsFactory(
                tenant=tenant,
                creative_size="300x250",
                median_cpm=Decimal("5.50"),  # Different from original floor (5.0)
                p75_cpm=Decimal("8.25"),  # Different from original p75 (8.0)
                p90_cpm=Decimal("12.00"),
            )

            response = await env.call_impl(brief="display ads")

            assert len(response.products) == 1
            product = response.products[0]

            # Find the CPM option
            cpm_options = [po.root for po in product.pricing_options if po.root.pricing_model.upper() == "CPM"]
            assert len(cpm_options) >= 1

            cpm = cpm_options[0]
            # The service sets floor_price = median_cpm (5.50)
            # If still mocked (pass-through), floor_price would be 5.0 (from original price_guidance.floor)
            assert getattr(cpm, "floor_price", None) == 5.50, (
                f"Expected floor_price=5.50 (median_cpm from metrics), got {getattr(cpm, 'floor_price', None)}. "
                "If floor_price is 5.0, DynamicPricingService is still mocked (pass-through)."
            )

    @pytest.mark.asyncio
    async def test_metrics_no_match_graceful(self, integration_db):
        """Products with formats that don't match any metrics pass through unchanged."""
        with ProductEnv(tenant_id="pricing-nomatch", principal_id="pricing-nomatch-p") as env:
            tenant = TenantFactory(tenant_id="pricing-nomatch", subdomain="pricing-nomatch")
            PrincipalFactory(tenant=tenant, principal_id="pricing-nomatch-p")

            p = ProductFactory(tenant=tenant, product_id="nomatch_product", name="No Match")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Create metrics for a DIFFERENT size than the product uses
            FormatPerformanceMetricsFactory(
                tenant=tenant,
                creative_size="970x250",  # product uses 300x250
            )

            response = await env.call_impl(brief="test ads")

            assert len(response.products) == 1
            # Product should still be returned, just without enrichment
            product = response.products[0]
            assert len(product.pricing_options) >= 1

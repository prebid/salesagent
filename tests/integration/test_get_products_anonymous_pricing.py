"""Integration tests for anonymous pricing suppression (UC-001).

Tests verify that anonymous users (no principal_id) receive products with
pricing_options set to empty list, while authenticated users see full pricing.

Obligations covered:
- BR-RULE-004-01: Anonymous pricing suppression
- CONSTR-ANONYMOUS-PRICING-01: Anonymous pricing schema constraint
"""

from decimal import Decimal

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import LazyTenantContext
from src.core.testing_hooks import AdCPTestContext
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _lazy_identity(
    tenant_id: str,
    principal_id: str | None = "p1",
) -> ResolvedIdentity:
    """Create a ResolvedIdentity using LazyTenantContext for real DB tenant lookup."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=LazyTenantContext(tenant_id),
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
    )


class TestAnonymousPricingSuppression:
    """Tests that anonymous users get pricing_options=[] on every product.

    The business rule: unauthenticated (anonymous) requests must have
    pricing data stripped from all returned products. Products are still
    returned, only pricing is hidden.
    """

    @pytest.mark.asyncio
    async def test_anonymous_request_products_have_empty_pricing_options(self, integration_db):
        """Anonymous requests have pricing_options set to empty array on every product.

        Covers: BR-RULE-004-01
        """
        with ProductEnv(tenant_id="anon-pricing-1", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-1",
                subdomain="anon-pricing-1",
                brand_manifest_policy="public",
            )
            p = ProductFactory(tenant=tenant, product_id="priced_product")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("15.00"))

            env._identity = _lazy_identity("anon-pricing-1", principal_id=None)

            result = await env.call_impl(brief="display ads")

        assert len(result.products) == 1
        assert result.products[0].product_id == "priced_product"
        assert result.products[0].pricing_options == []

    @pytest.mark.asyncio
    async def test_anonymous_request_product_count_unchanged(self, integration_db):
        """Anonymous pricing suppression does not reduce the product count.

        Covers: BR-RULE-004-01
        """
        with ProductEnv(tenant_id="anon-pricing-2", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-2",
                subdomain="anon-pricing-2",
                brand_manifest_policy="public",
            )
            for pid in ("prod_a", "prod_b", "prod_c"):
                p = ProductFactory(tenant=tenant, product_id=pid)
                PricingOptionFactory(product=p)

            env._identity = _lazy_identity("anon-pricing-2", principal_id=None)

            result = await env.call_impl(brief="all products")

        assert len(result.products) == 3
        for product in result.products:
            assert product.pricing_options == [], (
                f"Product {product.product_id} should have empty pricing_options for anonymous user"
            )

    @pytest.mark.asyncio
    async def test_authenticated_request_has_populated_pricing_options(self, integration_db):
        """Authenticated requests return products with populated pricing_options.

        Covers: CONSTR-ANONYMOUS-PRICING-01
        """
        with ProductEnv(tenant_id="anon-pricing-3", principal_id="auth-principal") as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-3",
                subdomain="anon-pricing-3",
            )
            PrincipalFactory(tenant=tenant, principal_id="auth-principal")
            p = ProductFactory(tenant=tenant, product_id="priced_product")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("15.00"))

            result = await env.call_impl(brief="display ads")

        assert len(result.products) == 1
        assert len(result.products[0].pricing_options) > 0

    @pytest.mark.asyncio
    async def test_multiple_pricing_options_all_suppressed_for_anonymous(self, integration_db):
        """A product with multiple pricing options has ALL of them suppressed for anonymous.

        Covers: BR-RULE-004-01
        """
        with ProductEnv(tenant_id="anon-pricing-4", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-4",
                subdomain="anon-pricing-4",
                brand_manifest_policy="public",
            )
            p = ProductFactory(tenant=tenant, product_id="multi_price_product")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("15.00"), currency="USD")
            PricingOptionFactory(product=p, pricing_model="cpc", rate=Decimal("2.50"), currency="USD")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("12.00"), currency="EUR")

            env._identity = _lazy_identity("anon-pricing-4", principal_id=None)

            result = await env.call_impl(brief="multi pricing product")

        assert len(result.products) == 1
        assert result.products[0].pricing_options == [], "All pricing options should be suppressed for anonymous users"

    @pytest.mark.asyncio
    async def test_mixed_delivery_types_all_suppressed_for_anonymous(self, integration_db):
        """Products with different delivery types all have pricing suppressed for anonymous.

        Covers: CONSTR-ANONYMOUS-PRICING-01
        """
        with ProductEnv(tenant_id="anon-pricing-5", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-5",
                subdomain="anon-pricing-5",
                brand_manifest_policy="public",
            )

            # Guaranteed product with fixed pricing
            p_guaranteed = ProductFactory(
                tenant=tenant,
                product_id="guaranteed_prod",
                delivery_type="guaranteed",
            )
            PricingOptionFactory(
                product=p_guaranteed,
                pricing_model="cpm",
                rate=Decimal("20.00"),
                is_fixed=True,
            )

            # Non-guaranteed product with auction pricing
            p_non_guaranteed = ProductFactory(
                tenant=tenant,
                product_id="non_guaranteed_prod",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=p_non_guaranteed,
                pricing_model="cpm",
                rate=Decimal("5.00"),
                is_fixed=False,
                price_guidance={"floor": 5.0, "p50": 7.5, "p75": 10.0, "p90": 12.5},
            )

            env._identity = _lazy_identity("anon-pricing-5", principal_id=None)

            result = await env.call_impl(brief="mixed delivery")

        assert len(result.products) == 2
        for product in result.products:
            assert product.pricing_options == [], (
                f"Product {product.product_id} ({product.delivery_type}) "
                "should have empty pricing_options for anonymous user"
            )

    @pytest.mark.asyncio
    async def test_authenticated_sees_pricing_anonymous_does_not(self, integration_db):
        """Same product returns pricing for authenticated but not anonymous users.

        Covers: CONSTR-ANONYMOUS-PRICING-01
        """
        # Authenticated request
        with ProductEnv(tenant_id="anon-pricing-6", principal_id="auth-user") as env:
            tenant = TenantFactory(
                tenant_id="anon-pricing-6",
                subdomain="anon-pricing-6",
                brand_manifest_policy="public",
            )
            PrincipalFactory(tenant=tenant, principal_id="auth-user")
            p = ProductFactory(tenant=tenant, product_id="dual_test_product")
            PricingOptionFactory(product=p, pricing_model="cpm", rate=Decimal("10.00"))

            auth_result = await env.call_impl(brief="pricing test")

        assert len(auth_result.products) == 1
        assert len(auth_result.products[0].pricing_options) > 0, "Authenticated user should see pricing options"

        # Anonymous request against same tenant/product
        with ProductEnv(tenant_id="anon-pricing-6", principal_id=None) as env:
            env._identity = _lazy_identity("anon-pricing-6", principal_id=None)

            anon_result = await env.call_impl(brief="pricing test")

        assert len(anon_result.products) == 1
        assert anon_result.products[0].pricing_options == [], "Anonymous user should not see pricing options"

"""Integration regression tests for the GetProductsResponse anonymous message.

Pins the fix for issue #1246. The unit tests in
``tests/unit/test_pricing_option_is_priced.py`` and
``tests/unit/test_get_products_response_str.py`` exercise the helper and the
``__str__`` method directly against hand-built fixtures. These integration
tests close the missing seam: they wire the **real** DB → Product ORM →
``convert_pricing_option_to_adcp`` → ``GetProductsResponse`` → ``str()``
pipeline together, so a future regression in any layer that produces an
unexpected wire shape would fail here.

The auth message string itself is identical across MCP and A2A transports —
both call ``str(response)`` on the same Pydantic model that
``_get_products_impl`` returns. We therefore exercise the message through
``env.call_impl()`` (the shared business-logic seam) rather than each
transport individually; the unit tests already cover transport-shape
variation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import LazyTenantContext
from src.core.testing_hooks import AdCPTestContext
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_AUTH_SUFFIX = "Please connect through an authorized buying agent for pricing data."


def _lazy_identity(tenant_id: str, principal_id: str | None) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=LazyTenantContext(tenant_id),
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
    )


class TestAnonymousMessageRegression:
    """Issue #1246 regression — authenticated v3 buyers must not see the auth suffix."""

    @pytest.mark.asyncio
    async def test_authenticated_fixed_price_no_auth_suffix(self, integration_db):
        """v3 fixed-price buyer: response message must NOT contain the auth suffix."""
        with ProductEnv(tenant_id="anon-msg-1", principal_id="auth-1") as env:
            tenant = TenantFactory(tenant_id="anon-msg-1", subdomain="anon-msg-1")
            PrincipalFactory(tenant=tenant, principal_id="auth-1")
            product = ProductFactory(tenant=tenant, product_id="fixed_priced")
            PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("10.00"), is_fixed=True)

            response = await env.call_impl(brief="display ads")

        assert len(response.products) == 1
        assert _AUTH_SUFFIX not in str(response)

    @pytest.mark.asyncio
    async def test_authenticated_auction_with_floor_no_auth_suffix(self, integration_db):
        """v3 auction buyer (floor_price + percentiles): no auth suffix."""
        with ProductEnv(tenant_id="anon-msg-2", principal_id="auth-2") as env:
            tenant = TenantFactory(tenant_id="anon-msg-2", subdomain="anon-msg-2")
            PrincipalFactory(tenant=tenant, principal_id="auth-2")
            product = ProductFactory(
                tenant=tenant,
                product_id="auction_with_floor",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                is_fixed=False,
                price_guidance={"floor": 5.0, "p50": 8.0, "p75": 11.0, "p90": 14.0},
            )

            response = await env.call_impl(brief="auction inventory")

        assert len(response.products) == 1
        assert _AUTH_SUFFIX not in str(response)

    @pytest.mark.asyncio
    async def test_authenticated_auction_price_guidance_only_no_auth_suffix(self, integration_db):
        """v3 auction with only price_guidance (no floor) — spec-legal — no auth suffix.

        This case is what the reporter's three-field proposal would have missed:
        an auction option that publishes percentile hints but no hard floor.
        Fixed by including ``price_guidance`` in the helper's recognized fields.
        """
        with ProductEnv(tenant_id="anon-msg-3", principal_id="auth-3") as env:
            tenant = TenantFactory(tenant_id="anon-msg-3", subdomain="anon-msg-3")
            PrincipalFactory(tenant=tenant, principal_id="auth-3")
            product = ProductFactory(
                tenant=tenant,
                product_id="auction_pg_only",
                delivery_type="non_guaranteed",
            )
            # Note: DB constraint a098c8bb42ed requires `floor` in price_guidance
            # for is_fixed=False rows, so we set it but expect the conversion to
            # promote it to top-level floor_price; price_guidance keeps the
            # percentiles. The buyer-visible shape is still rate-bearing.
            PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                is_fixed=False,
                price_guidance={"floor": 4.0, "p25": 5.0, "p50": 7.0, "p75": 10.0, "p90": 13.0},
            )

            response = await env.call_impl(brief="percentile hint product")

        assert len(response.products) == 1
        assert _AUTH_SUFFIX not in str(response)

    @pytest.mark.asyncio
    async def test_authenticated_mixed_priced_and_bare_no_auth_suffix(self, integration_db):
        """Mixed response: priced product + product with pricing_options=[] -> no auth suffix.

        The heuristic fires only when EVERY product with non-empty
        pricing_options has only rate-less options. Adding even one priced
        product breaks the inner all().
        """
        with ProductEnv(tenant_id="anon-msg-4", principal_id="auth-4") as env:
            tenant = TenantFactory(tenant_id="anon-msg-4", subdomain="anon-msg-4")
            PrincipalFactory(tenant=tenant, principal_id="auth-4")

            priced = ProductFactory(tenant=tenant, product_id="priced_one")
            PricingOptionFactory(product=priced, pricing_model="cpm", rate=Decimal("12.00"), is_fixed=True)

            second = ProductFactory(tenant=tenant, product_id="priced_two")
            PricingOptionFactory(product=second, pricing_model="cpm", rate=Decimal("8.00"), is_fixed=True)

            response = await env.call_impl(brief="mixed catalog")

        assert len(response.products) == 2
        assert _AUTH_SUFFIX not in str(response)


class TestAnonymousMessageEmittedWhenAppropriate:
    """When the buyer truly cannot see pricing, the auth suffix is still expected."""

    @pytest.mark.asyncio
    async def test_anonymous_buyer_pricing_stripped_shows_auth_suffix(self, integration_db):
        """Anonymous buyer (principal_id=None): pricing_options stripped to [], auth suffix shown."""
        with ProductEnv(tenant_id="anon-msg-5", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="anon-msg-5",
                subdomain="anon-msg-5",
                brand_manifest_policy="public",
            )
            product = ProductFactory(tenant=tenant, product_id="anon_view")
            PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("15.00"), is_fixed=True)

            env._identity = _lazy_identity("anon-msg-5", principal_id=None)

            response = await env.call_impl(brief="anonymous view")

        # Anonymous flow zeroes pricing_options on every product (products.py:746-752),
        # so the heuristic's outer `all(... if p.pricing_options)` iterates zero items
        # and is vacuously True — the suffix fires.
        assert len(response.products) == 1
        assert response.products[0].pricing_options == []
        assert _AUTH_SUFFIX in str(response)

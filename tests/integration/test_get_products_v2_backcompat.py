"""Integration regression tests for v2 backward-compat in get_products.

Pins the dead-code regression fixed in this PR (companion to #1246):
``apply_version_compat`` short-circuited on dict input ever since PR #1081's
squash, so every pre-3.0 buyer received clean v3 dicts (no ``rate``,
``is_fixed``, or ``price_guidance.floor``) regardless of the declared
``adcp_version``. The fix renamed the function to
``add_get_products_v2_compat`` and made it dict-in/dict-out so it actually
walks the response and adds v2 keys for pre-3.0 clients.

These tests exercise the **MCP** transport path end-to-end: real DB →
``_get_products_impl`` → ``model_dump`` → ``add_get_products_v2_compat`` →
wire dict. They assert that pre-3.0 buyers receive both v3 fields (the
authoritative source) AND v2 mirror keys, while v3+ buyers receive a clean
v3-only shape.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _structured_pricing_option(env: ProductEnv, *, adcp_version: str | None) -> dict:
    """Drive the MCP wrapper end-to-end and return the first product's first pricing option dict."""
    result = env.call_mcp(brief="display ads", adcp_version=adcp_version or "1.0.0")
    response_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    # The harness call_mcp returns the parsed Pydantic response — re-apply
    # the transport-layer compat to mirror what real wire callers receive.
    from src.core.version_compat import add_get_products_v2_compat

    response_dict = add_get_products_v2_compat(response_dict, adcp_version)
    return response_dict["products"][0]["pricing_options"][0]


class TestV2BuyerBackwardCompat:
    """Pre-3.0 clients receive v2 mirror keys alongside v3 fields."""

    def test_v2_buyer_fixed_price_gets_rate_and_is_fixed(self, integration_db):
        with ProductEnv(tenant_id="v2-bc-1", principal_id="auth-v2-1") as env:
            tenant = TenantFactory(tenant_id="v2-bc-1", subdomain="v2-bc-1")
            PrincipalFactory(tenant=tenant, principal_id="auth-v2-1")
            product = ProductFactory(tenant=tenant, product_id="fixed_p")
            PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("10.00"), is_fixed=True)

            po = _structured_pricing_option(env, adcp_version="2.5.0")

        # v3 source field still present (additive transform).
        assert po["fixed_price"] == 10.0
        # v2 mirror keys added.
        assert po["is_fixed"] is True
        assert po["rate"] == 10.0

    def test_v2_buyer_auction_gets_floor_in_price_guidance(self, integration_db):
        with ProductEnv(tenant_id="v2-bc-2", principal_id="auth-v2-2") as env:
            tenant = TenantFactory(tenant_id="v2-bc-2", subdomain="v2-bc-2")
            PrincipalFactory(tenant=tenant, principal_id="auth-v2-2")
            product = ProductFactory(
                tenant=tenant,
                product_id="auction_p",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                is_fixed=False,
                price_guidance={"floor": 5.0, "p50": 8.0, "p75": 11.0, "p90": 14.0},
            )

            po = _structured_pricing_option(env, adcp_version="2.5.0")

        # v3 fields present.
        assert po["floor_price"] == 5.0
        # v2 keys added: is_fixed=False (no fixed_price), price_guidance.floor mirrored.
        assert po["is_fixed"] is False
        assert "rate" not in po
        assert po["price_guidance"]["floor"] == 5.0


class TestV3BuyerCleanResponse:
    """V3+ clients receive only v3 fields — no v2 mirror keys."""

    def test_v3_buyer_no_v2_keys_for_fixed_price(self, integration_db):
        with ProductEnv(tenant_id="v2-bc-3", principal_id="auth-v3-1") as env:
            tenant = TenantFactory(tenant_id="v2-bc-3", subdomain="v2-bc-3")
            PrincipalFactory(tenant=tenant, principal_id="auth-v3-1")
            product = ProductFactory(tenant=tenant, product_id="fixed_p")
            PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("12.00"), is_fixed=True)

            po = _structured_pricing_option(env, adcp_version="3.0.0")

        assert po["fixed_price"] == 12.0
        # No v2 mirror keys for v3+ clients.
        assert "rate" not in po
        assert "is_fixed" not in po

    def test_v3_buyer_no_v2_keys_for_auction(self, integration_db):
        with ProductEnv(tenant_id="v2-bc-4", principal_id="auth-v3-2") as env:
            tenant = TenantFactory(tenant_id="v2-bc-4", subdomain="v2-bc-4")
            PrincipalFactory(tenant=tenant, principal_id="auth-v3-2")
            product = ProductFactory(
                tenant=tenant,
                product_id="auction_p",
                delivery_type="non_guaranteed",
            )
            PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                is_fixed=False,
                price_guidance={"floor": 4.0, "p50": 7.0, "p75": 10.0, "p90": 13.0},
            )

            po = _structured_pricing_option(env, adcp_version="3.0.0")

        assert po["floor_price"] == 4.0
        # v3 price_guidance keeps only percentiles — no `floor` key.
        assert "floor" not in po["price_guidance"]
        # No v2 mirror keys for v3+ clients.
        assert "rate" not in po
        assert "is_fixed" not in po

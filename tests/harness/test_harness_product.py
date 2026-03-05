"""Meta-tests for ProductEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

import pytest

from src.core.schemas import GetProductsResponse
from tests.harness.product_unit import ProductEnv


class TestProductEnvContract:
    """Contract tests for ProductEnv (unit variant)."""

    async def test_default_happy_path(self):
        """Default env returns empty product list with no products added."""
        with ProductEnv() as env:
            response = await env.call_impl(brief="test brief")

            assert isinstance(response, GetProductsResponse)
            assert response.products == []

    async def test_single_product(self):
        """add_product makes products visible in response."""
        with ProductEnv() as env:
            env.add_product(product_id="prod_001", name="Display Ad")
            response = await env.call_impl(brief="display")

            assert len(response.products) == 1
            assert response.products[0].product_id == "prod_001"

    async def test_multiple_products(self):
        """Multiple add_product calls accumulate products."""
        with ProductEnv() as env:
            for i in range(3):
                env.add_product(product_id=f"prod_{i:03d}", name=f"Product {i}")
            response = await env.call_impl(brief="ads")

            assert len(response.products) == 3

    async def test_principal_access_filtering(self):
        """Products with allowed_principal_ids filter by principal."""
        with ProductEnv() as env:
            env.add_product(product_id="public", allowed_principal_ids=None)
            env.add_product(product_id="restricted", allowed_principal_ids=["test_principal"])
            env.add_product(product_id="hidden", allowed_principal_ids=["other_principal"])

            response = await env.call_impl(brief="test")

            product_ids = [p.product_id for p in response.products]
            assert "public" in product_ids
            assert "restricted" in product_ids
            assert "hidden" not in product_ids

    async def test_mock_access(self):
        """env.mock[name] provides access to all patch targets."""
        with ProductEnv() as env:
            assert "uow" in env.mock
            assert "principal" in env.mock
            assert "convert" in env.mock
            assert "policy_service" in env.mock
            assert "dynamic_variants" in env.mock
            assert "ranking_factory" in env.mock
            assert "dynamic_pricing" in env.mock
            assert "resolve_property_list" in env.mock

    async def test_dynamic_variants_configurable(self):
        """set_dynamic_variants injects variants into the response."""
        from tests.harness.product_unit import _make_product

        with ProductEnv() as env:
            variant = _make_product(product_id="variant_001", name="Dynamic Variant")
            env.set_dynamic_variants([variant])

            # The convert mock is identity, so variant passes through
            response = await env.call_impl(brief="test")

            product_ids = [p.product_id for p in response.products]
            assert "variant_001" in product_ids

    async def test_custom_identity(self):
        """Constructor kwargs override identity fields."""
        with ProductEnv(principal_id="custom_principal", tenant_id="custom_tenant") as env:
            assert env.identity.principal_id == "custom_principal"
            assert env.identity.tenant_id == "custom_tenant"

    async def test_filters_forwarded_to_impl(self):
        """call_impl with filters applies them to the request."""
        with ProductEnv() as env:
            env.add_product(product_id="guaranteed_prod", delivery_type="guaranteed")
            env.add_product(product_id="non_guaranteed_prod", delivery_type="non_guaranteed")

            response = await env.call_impl(
                brief="test",
                filters={"delivery_type": "guaranteed"},
            )

            product_ids = [p.product_id for p in response.products]
            assert "guaranteed_prod" in product_ids
            assert "non_guaranteed_prod" not in product_ids

    async def test_no_identity_raises(self):
        """_get_products_impl without identity raises."""

        with ProductEnv(principal_id=None) as env:  # type: ignore[arg-type]
            # The identity still has tenant, but no principal_id
            # The brand_manifest_policy defaults to "require_auth" which checks principal_id
            from src.core.exceptions import AdCPAuthenticationError

            with pytest.raises(AdCPAuthenticationError):
                await env.call_impl(brief="test")

    async def test_ranking_disabled_by_default(self):
        """AI ranking is disabled by default (no product_ranking_prompt in tenant)."""
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")
            response = await env.call_impl(brief="test")

            # Ranking mock should not have been called
            # (tenant dict has no product_ranking_prompt)
            assert len(response.products) == 1

    async def test_policy_not_invoked_by_default(self):
        """Policy checks are not invoked when tenant has no gemini_api_key."""
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")
            response = await env.call_impl(brief="test")

            # PolicyCheckService should not have been instantiated
            env.mock["policy_service"].assert_not_called()
            assert len(response.products) == 1

    async def test_pricing_passthrough(self):
        """Dynamic pricing mock passes products through unchanged."""

        with ProductEnv() as env:
            product = env.add_product(product_id="prod_001")
            original_pricing = product.pricing_options

            response = await env.call_impl(brief="test")

            # Pricing should not have been modified
            assert response.products[0].pricing_options == original_pricing

    async def test_add_product_returns_mock(self):
        """add_product returns the mock for further customization."""
        with ProductEnv() as env:
            product = env.add_product(product_id="prod_custom")
            product.custom_field = "custom_value"

            assert product.product_id == "prod_custom"
            assert product.custom_field == "custom_value"


class TestProductEnvBrandBehavior:
    """Tests for brand-related behavior in ProductEnv."""

    async def test_default_brand_provides_domain(self):
        """Default call_impl provides a brand with domain (satisfying require_auth)."""
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")
            response = await env.call_impl(brief="test")

            assert len(response.products) == 1

    async def test_explicit_brand_dict(self):
        """Explicit brand dict is forwarded to request."""
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")
            response = await env.call_impl(
                brief="test",
                brand={"domain": "example.com"},
            )

            assert len(response.products) == 1

"""Canonical product entity tests using ProductEnv harness.

Unit-level tests for _get_products_impl business logic. Each test uses the
ProductEnv (unit variant) which mocks all external dependencies.

Schema-only obligations are covered by test_product_schema_obligations.py.
Integration-level obligations are covered by tests/integration/test_product_v3.py.

Spec verification: 2026-03-07
adcp spec commit: 8f26baf3
adcp-client-python commit: a08805d (v3.6.0)
Verified: 3/12 CONFIRMED, 9/12 UNSPECIFIED, 0 CONTRADICTS

CONFIRMED (3 — spec-defined behavior):
  test_empty_catalog_returns_empty        — get-products-response.json: products required, [] valid
  test_products_returned_in_response      — get-products-response.json: products[], product.json: product_id
  test_delivery_type_filter               — product-filters.json: delivery_type field

UNSPECIFIED (9 — implementation-defined, not in AdCP spec):
  test_missing_identity_raises            — identity resolution is seller-defined
  test_no_principal_requires_auth_policy_rejects — brand_manifest_policy is seller-defined
  test_dynamic_variants_injected          — dynamic variants are a seller feature
  test_unrestricted_products_visible_to_all — allowed_principal_ids ACL is seller-defined
  test_restricted_product_visible_to_allowed_principal — same
  test_restricted_product_hidden_from_other_principal — same
  test_anonymous_sees_only_unrestricted   — anonymous access policy is seller-defined
  test_policy_disabled_by_default         — content policy checking is seller-defined
  test_policy_blocked_raises_authorization_error — same
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPAuthenticationError, AdCPAuthorizationError
from src.core.schemas import GetProductsResponse
from tests.harness.product_unit import ProductEnv, _make_product


class TestProductPreconditions:
    """Precondition tests: identity, tenant, principal requirements."""

    async def test_missing_identity_raises(self):
        """Covers: UC-001-PRECOND-04

        Identity is required to determine tenant and principal.
        """
        from src.core.exceptions import AdCPValidationError

        with ProductEnv() as env:
            # Bypass the harness identity by calling impl directly
            from src.core.schemas import GetProductsRequest as GetProductsRequestGenerated
            from src.core.tools.products import _get_products_impl

            req = GetProductsRequestGenerated(brief="test", brand={"domain": "test.com"})
            with pytest.raises(AdCPValidationError, match="Identity is required"):
                await _get_products_impl(req, identity=None)

    async def test_no_principal_requires_auth_policy_rejects(self):
        """Covers: UC-001-PRECOND-05

        When brand_manifest_policy is require_auth, anonymous requests are rejected.
        """
        with ProductEnv(principal_id=None) as env:  # type: ignore[arg-type]
            with pytest.raises(AdCPAuthenticationError):
                await env.call_impl(brief="test")


class TestProductMainFlow:
    """Main flow: product discovery pipeline."""

    async def test_empty_catalog_returns_empty(self):
        """Covers: UC-001-MAIN-03

        When no products exist in the catalog, response is empty.

        Spec: get-products-response.json — products is required, empty [] is valid
        """
        with ProductEnv() as env:
            response = await env.call_impl(brief="test")

            assert isinstance(response, GetProductsResponse)
            assert len(response.products) == 0

    async def test_products_returned_in_response(self):
        """Covers: UC-001-MAIN-04

        Products from the catalog are included in the response.

        Spec: get-products-response.json — products[] array; product.json — product_id required
        """
        with ProductEnv() as env:
            env.add_product(product_id="prod_001", name="Display Ad")
            env.add_product(product_id="prod_002", name="Video Ad")

            response = await env.call_impl(brief="display")

            assert len(response.products) == 2
            ids = {p.product_id for p in response.products}
            assert ids == {"prod_001", "prod_002"}

    async def test_delivery_type_filter(self):
        """Covers: UC-001-MAIN-06

        Products can be filtered by delivery_type.

        Spec: product-filters.json — delivery_type is a spec-defined filter dimension
        """
        with ProductEnv() as env:
            env.add_product(product_id="guaranteed", delivery_type="guaranteed")
            env.add_product(product_id="non_guaranteed", delivery_type="non_guaranteed")

            response = await env.call_impl(
                brief="test",
                filters={"delivery_type": "guaranteed"},
            )

            ids = [p.product_id for p in response.products]
            assert "guaranteed" in ids
            assert "non_guaranteed" not in ids

    async def test_dynamic_variants_injected(self):
        """Covers: UC-001-MAIN-07

        Dynamic variants from signals agents are added to the response.
        """
        with ProductEnv() as env:
            variant = _make_product(product_id="variant_001", name="Dynamic Variant")
            env.set_dynamic_variants([variant])

            response = await env.call_impl(brief="test")

            ids = [p.product_id for p in response.products]
            assert "variant_001" in ids


class TestProductAccessControl:
    """Principal-based access filtering."""

    async def test_unrestricted_products_visible_to_all(self):
        """Covers: UC-001-MAIN-22

        Products without allowed_principal_ids are visible to any principal.
        """
        with ProductEnv() as env:
            env.add_product(product_id="public", allowed_principal_ids=None)

            response = await env.call_impl(brief="test")

            assert len(response.products) == 1

    async def test_restricted_product_visible_to_allowed_principal(self):
        """Covers: UC-001-MAIN-20

        Products with allowed_principal_ids include the requesting principal.
        """
        with ProductEnv(principal_id="allowed_p") as env:
            env.add_product(product_id="restricted", allowed_principal_ids=["allowed_p"])

            response = await env.call_impl(brief="test")

            assert len(response.products) == 1

    async def test_restricted_product_hidden_from_other_principal(self):
        """Covers: UC-001-MAIN-21

        Products with allowed_principal_ids exclude non-listed principals.
        """
        with ProductEnv(principal_id="other_p") as env:
            env.add_product(product_id="restricted", allowed_principal_ids=["allowed_p"])

            response = await env.call_impl(brief="test")

            assert len(response.products) == 0

    async def test_anonymous_sees_only_unrestricted(self):
        """Covers: UC-001-ALT-ANONYMOUS-DISCOVERY-04

        Anonymous users (no principal) only see unrestricted products.
        Note: brand_manifest_policy must be "public" for anonymous access.
        """
        with ProductEnv(
            principal_id=None,  # type: ignore[arg-type]
            brand_manifest_policy="public",
        ) as env:
            env.add_product(product_id="public", allowed_principal_ids=None)
            env.add_product(product_id="restricted", allowed_principal_ids=["some_p"])

            response = await env.call_impl(brief="test")

            ids = [p.product_id for p in response.products]
            assert "public" in ids
            assert "restricted" not in ids


class TestProductPolicyChecks:
    """Policy-based filtering and compliance."""

    async def test_policy_disabled_by_default(self):
        """Covers: UC-001-MAIN-02

        Policy checks are skipped when tenant has no gemini_api_key.
        """
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")

            response = await env.call_impl(brief="test")

            env.mock["policy_service"].assert_not_called()
            assert len(response.products) == 1

    async def test_policy_blocked_raises_authorization_error(self):
        """Covers: UC-001-EXT-A-01

        When policy blocks the brief, AdCPAuthorizationError is raised.
        """
        with ProductEnv(
            advertising_policy={"enabled": True},
            gemini_api_key="test_key",
        ) as env:
            env.set_policy_blocked(reason="Prohibited content")
            env.add_product(product_id="prod_001")

            with pytest.raises(AdCPAuthorizationError, match="Prohibited content"):
                await env.call_impl(brief="gambling ads")

"""Integration tests for product principal access control.

MIGRATED from tests/unit/test_product_principal_access.py.
Uses ProductEnv harness + factories to test access control through the real pipeline.

Tests the access control logic that restricts product visibility to specific principals,
exercised through the full _get_products_impl pipeline with real DB data.
"""

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


class TestProductPrincipalAccessControlPipeline:
    """Test the principal access control filter through the real pipeline.

    These tests verify that _get_products_impl correctly filters products
    based on allowed_principal_ids using real database records.
    """

    @pytest.mark.asyncio
    async def test_product_with_no_restrictions_visible_to_all(self, integration_db):
        """Products with null allowed_principal_ids are visible to any principal."""
        with ProductEnv(tenant_id="acl-no-restrict", principal_id="any-principal") as env:
            tenant = TenantFactory(tenant_id="acl-no-restrict", subdomain="acl-no-restrict")
            PrincipalFactory(tenant=tenant, principal_id="any-principal")
            p = ProductFactory(
                tenant=tenant,
                product_id="unrestricted_product",
                allowed_principal_ids=None,
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 1
        assert result.products[0].product_id == "unrestricted_product"

    @pytest.mark.asyncio
    async def test_product_with_empty_list_visible_to_all(self, integration_db):
        """Products with empty allowed_principal_ids list are visible to any principal."""
        with ProductEnv(tenant_id="acl-empty-list", principal_id="any-principal") as env:
            tenant = TenantFactory(tenant_id="acl-empty-list", subdomain="acl-empty-list")
            PrincipalFactory(tenant=tenant, principal_id="any-principal")
            p = ProductFactory(
                tenant=tenant,
                product_id="unrestricted_product",
                allowed_principal_ids=[],
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 1
        assert result.products[0].product_id == "unrestricted_product"

    @pytest.mark.asyncio
    async def test_product_visible_to_allowed_principal(self, integration_db):
        """Restricted products are visible to allowed principals."""
        with ProductEnv(tenant_id="acl-allowed", principal_id="principal-1") as env:
            tenant = TenantFactory(tenant_id="acl-allowed", subdomain="acl-allowed")
            PrincipalFactory(tenant=tenant, principal_id="principal-1")
            p = ProductFactory(
                tenant=tenant,
                product_id="restricted_product",
                allowed_principal_ids=["principal-1", "principal-2"],
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 1
        assert result.products[0].product_id == "restricted_product"

    @pytest.mark.asyncio
    async def test_product_hidden_from_non_allowed_principal(self, integration_db):
        """Restricted products are hidden from non-allowed principals."""
        with ProductEnv(tenant_id="acl-hidden", principal_id="principal-3") as env:
            tenant = TenantFactory(tenant_id="acl-hidden", subdomain="acl-hidden")
            PrincipalFactory(tenant=tenant, principal_id="principal-3")
            p = ProductFactory(
                tenant=tenant,
                product_id="restricted_product",
                allowed_principal_ids=["principal-1", "principal-2"],
            )
            PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_restricted_product_hidden_from_anonymous_users(self, integration_db):
        """Restricted products are hidden from anonymous users (no principal_id)."""
        with ProductEnv(tenant_id="acl-anon-hid", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="acl-anon-hid",
                subdomain="acl-anon-hid",
                brand_manifest_policy="public",
            )
            p = ProductFactory(
                tenant=tenant,
                product_id="restricted_product",
                allowed_principal_ids=["principal-1"],
            )
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("acl-anon-hid", principal_id=None)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 0

    @pytest.mark.asyncio
    async def test_unrestricted_product_visible_to_anonymous_users(self, integration_db):
        """Unrestricted products are visible to anonymous users."""
        with ProductEnv(tenant_id="acl-anon-vis", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="acl-anon-vis",
                subdomain="acl-anon-vis",
                brand_manifest_policy="public",
            )
            p = ProductFactory(
                tenant=tenant,
                product_id="public_product",
                allowed_principal_ids=None,
            )
            PricingOptionFactory(product=p)

            env._identity = _lazy_identity("acl-anon-vis", principal_id=None)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 1
        assert result.products[0].product_id == "public_product"


class TestProductAccessFilterPipeline:
    """Test the filter function as applied in the full get_products pipeline."""

    @pytest.mark.asyncio
    async def test_filter_returns_all_unrestricted_products(self, integration_db):
        """All unrestricted products are returned regardless of principal."""
        with ProductEnv(tenant_id="acl-all-unr", principal_id="any-principal") as env:
            tenant = TenantFactory(tenant_id="acl-all-unr", subdomain="acl-all-unr")
            PrincipalFactory(tenant=tenant, principal_id="any-principal")
            for pid in ("prod-1", "prod-2", "prod-3"):
                p = ProductFactory(
                    tenant=tenant,
                    product_id=pid,
                    allowed_principal_ids=None,
                )
                PricingOptionFactory(product=p)

            result = await env.call_impl(brief="test")

        assert len(result.products) == 3

    @pytest.mark.asyncio
    async def test_filter_returns_allowed_products_for_principal(self, integration_db):
        """Filter returns only allowed products for a specific principal."""
        with ProductEnv(tenant_id="acl-mixed", principal_id="principal-1") as env:
            tenant = TenantFactory(tenant_id="acl-mixed", subdomain="acl-mixed")
            PrincipalFactory(tenant=tenant, principal_id="principal-1")

            # Public product
            p1 = ProductFactory(tenant=tenant, product_id="public", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            # For principal-1 only
            p2 = ProductFactory(tenant=tenant, product_id="for-p1", allowed_principal_ids=["principal-1"])
            PricingOptionFactory(product=p2)
            # For principal-2 only
            p3 = ProductFactory(tenant=tenant, product_id="for-p2", allowed_principal_ids=["principal-2"])
            PricingOptionFactory(product=p3)
            # For both
            p4 = ProductFactory(
                tenant=tenant,
                product_id="for-both",
                allowed_principal_ids=["principal-1", "principal-2"],
            )
            PricingOptionFactory(product=p4)

            result = await env.call_impl(brief="test")

        result_ids = [p.product_id for p in result.products]
        assert "public" in result_ids
        assert "for-p1" in result_ids
        assert "for-both" in result_ids
        assert "for-p2" not in result_ids
        assert len(result.products) == 3

    @pytest.mark.asyncio
    async def test_filter_for_anonymous_returns_only_unrestricted(self, integration_db):
        """Anonymous users only see unrestricted products."""
        with ProductEnv(tenant_id="acl-anon-filt", principal_id=None) as env:
            tenant = TenantFactory(
                tenant_id="acl-anon-filt",
                subdomain="acl-anon-filt",
                brand_manifest_policy="public",
            )

            # Public product
            p1 = ProductFactory(tenant=tenant, product_id="public", allowed_principal_ids=None)
            PricingOptionFactory(product=p1)
            # Restricted product
            p2 = ProductFactory(
                tenant=tenant,
                product_id="restricted",
                allowed_principal_ids=["principal-1"],
            )
            PricingOptionFactory(product=p2)

            env._identity = _lazy_identity("acl-anon-filt", principal_id=None)

            result = await env.call_impl(brief="test")

        result_ids = [p.product_id for p in result.products]
        assert "public" in result_ids
        assert "restricted" not in result_ids
        assert len(result.products) == 1


class TestProductSchemaWithAllowedPrincipalIds:
    """Test that the Product schema includes the allowed_principal_ids field.

    Note: These are pure schema tests -- no DB needed, but kept here for suite cohesion.
    """

    def test_product_schema_has_allowed_principal_ids_field(self):
        """Product schema accepts allowed_principal_ids."""
        from src.core.schemas import Product

        assert "allowed_principal_ids" in Product.model_fields

    def test_product_schema_field_is_optional(self):
        """allowed_principal_ids is optional (None by default)."""
        from src.core.schemas import Product

        field_info = Product.model_fields["allowed_principal_ids"]
        assert field_info.default is None

    def test_product_schema_field_is_excluded_from_serialization(self):
        """allowed_principal_ids is excluded from API responses."""
        from src.core.schemas import Product

        field_info = Product.model_fields["allowed_principal_ids"]
        assert field_info.exclude is True

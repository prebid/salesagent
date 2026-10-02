#!/usr/bin/env python3
"""
Test brand_manifest_policy enforcement in get_products.

Tests verify that the three policy options work correctly:
- require_brand: Requires brand_manifest
- require_auth: Requires authentication, brand_manifest optional
- public: No requirements, brand_manifest and auth optional
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import BrandReference

from src.core.exceptions import AdCPAuthorizationError
from src.core.tools.products import _get_products_impl
from tests.factories.principal import PrincipalFactory

logger = logging.getLogger(__name__)


def _make_identity(principal_id, tenant):
    """The identity a get_products call arrives with.

    ``get_products`` is a PUBLIC tool, so it takes a ``PublicIdentity``: whoever reached
    it, or nobody. ``principal_id=None`` is the anonymous caller — a principal-less
    identity, not a ``ResolvedIdentity`` whose id is None. *tenant* has no default,
    because the type has none: there is no tenant-less identity to build.
    """
    return PrincipalFactory.make_public_identity(
        principal_id=principal_id,
        tenant_id=tenant["tenant_id"],
        tenant=tenant,
    )


@pytest.mark.asyncio
async def test_public_policy_allows_no_brand_manifest():
    """Test that public policy allows requests without brand_manifest."""
    # Create mock request without brand_manifest
    mock_request = MagicMock()
    mock_request.brand = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "public",
        "advertising_policy": {},
    }

    identity = _make_identity(principal_id=None, tenant=mock_tenant)

    # Mock all the dependencies
    with (
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.database.repositories.uow.ProductUoW") as mock_uow_cls,
    ):
        # Mock variants generation
        mock_generate_variants.return_value = []

        # Mock pricing service
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock ProductUoW (returns no products)
        mock_uow = MagicMock()
        mock_uow.products.list_all.return_value = []
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Call implementation - should NOT raise error
        response = await _get_products_impl(mock_request, identity)

        # Verify response is valid (no error)
        assert response is not None
        assert response.products == []


@pytest.mark.asyncio
async def test_require_brand_policy_rejects_no_brand_manifest():
    """Test that require_brand policy rejects requests without brand_manifest."""
    # Create mock request without brand_manifest
    mock_request = MagicMock()
    mock_request.brand = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_brand",
        "advertising_policy": {},
    }

    identity = _make_identity(principal_id="principal_123", tenant=mock_tenant)

    # No dependency mocks: the policy refusal happens before any catalog work, and the
    # principal the impl reads comes off the identity.
    # Call implementation - should raise AdCPAuthorizationError (transport-agnostic)
    with pytest.raises(AdCPAuthorizationError):
        await _get_products_impl(mock_request, identity)


@pytest.mark.asyncio
async def test_require_brand_policy_accepts_with_brand_manifest():
    """Test that require_brand policy accepts requests with brand (adcp 3.6.0: brand replaces brand_manifest)."""
    mock_request = MagicMock()
    # adcp 3.6.0: brand replaces brand_manifest; use BrandReference model
    mock_request.brand = BrandReference(domain="nike.com")
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_brand",
        "advertising_policy": {},
    }

    identity = _make_identity(principal_id="principal_123", tenant=mock_tenant)

    # Mock all dependencies
    with (
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.database.repositories.uow.ProductUoW") as mock_uow_cls,
    ):
        # Mock variants
        mock_generate_variants.return_value = []

        # Mock pricing
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock ProductUoW (returns no products)
        mock_uow = MagicMock()
        mock_uow.products.list_all.return_value = []
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Call implementation - should NOT raise error
        response = await _get_products_impl(mock_request, identity)

        # Verify response is valid
        assert response is not None


# The "require_auth" refusal is NOT gradeable at this level, and no test here attempts it.
# The anonymous caller of a public tool is a PublicIdentity, not a ResolvedIdentity with a
# None principal (ResolvedIdentity.principal is a required field), and _get_products_impl
# does not read the seller policy at all: the refusal is minted by the resolver, which asks
# ToolSpec.requires_credential(tenant) once the tenant row is loaded (registry.py:195).
# ruff-boundary.toml bans raising AUTH_MISSING / AUTH_INVALID anywhere but the resolver, so
# this implementation could not raise it even if a guard were written here.
#
# The obligation (BR-UC-001 INV-1: a require_auth seller makes get_products need a caller)
# is graded where it is decided -- the resolver, for every transport at once -- by the
# transport-blind auth scenarios asserting the AUTH_MISSING wire envelope.


@pytest.mark.asyncio
async def test_require_auth_policy_accepts_with_auth():
    """Test that require_auth policy accepts authenticated requests (brand_manifest optional)."""
    # Create mock request WITHOUT brand_manifest
    mock_request = MagicMock()
    mock_request.brand = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_auth",
        "advertising_policy": {},
    }

    identity = _make_identity(principal_id="principal_123", tenant=mock_tenant)

    # Mock all dependencies (the authenticated caller comes off the identity)
    with (
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.database.repositories.uow.ProductUoW") as mock_uow_cls,
    ):
        # Mock variants
        mock_generate_variants.return_value = []

        # Mock pricing
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock ProductUoW (returns no products)
        mock_uow = MagicMock()
        mock_uow.products.list_all.return_value = []
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Call implementation - should NOT raise error (brand_manifest optional)
        response = await _get_products_impl(mock_request, identity)

        # Verify response is valid
        assert response is not None

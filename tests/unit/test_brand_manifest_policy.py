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
from fastmcp.exceptions import ToolError

from src.core.tools.products import _get_products_impl

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_public_policy_allows_no_brand_manifest():
    """Test that public policy allows requests without brand_manifest."""
    # Create mock request without brand_manifest
    mock_request = MagicMock()
    mock_request.brand_manifest = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    # Create mock context with public policy
    mock_context = MagicMock()
    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "public",
        "advertising_policy": {},
    }

    # Mock all the dependencies
    with (
        patch("src.core.tools.products.get_principal_from_context") as mock_get_principal,
        patch("src.core.tools.products.get_principal_object") as mock_get_principal_obj,
        patch("src.core.tools.products.get_testing_context") as mock_get_testing,
        patch("src.core.tools.products.set_current_tenant") as mock_set_tenant,
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.tools.products.get_db_session") as mock_db_session,
    ):
        # Setup mocks
        mock_get_principal.return_value = (None, mock_tenant)  # No auth (anonymous)
        mock_get_principal_obj.return_value = None
        mock_get_testing.return_value = None

        # Mock variants generation
        mock_generate_variants.return_value = []

        # Mock pricing service
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock database session (returns no products)
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Call implementation - should NOT raise error
        response = await _get_products_impl(mock_request, mock_context)

        # Verify response is valid (no error)
        assert response is not None
        assert response.products == []


@pytest.mark.asyncio
async def test_require_brand_policy_rejects_no_brand_manifest():
    """Test that require_brand policy rejects requests without brand_manifest."""
    # Create mock request without brand_manifest
    mock_request = MagicMock()
    mock_request.brand_manifest = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    # Create mock context with require_brand policy
    mock_context = MagicMock()
    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_brand",
        "advertising_policy": {},
    }

    # Mock dependencies
    with (
        patch("src.core.tools.products.get_principal_from_context") as mock_get_principal,
        patch("src.core.tools.products.get_principal_object") as mock_get_principal_obj,
        patch("src.core.tools.products.get_testing_context") as mock_get_testing,
        patch("src.core.tools.products.set_current_tenant") as mock_set_tenant,
    ):
        # Setup mocks
        mock_get_principal.return_value = ("principal_123", mock_tenant)  # Authenticated
        mock_get_principal_obj.return_value = None
        mock_get_testing.return_value = None

        # Call implementation - should raise ToolError
        with pytest.raises(ToolError) as exc_info:
            await _get_products_impl(mock_request, mock_context)

        # Verify error message
        assert "Brand manifest required by tenant policy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_require_brand_policy_accepts_with_brand_manifest():
    """Test that require_brand policy accepts requests with brand_manifest."""
    # Create mock request WITH brand_manifest
    mock_brand_manifest = MagicMock()
    mock_brand_manifest.name = "Nike"
    mock_brand_manifest.url = "https://nike.com"

    mock_request = MagicMock()
    mock_request.brand_manifest = mock_brand_manifest
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    # Create mock context with require_brand policy
    mock_context = MagicMock()
    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_brand",
        "advertising_policy": {},
    }

    # Mock all dependencies
    with (
        patch("src.core.tools.products.get_principal_from_context") as mock_get_principal,
        patch("src.core.tools.products.get_principal_object") as mock_get_principal_obj,
        patch("src.core.tools.products.get_testing_context") as mock_get_testing,
        patch("src.core.tools.products.set_current_tenant") as mock_set_tenant,
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.tools.products.get_db_session") as mock_db_session,
    ):
        # Setup mocks
        mock_get_principal.return_value = ("principal_123", mock_tenant)
        mock_get_principal_obj.return_value = None
        mock_get_testing.return_value = None

        # Mock variants
        mock_generate_variants.return_value = []

        # Mock pricing
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock database session (returns no products)
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Call implementation - should NOT raise error
        response = await _get_products_impl(mock_request, mock_context)

        # Verify response is valid
        assert response is not None


@pytest.mark.asyncio
async def test_require_auth_policy_rejects_no_auth():
    """Test that require_auth policy rejects unauthenticated requests."""
    # Create mock request with brand_manifest
    mock_brand_manifest = MagicMock()
    mock_brand_manifest.name = "Nike"

    mock_request = MagicMock()
    mock_request.brand_manifest = mock_brand_manifest
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    # Create mock context with require_auth policy
    mock_context = MagicMock()
    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_auth",
        "advertising_policy": {},
    }

    # Mock dependencies
    with (
        patch("src.core.tools.products.get_principal_from_context") as mock_get_principal,
        patch("src.core.tools.products.get_testing_context") as mock_get_testing,
        patch("src.core.tools.products.set_current_tenant") as mock_set_tenant,
    ):
        # Setup mocks - NO authentication
        mock_get_principal.return_value = (None, mock_tenant)  # Anonymous
        mock_get_testing.return_value = None

        # Call implementation - should raise ToolError
        with pytest.raises(ToolError) as exc_info:
            await _get_products_impl(mock_request, mock_context)

        # Verify error message
        assert "Authentication required by tenant policy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_require_auth_policy_accepts_with_auth():
    """Test that require_auth policy accepts authenticated requests (brand_manifest optional)."""
    # Create mock request WITHOUT brand_manifest
    mock_request = MagicMock()
    mock_request.brand_manifest = None
    mock_request.brief = "Athletic footwear"
    mock_request.filters = None
    mock_request.context = None

    # Create mock context with require_auth policy
    mock_context = MagicMock()
    mock_tenant = {
        "tenant_id": "test_tenant",
        "brand_manifest_policy": "require_auth",
        "advertising_policy": {},
    }

    # Mock all dependencies
    with (
        patch("src.core.tools.products.get_principal_from_context") as mock_get_principal,
        patch("src.core.tools.products.get_principal_object") as mock_get_principal_obj,
        patch("src.core.tools.products.get_testing_context") as mock_get_testing,
        patch("src.core.tools.products.set_current_tenant") as mock_set_tenant,
        patch("src.services.dynamic_products.generate_variants_for_brief") as mock_generate_variants,
        patch("src.services.dynamic_pricing_service.DynamicPricingService") as mock_pricing_service,
        patch("src.core.tools.products.get_db_session") as mock_db_session,
    ):
        # Setup mocks - WITH authentication
        mock_get_principal.return_value = ("principal_123", mock_tenant)  # Authenticated
        mock_get_principal_obj.return_value = None
        mock_get_testing.return_value = None

        # Mock variants
        mock_generate_variants.return_value = []

        # Mock pricing
        mock_pricing_instance = MagicMock()
        mock_pricing_instance.enrich_products_with_pricing.return_value = []
        mock_pricing_service.return_value = mock_pricing_instance

        # Mock database session (returns no products)
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.unique.return_value.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_db_session.return_value.__enter__.return_value = mock_session

        # Call implementation - should NOT raise error (brand_manifest optional)
        response = await _get_products_impl(mock_request, mock_context)

        # Verify response is valid
        assert response is not None

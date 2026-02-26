"""Tests verifying adcp 3.6.0 brand migration: brand_manifest -> brand (BrandReference).

adcp 3.6.0 made the following breaking changes:
  - CreateMediaBuyRequest: brand_manifest REMOVED, brand (BrandReference) REQUIRED
  - GetProductsRequest: brand_manifest REMOVED, brand (BrandReference) added
  - BrandReference requires a 'domain' field (lowercase domain pattern)

These tests document the validation errors that occur when test code still uses
the old brand_manifest field, and confirm the correct brand (BrandReference) usage.
"""

import pytest
from pydantic import ValidationError


class TestCreateMediaBuyRequestBrandMigration:
    """CreateMediaBuyRequest: brand is REQUIRED, brand_manifest is REMOVED."""

    def test_brand_manifest_rejected_on_create_media_buy_request(self):
        """Constructing CreateMediaBuyRequest with brand_manifest raises ValidationError.

        This demonstrates the root cause of 20+ test failures after adcp 3.6.0 upgrade:
        brand_manifest is no longer a valid field and brand is now required.
        """
        from src.core.schemas import CreateMediaBuyRequest

        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(
                brand_manifest={"name": "Test Brand"},
                buyer_ref="test_buyer",
                packages=[],
                start_time="asap",
                end_time="2026-12-31T23:59:59Z",
            )

        errors = exc_info.value.errors()
        error_types = {(e["loc"], e["type"]) for e in errors}

        # Two distinct errors expected:
        # 1. brand_manifest is an extra (forbidden) input
        # 2. brand (BrandReference) is missing (required)
        assert any("brand_manifest" in str(e["loc"]) for e in errors), (
            "Expected 'brand_manifest' to be flagged as extra input"
        )
        assert any(e["type"] == "missing" and "brand" in str(e["loc"]) for e in errors), (
            "Expected 'brand' to be flagged as missing/required"
        )

    def test_brand_reference_accepted_on_create_media_buy_request(self):
        """Constructing CreateMediaBuyRequest with brand={'domain': '...'} succeeds.

        This is the correct pattern after adcp 3.6.0 migration.
        """
        from src.core.schemas import CreateMediaBuyRequest

        # Should NOT raise for the brand field (may raise for other missing fields
        # like packages, but brand itself should be accepted)
        request = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            buyer_ref="test_buyer",
            packages=[],
            start_time="asap",
            end_time="2026-12-31T23:59:59Z",
        )
        assert request.brand is not None
        assert request.brand.domain == "testbrand.com"

    def test_brand_field_is_required_on_create_media_buy_request(self):
        """CreateMediaBuyRequest without brand raises ValidationError for missing field."""
        from src.core.schemas import CreateMediaBuyRequest

        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(
                buyer_ref="test_buyer",
                packages=[],
                start_time="asap",
                end_time="2026-12-31T23:59:59Z",
            )

        errors = exc_info.value.errors()
        assert any(e["type"] == "missing" and "brand" in str(e["loc"]) for e in errors), (
            "Expected 'brand' to be required"
        )


class TestGetProductsRequestBrandMigration:
    """GetProductsRequest: brand_manifest REMOVED, brand (BrandReference) optional."""

    def test_brand_manifest_rejected_on_get_products_request(self):
        """Constructing GetProductsRequest with brand_manifest raises ValidationError.

        Our schema uses extra='forbid' in dev/CI, so brand_manifest (now an unknown
        field in adcp 3.6.0) triggers 'Extra inputs are not permitted'.
        """
        from src.core.schemas import GetProductsRequest

        with pytest.raises(ValidationError) as exc_info:
            GetProductsRequest(
                brief="test products",
                brand_manifest={"name": "Test Brand"},
            )

        errors = exc_info.value.errors()
        assert any("brand_manifest" in str(e["loc"]) for e in errors), (
            "Expected 'brand_manifest' to be flagged as extra input"
        )

    def test_brand_reference_accepted_on_get_products_request(self):
        """Constructing GetProductsRequest with brand={'domain': '...'} succeeds."""
        from src.core.schemas import GetProductsRequest

        request = GetProductsRequest(
            brief="test products",
            brand={"domain": "testbrand.com"},
        )
        # brand field should be populated
        assert request.brand is not None


class TestBrandReferenceValidation:
    """BrandReference requires a valid domain field."""

    def test_brand_reference_requires_domain(self):
        """BrandReference without 'domain' field is invalid."""
        from adcp.types.generated_poc.core.brand_ref import BrandReference

        with pytest.raises(ValidationError):
            BrandReference()  # type: ignore[call-arg]

    def test_brand_reference_domain_pattern(self):
        """BrandReference domain must match lowercase domain pattern."""
        from adcp.types.generated_poc.core.brand_ref import BrandReference

        # Valid domain
        br = BrandReference(domain="nike.com")
        assert br.domain == "nike.com"

        # Invalid domain (uppercase not allowed by pattern)
        with pytest.raises(ValidationError):
            BrandReference(domain="Nike.COM")

    def test_old_brand_manifest_dict_incompatible_with_brand_reference(self):
        """Old brand_manifest dict format {'name': '...'} cannot be used as BrandReference.

        This is the core incompatibility: tests used brand_manifest={'name': 'Nike'}
        but BrandReference requires {'domain': 'nike.com'}.
        """
        from adcp.types.generated_poc.core.brand_ref import BrandReference

        with pytest.raises(ValidationError):
            # Old format: {"name": "Nike"} - missing required 'domain' field
            BrandReference(**{"name": "Nike"})


class TestFactoryProducesBrandReference:
    """Verify that the test factory already produces correct brand format."""

    def test_factory_uses_brand_not_brand_manifest(self):
        """create_test_media_buy_request_dict uses brand with domain, not brand_manifest."""
        from tests.helpers.adcp_factories import create_test_media_buy_request_dict

        request_dict = create_test_media_buy_request_dict()
        assert "brand" in request_dict, "Factory should produce 'brand' field"
        assert "brand_manifest" not in request_dict, "Factory should NOT produce 'brand_manifest'"
        assert "domain" in request_dict["brand"], "brand should have 'domain' field"

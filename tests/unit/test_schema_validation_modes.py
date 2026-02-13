"""Test schema validation modes (production vs development).

Validation mode is set at class definition time via get_pydantic_extra_mode():
- Dev/test (default): extra='forbid' — rejects unknown fields with ValidationError
- Production (ENVIRONMENT=production): extra='ignore' — silently drops unknown fields

To test production-mode behavior, run:
    ENVIRONMENT=production pytest tests/unit/test_schema_validation_modes.py -v
"""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    CreateMediaBuyRequest,
    Creative,
    GetMediaBuyDeliveryRequest,
    GetProductsRequest,
    ListCreativeFormatsRequest,
    ListCreativesRequest,
    PackageRequest,
    Targeting,
)

# Minimal valid data for constructing test models
_VALID_CMR_DATA = {
    "buyer_ref": "test-123",
    "brand_manifest": {"name": "Test Product"},
    "packages": [
        {
            "buyer_ref": "pkg_1",
            "product_id": "prod_1",
            "budget": 5000.0,
            "pricing_option_id": "test",
        }
    ],
    "start_time": "2025-02-15T00:00:00Z",
    "end_time": "2025-02-28T23:59:59Z",
}

_VALID_PACKAGE_DATA = {
    "buyer_ref": "pkg_1",
    "product_id": "prod_1",
    "budget": 5000.0,
    "pricing_option_id": "test",
}


class TestBuyerModelRejectsExtraInDev:
    """All buyer-facing request models reject unknown fields in dev mode (default)."""

    def test_create_media_buy_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            CreateMediaBuyRequest(**_VALID_CMR_DATA, bogus="injected")

    def test_package_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            PackageRequest(**_VALID_PACKAGE_DATA, bogus="injected")

    def test_targeting_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            Targeting(geo_country_any_of=["US"], bogus="injected")

    def test_creative_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            Creative(
                creative_id="c_1",
                name="Test",
                format_id={"agent_url": "https://example.com", "id": "display/banner"},
                bogus="injected",
            )

    def test_list_creative_formats_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            ListCreativeFormatsRequest(bogus="injected")

    def test_list_creatives_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            ListCreativesRequest(bogus="injected")

    def test_get_media_buy_delivery_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="bogus"):
            GetMediaBuyDeliveryRequest(bogus="injected")


class TestNestedModelRejectsExtraInDev:
    """Extra fields on nested models within CreateMediaBuyRequest are rejected."""

    def test_nested_package_rejects_extra(self):
        """Bogus field on PackageRequest within CMR.packages is rejected."""
        data = {
            **_VALID_CMR_DATA,
            "packages": [{**_VALID_PACKAGE_DATA, "bogus_pkg_field": "injected"}],
        }
        with pytest.raises(ValidationError, match="bogus_pkg_field"):
            CreateMediaBuyRequest(**data)

    def test_nested_targeting_rejects_extra(self):
        """Bogus field on targeting_overlay within a package is rejected."""
        data = {
            **_VALID_CMR_DATA,
            "packages": [
                {
                    **_VALID_PACKAGE_DATA,
                    "targeting_overlay": {
                        "geo_country_any_of": ["US"],
                        "bogus_targeting": "injected",
                    },
                }
            ],
        }
        with pytest.raises(ValidationError, match="bogus_targeting"):
            CreateMediaBuyRequest(**data)


class TestExtFieldAccepted:
    """The AdCP ext field is the sanctioned extension mechanism and must be accepted."""

    def test_ext_field_accepted_on_cmr(self):
        cmr = CreateMediaBuyRequest(
            **_VALID_CMR_DATA,
            ext={"vendor": {"custom": "value"}},
        )
        assert cmr.ext is not None


class TestInternalModelsRejectExtra:
    """Models inheriting from our AdCPBaseModel also reject extra fields in dev."""

    def test_get_products_request_rejects_extra(self):
        with pytest.raises(ValidationError, match="unknown_field"):
            GetProductsRequest(
                brief="test",
                brand_manifest={"name": "test"},
                unknown_field="should_fail",
            )


class TestConfigHelperFunctions:
    """Test the config helper functions directly."""

    def test_development_mode(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

    def test_production_mode(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            assert is_production()
            assert get_pydantic_extra_mode() == "ignore"

    def test_staging_defaults_to_strict(self):
        from src.core.config import get_pydantic_extra_mode, is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

    def test_case_insensitive(self):
        from src.core.config import is_production

        with patch.dict(os.environ, {"ENVIRONMENT": "PRODUCTION"}):
            assert is_production()
        with patch.dict(os.environ, {"ENVIRONMENT": "Production"}):
            assert is_production()

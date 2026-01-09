"""Test schema validation modes (production vs development)."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.schemas import CreateMediaBuyRequest, GetProductsRequest


class TestSchemaValidationModes:
    """Test that validation strictness changes based on environment."""

    def test_development_mode_rejects_extra_fields(self):
        """Development mode (default) should reject unknown fields."""
        # Default ENVIRONMENT is not set, so should be "development" mode
        with patch.dict(os.environ, {}, clear=False):
            # Remove ENVIRONMENT if it exists
            os.environ.pop("ENVIRONMENT", None)

            # Try to create request with extra field
            with pytest.raises(ValidationError) as exc_info:
                GetProductsRequest(brief="test", brand_manifest={"name": "test"}, unknown_field="should_fail")

            # Verify it's complaining about the extra field
            assert "unknown_field" in str(exc_info.value)

    def test_production_mode_ignores_extra_fields(self):
        """Production mode should silently ignore unknown fields."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            # This should NOT raise - extra field should be ignored
            request = GetProductsRequest(
                brief="test", brand_manifest={"name": "test"}, unknown_field="should_be_ignored"
            )

            # Verify the valid fields work
            assert request.brief == "test"
            # Library may wrap in BrandManifestReference with BrandManifest in root
            if hasattr(request.brand_manifest, "name"):
                assert request.brand_manifest.name == "test"
            elif hasattr(request.brand_manifest, "root") and hasattr(request.brand_manifest.root, "name"):
                assert request.brand_manifest.root.name == "test"

            # Verify unknown field was dropped
            assert not hasattr(request, "unknown_field")

    def test_adcp_version_ignored_in_schema(self):
        """Schema extending library type ignores extra fields (library uses extra=allow).

        As of adcp 2.18.0, library types use extra="allow" for forward compatibility.
        Extra fields are accepted but not stored as model attributes.
        """
        # Extra field should be accepted but ignored
        request = CreateMediaBuyRequest(
            buyer_ref="test-123",
            brand_manifest={"name": "Test Product"},
            packages=[
                {"buyer_ref": "pkg_1", "product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test"}
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            adcp_version="1.8.0",  # Extra field - accepted but ignored
        )

        # Verify valid fields work
        assert request.buyer_ref == "test-123"
        # Extra field is in model_extra, not as attribute
        assert request.model_extra.get("adcp_version") == "1.8.0"

    def test_create_media_buy_ignores_extra_fields(self):
        """CreateMediaBuyRequest ignores extra fields (library uses extra=allow).

        As of adcp 2.18.0, library types use extra="allow" for forward compatibility.
        This allows protocol evolution without breaking existing clients.
        """
        # Extra field should be accepted but ignored
        request = CreateMediaBuyRequest(
            buyer_ref="test-123",
            brand_manifest={"name": "Test Product"},
            packages=[
                {"buyer_ref": "pkg_1", "product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "test"}
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            future_field="should_be_ignored",
        )

        # Verify valid fields work
        assert request.buyer_ref == "test-123"
        # Extra field is in model_extra, not as attribute
        assert request.model_extra.get("future_field") == "should_be_ignored"

    def test_environment_case_insensitive(self):
        """ENVIRONMENT variable should be case-insensitive."""
        # Test uppercase
        with patch.dict(os.environ, {"ENVIRONMENT": "PRODUCTION"}):
            request = GetProductsRequest(brief="test", brand_manifest={"name": "test"}, extra="ignored")
            assert request.brief == "test"

        # Test mixed case
        with patch.dict(os.environ, {"ENVIRONMENT": "Production"}):
            request = GetProductsRequest(brief="test", brand_manifest={"name": "test"}, extra="ignored")
            assert request.brief == "test"

    def test_staging_environment_defaults_to_strict(self):
        """Staging environment should use strict validation (not production)."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            # Should behave like development (strict)
            with pytest.raises(ValidationError):
                GetProductsRequest(brief="test", brand_manifest={"name": "test"}, unknown_field="should_fail")

    def test_config_helper_functions(self):
        """Test the config helper functions directly."""
        from src.core.config import get_pydantic_extra_mode, is_production

        # Test development mode
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

        # Test production mode
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            assert is_production()
            assert get_pydantic_extra_mode() == "ignore"

        # Test staging mode
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            assert not is_production()
            assert get_pydantic_extra_mode() == "forbid"

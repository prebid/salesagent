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
                GetProductsRequest(brief="test", promoted_offering="test", unknown_field="should_fail")

            # Verify it's complaining about the extra field
            assert "unknown_field" in str(exc_info.value)

    def test_production_mode_ignores_extra_fields(self):
        """Production mode should silently ignore unknown fields."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            # This should NOT raise - extra field should be ignored
            request = GetProductsRequest(brief="test", promoted_offering="test", unknown_field="should_be_ignored")

            # Verify the valid fields work
            assert request.brief == "test"
            assert request.promoted_offering == "test"

            # Verify unknown field was dropped
            assert not hasattr(request, "unknown_field")

    def test_adcp_version_accepted_in_production(self):
        """Production mode should accept future schema fields like adcp_version."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            # This was failing before - client sending adcp_version from newer schema
            request = CreateMediaBuyRequest(
                buyer_ref="test-123",
                promoted_offering="Test Product",
                packages=[],
                adcp_version="1.8.0",  # Future field from v1.8.0 schema
            )

            assert request.buyer_ref == "test-123"
            # adcp_version might be stored or ignored depending on schema definition

    def test_create_media_buy_with_extra_fields_production(self):
        """CreateMediaBuyRequest should accept extra fields in production."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            request = CreateMediaBuyRequest(
                buyer_ref="test-123",
                promoted_offering="Test Product",
                packages=[],
                future_field="from_v2.0",  # Field that doesn't exist yet
                another_future_field=123,
            )

            assert request.buyer_ref == "test-123"
            # Extra fields should be silently dropped

    def test_create_media_buy_rejects_extra_fields_development(self):
        """CreateMediaBuyRequest should reject extra fields in development."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENVIRONMENT", None)  # Default to development

            with pytest.raises(ValidationError) as exc_info:
                CreateMediaBuyRequest(
                    buyer_ref="test-123",
                    promoted_offering="Test Product",
                    packages=[],
                    future_field="should_fail",
                )

            assert "future_field" in str(exc_info.value)

    def test_environment_case_insensitive(self):
        """ENVIRONMENT variable should be case-insensitive."""
        # Test uppercase
        with patch.dict(os.environ, {"ENVIRONMENT": "PRODUCTION"}):
            request = GetProductsRequest(brief="test", promoted_offering="test", extra="ignored")
            assert request.brief == "test"

        # Test mixed case
        with patch.dict(os.environ, {"ENVIRONMENT": "Production"}):
            request = GetProductsRequest(brief="test", promoted_offering="test", extra="ignored")
            assert request.brief == "test"

    def test_staging_environment_defaults_to_strict(self):
        """Staging environment should use strict validation (not production)."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            # Should behave like development (strict)
            with pytest.raises(ValidationError):
                GetProductsRequest(brief="test", promoted_offering="test", unknown_field="should_fail")

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

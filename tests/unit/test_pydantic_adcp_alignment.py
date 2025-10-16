"""Tests to ensure Pydantic models accept all AdCP-valid fields.

This test suite verifies that our Pydantic request models accept all fields
defined in the official AdCP JSON schemas. This prevents validation errors
when clients send spec-compliant requests.

The tests validate the critical gap between:
1. AdCP JSON Schema validation (what the spec allows)
2. Pydantic model validation (what our code accepts)

These tests caught the bug where GetProductsRequest didn't accept `filters`
and `adcp_version` fields even though they're valid per AdCP spec.
"""

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    FormatId,
    GetProductsRequest,
    ProductFilters,
)


class TestGetProductsRequestAlignment:
    """Test that GetProductsRequest accepts all AdCP-valid fields."""

    def test_minimal_required_fields(self):
        """Test with only required fields per AdCP spec."""
        req = GetProductsRequest(promoted_offering="Nike Air Jordan 2025 basketball shoes")

        assert req.promoted_offering == "Nike Air Jordan 2025 basketball shoes"
        assert req.brief == ""  # Default value
        assert req.adcp_version == "1.0.0"  # Default value
        assert req.filters is None  # Optional field

    def test_with_all_optional_fields(self):
        """Test with all optional fields that AdCP spec allows."""
        req = GetProductsRequest(
            promoted_offering="Acme Corp enterprise software",
            brief="Looking for display advertising on tech sites",
            adcp_version="1.6.0",
            filters=ProductFilters(
                delivery_type="guaranteed",
                is_fixed_price=True,
                format_types=["video", "display"],
                format_ids=[
                    FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                    FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_30s"),
                ],
                standard_formats_only=False,
            ),
        )

        assert req.promoted_offering == "Acme Corp enterprise software"
        assert req.brief == "Looking for display advertising on tech sites"
        assert req.adcp_version == "1.6.0"
        assert req.filters is not None
        assert req.filters.delivery_type == "guaranteed"
        assert req.filters.is_fixed_price is True
        assert req.filters.format_types == ["video", "display"]
        assert len(req.filters.format_ids) == 2
        assert req.filters.format_ids[0].id == "display_300x250"
        assert req.filters.format_ids[1].id == "video_30s"
        assert req.filters.standard_formats_only is False

    def test_filters_as_dict(self):
        """Test that filters can be provided as dict (JSON deserialization pattern)."""
        req = GetProductsRequest(
            promoted_offering="Tesla Model Y electric vehicle",
            filters={
                "delivery_type": "non_guaranteed",
                "format_types": ["video"],
                "is_fixed_price": False,
            },
        )

        assert req.filters is not None
        assert req.filters.delivery_type == "non_guaranteed"
        assert req.filters.format_types == ["video"]
        assert req.filters.is_fixed_price is False

    def test_partial_filters(self):
        """Test with only some filter fields (all filters are optional)."""
        req = GetProductsRequest(
            promoted_offering="Spotify Premium music streaming", filters=ProductFilters(delivery_type="guaranteed")
        )

        assert req.filters is not None
        assert req.filters.delivery_type == "guaranteed"
        assert req.filters.is_fixed_price is None
        assert req.filters.format_types is None

    def test_adcp_version_validation(self):
        """Test that adcp_version validates format per spec (X.Y.Z pattern)."""
        # Valid versions
        valid_versions = ["1.0.0", "1.6.0", "2.0.0", "10.5.3"]
        for version in valid_versions:
            req = GetProductsRequest(promoted_offering="Test product", adcp_version=version)
            assert req.adcp_version == version

        # Invalid version format (should fail validation)
        with pytest.raises(ValidationError, match="pattern"):
            GetProductsRequest(promoted_offering="Test product", adcp_version="1.0")  # Missing patch version

    def test_filters_format_types_enum(self):
        """Test that format_types accepts valid enum values per AdCP spec."""
        # AdCP spec only supports: video, display, audio (no native)
        valid_types = ["video", "display", "audio"]

        for format_type in valid_types:
            req = GetProductsRequest(
                promoted_offering="Test product", filters=ProductFilters(format_types=[format_type])
            )
            assert format_type in req.filters.format_types

    def test_filters_delivery_type_values(self):
        """Test that delivery_type accepts valid values per AdCP spec."""
        # Guaranteed products
        req1 = GetProductsRequest(promoted_offering="Test product", filters=ProductFilters(delivery_type="guaranteed"))
        assert req1.filters.delivery_type == "guaranteed"

        # Non-guaranteed products
        req2 = GetProductsRequest(
            promoted_offering="Test product", filters=ProductFilters(delivery_type="non_guaranteed")
        )
        assert req2.filters.delivery_type == "non_guaranteed"


class TestProductFiltersModel:
    """Test ProductFilters Pydantic model independently."""

    def test_empty_filters(self):
        """Test that ProductFilters can be created with no fields (all optional)."""
        filters = ProductFilters()

        assert filters.delivery_type is None
        assert filters.is_fixed_price is None
        assert filters.format_types is None
        assert filters.format_ids is None
        assert filters.standard_formats_only is None

    def test_single_field_filters(self):
        """Test filters with only one field set."""
        filters = ProductFilters(delivery_type="guaranteed")
        assert filters.delivery_type == "guaranteed"
        assert filters.is_fixed_price is None

    def test_boolean_filters(self):
        """Test boolean filter fields (is_fixed_price, standard_formats_only)."""
        filters = ProductFilters(is_fixed_price=True, standard_formats_only=False)

        assert filters.is_fixed_price is True
        assert filters.standard_formats_only is False

    def test_array_filters(self):
        """Test array filter fields (format_types, format_ids)."""
        filters = ProductFilters(
            format_types=["video", "display", "audio"],
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_30s"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="audio_15s"),
            ],
        )

        assert len(filters.format_types) == 3
        assert "video" in filters.format_types
        assert len(filters.format_ids) == 3
        assert filters.format_ids[0].id == "display_300x250"

    def test_model_dump_excludes_none(self):
        """Test that model_dump with exclude_none only includes set fields."""
        filters = ProductFilters(delivery_type="guaranteed", is_fixed_price=True)

        dumped = filters.model_dump(exclude_none=True)

        assert "delivery_type" in dumped
        assert "is_fixed_price" in dumped
        assert "format_types" not in dumped  # Was None
        assert "format_ids" not in dumped  # Was None


class TestAdCPSchemaCompatibility:
    """Test compatibility with actual AdCP schema examples."""

    def test_example_from_adcp_spec_1(self):
        """Test example from test_adcp_schema_compliance.py line 149."""
        # This is the exact example that was passing JSON schema validation
        # but would have failed Pydantic validation before our fix
        req = GetProductsRequest(
            promoted_offering="mobile apps", filters={"format_types": ["video"], "is_fixed_price": True}
        )

        assert req.promoted_offering == "mobile apps"
        assert req.filters.format_types == ["video"]
        assert req.filters.is_fixed_price is True

    def test_example_minimal_adcp_request(self):
        """Test minimal valid request per AdCP spec."""
        req = GetProductsRequest(promoted_offering="eco-friendly products")

        assert req.promoted_offering == "eco-friendly products"
        assert req.brief == ""
        assert req.adcp_version == "1.0.0"
        assert req.filters is None

    def test_example_with_brief(self):
        """Test request with brief field."""
        req = GetProductsRequest(brief="display advertising", promoted_offering="eco-friendly products")

        assert req.brief == "display advertising"
        assert req.promoted_offering == "eco-friendly products"

    def test_example_multiple_filter_fields(self):
        """Test request with multiple filter fields."""
        req = GetProductsRequest(
            promoted_offering="premium video content",
            filters={
                "delivery_type": "non_guaranteed",
                "format_types": ["video"],
                "format_ids": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            },
        )

        assert req.filters.delivery_type == "non_guaranteed"
        assert req.filters.format_types == ["video"]
        assert len(req.filters.format_ids) == 2
        assert req.filters.format_ids[0].id == "video_30s"
        assert req.filters.format_ids[1].id == "video_15s"


class TestRegressionPrevention:
    """Tests to prevent regression of the original bug."""

    def test_client_can_send_filters(self):
        """
        Regression test for the bug reported by Wonderstruck client.

        The client was sending:
        {
          "promoted_offering": "cat food",
          "brief": "video ads",
          "adcp_version": "1.6.0",
          "filters": {
            "delivery_type": "guaranteed",
            "format_types": ["video"],
            "is_fixed_price": true
          }
        }

        This should NOT raise a Pydantic validation error.
        """
        try:
            req = GetProductsRequest(
                promoted_offering="cat food",
                brief="video ads",
                adcp_version="1.6.0",
                filters={
                    "delivery_type": "guaranteed",
                    "format_types": ["video"],
                    "is_fixed_price": True,
                },
            )
            # If we get here, the bug is fixed
            assert req.promoted_offering == "cat food"
            assert req.adcp_version == "1.6.0"
            assert req.filters is not None
        except ValidationError as e:
            pytest.fail(f"GetProductsRequest should accept AdCP-valid fields. Error: {e}")

    def test_client_can_send_adcp_version(self):
        """Test that clients can send adcp_version field."""
        req = GetProductsRequest(promoted_offering="test product", adcp_version="1.6.0")
        assert req.adcp_version == "1.6.0"

    def test_wonderstruck_exact_payload(self):
        """
        Test the exact payload structure that was failing for Wonderstruck.

        Before fix: Pydantic raised "Unexpected keyword argument 'filters'"
        After fix: Should create request successfully
        """
        # Exact structure from Wonderstruck's client
        payload = {
            "promoted_offering": "purina cat food",
            "brief": "video advertising campaigns",
            "adcp_version": "1.6.0",
            "filters": {"delivery_type": "guaranteed", "format_types": ["video"], "is_fixed_price": True},
        }

        # This should NOT raise ValidationError
        req = GetProductsRequest(**payload)

        assert req.promoted_offering == "purina cat food"
        assert req.brief == "video advertising campaigns"
        assert req.adcp_version == "1.6.0"
        assert req.filters.delivery_type == "guaranteed"
        assert req.filters.format_types == ["video"]
        assert req.filters.is_fixed_price is True

"""Tests to ensure Pydantic models accept all AdCP-valid fields.

This test suite verifies that our Pydantic request models accept all fields
defined in the official AdCP JSON schemas. This prevents validation errors
when clients send spec-compliant requests.

The tests validate the critical gap between:
1. AdCP JSON Schema validation (what the spec allows)
2. Pydantic model validation (what our code accepts)

These tests caught the bug where GetProductsRequest didn't accept `filters`
and `adcp_version` fields even though they're valid per AdCP spec.

adcp 3.6.0 update: brand_manifest replaced by brand (BrandReference with required domain).
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
        """Test with only required fields per AdCP spec.

        Per AdCP spec, ALL fields in GetProductsRequest are optional.
        """
        # Empty request is valid per spec
        empty_req = GetProductsRequest()
        assert empty_req.brand is None
        assert empty_req.brief is None
        assert empty_req.filters is None

        # With brand only (adcp 3.6.0: brand replaced brand_manifest)
        req = GetProductsRequest(brand={"domain": "nike.com"})
        # Local schema may store as dict, library coerces to BrandReference
        assert req.brand is not None
        assert req.brief is None  # Optional, defaults to None
        assert req.filters is None

    def test_with_all_optional_fields(self):
        """Test with all optional fields that AdCP spec allows."""
        req = GetProductsRequest(
            brand={"domain": "acme.com"},
            brief="Looking for display advertising on tech sites",
            filters=ProductFilters(
                delivery_type="guaranteed",
                format_types=["video", "display"],
                format_ids=[
                    FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                    FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_30s"),
                ],
                standard_formats_only=False,
            ),
        )

        # brand is stored (either as dict or BrandReference)
        assert req.brand is not None
        assert req.brief == "Looking for display advertising on tech sites"
        assert req.filters is not None
        assert req.filters.delivery_type.value == "guaranteed"
        # format_types are stored as enum objects internally but serialize to strings
        assert [ft.value for ft in req.filters.format_types] == ["video", "display"]
        assert len(req.filters.format_ids) == 2
        assert req.filters.format_ids[0].id == "display_300x250"
        assert req.filters.format_ids[1].id == "video_30s"
        assert req.filters.standard_formats_only is False

    def test_filters_as_dict(self):
        """Test that filters can be provided as dict (JSON deserialization pattern)."""
        req = GetProductsRequest(
            brand={"domain": "tesla.com"},
            filters={
                "delivery_type": "non_guaranteed",
                "format_types": ["video"],
            },
        )

        assert req.filters is not None
        # Library uses enum for delivery_type
        assert req.filters.delivery_type.value == "non_guaranteed"
        assert [ft.value for ft in req.filters.format_types] == ["video"]

    def test_partial_filters(self):
        """Test with only some filter fields (all filters are optional)."""
        req = GetProductsRequest(
            brand={"domain": "spotify.com"},
            filters=ProductFilters(delivery_type="guaranteed"),
        )

        assert req.filters is not None
        assert req.filters.delivery_type.value == "guaranteed"
        assert req.filters.format_types is None

    def test_filters_format_types_enum(self):
        """Test that format_types accepts valid enum values per AdCP spec."""
        # AdCP spec only supports: video, display, audio (no native)
        valid_types = ["video", "display", "audio"]

        for format_type in valid_types:
            req = GetProductsRequest(
                brand={"domain": "testbrand.com"}, filters=ProductFilters(format_types=[format_type])
            )
            # format_types are stored as enum objects, check enum value
            assert format_type in [ft.value for ft in req.filters.format_types]

    def test_filters_delivery_type_values(self):
        """Test that delivery_type accepts valid values per AdCP spec."""
        # Guaranteed products
        req1 = GetProductsRequest(brand={"domain": "testbrand.com"}, filters=ProductFilters(delivery_type="guaranteed"))
        assert req1.filters.delivery_type.value == "guaranteed"

        # Non-guaranteed products
        req2 = GetProductsRequest(
            brand={"domain": "testbrand.com"}, filters=ProductFilters(delivery_type="non_guaranteed")
        )
        assert req2.filters.delivery_type.value == "non_guaranteed"


class TestProductFiltersModel:
    """Test ProductFilters Pydantic model independently."""

    def test_empty_filters(self):
        """Test that ProductFilters can be created with no fields (all optional)."""
        filters = ProductFilters()

        assert filters.delivery_type is None
        assert filters.format_types is None
        assert filters.format_ids is None
        assert filters.standard_formats_only is None

    def test_single_field_filters(self):
        """Test filters with only one field set."""
        filters = ProductFilters(delivery_type="guaranteed")
        assert filters.delivery_type.value == "guaranteed"

    def test_boolean_filters(self):
        """Test boolean filter fields (standard_formats_only)."""
        filters = ProductFilters(standard_formats_only=False)

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
        # format_types are stored as enum objects, convert to strings for comparison
        assert "video" in [ft.value for ft in filters.format_types]
        assert len(filters.format_ids) == 3
        assert filters.format_ids[0].id == "display_300x250"

    def test_model_dump_excludes_none(self):
        """Test that model_dump with exclude_none only includes set fields."""
        filters = ProductFilters(delivery_type="guaranteed", standard_formats_only=True)

        dumped = filters.model_dump(exclude_none=True)

        assert "delivery_type" in dumped
        assert "standard_formats_only" in dumped
        assert "format_types" not in dumped  # Was None
        assert "format_ids" not in dumped  # Was None


class TestAdCPSchemaCompatibility:
    """Test compatibility with actual AdCP schema examples."""

    def test_example_from_adcp_spec_1(self):
        """Test example from test_adcp_schema_compliance.py line 149.

        adcp 3.6.0: brand_manifest replaced by brand (BrandReference with domain).
        """
        # This is the updated example - using brand (BrandReference) instead of brand_manifest
        req = GetProductsRequest(brand={"domain": "mobileapps.com"}, filters={"format_types": ["video"]})

        assert req.brand is not None
        assert [ft.value for ft in req.filters.format_types] == ["video"]

    def test_example_minimal_adcp_request(self):
        """Test minimal valid request per AdCP spec.

        Per AdCP spec, all fields are optional - even brand.
        """
        # Empty request is valid
        empty_req = GetProductsRequest()
        assert empty_req.brand is None
        assert empty_req.brief is None
        assert empty_req.filters is None

        # Brand only
        req = GetProductsRequest(brand={"domain": "eco-products.com"})
        assert req.brand is not None
        assert req.brief is None  # Optional, defaults to None
        assert req.filters is None

    def test_example_with_brief(self):
        """Test request with brief field."""
        req = GetProductsRequest(brief="display advertising", brand={"domain": "eco-products.com"})

        assert req.brief == "display advertising"
        assert req.brand is not None

    def test_example_multiple_filter_fields(self):
        """Test request with multiple filter fields."""
        req = GetProductsRequest(
            brand={"domain": "premium-video.com"},
            filters={
                "delivery_type": "non_guaranteed",
                "format_types": ["video"],
                "format_ids": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            },
        )

        assert req.filters.delivery_type.value == "non_guaranteed"
        assert [ft.value for ft in req.filters.format_types] == ["video"]
        assert len(req.filters.format_ids) == 2
        assert req.filters.format_ids[0].id == "video_30s"
        assert req.filters.format_ids[1].id == "video_15s"


class TestRegressionPrevention:
    """Tests to prevent regression of schema compliance."""

    def test_client_can_send_filters(self):
        """
        Regression test: clients can send filters in get_products request.

        Per AdCP spec, filters is an optional field for product filtering.
        """
        try:
            req = GetProductsRequest(
                brand={"domain": "catfood.com"},
                brief="video ads",
                filters={
                    "delivery_type": "guaranteed",
                    "format_types": ["video"],
                },
            )
            assert req.brand is not None
            assert req.brief == "video ads"
            assert req.filters is not None
            assert req.filters.delivery_type.value == "guaranteed"
        except ValidationError as e:
            pytest.fail(f"GetProductsRequest should accept AdCP-valid fields. Error: {e}")

    def test_all_fields_optional(self):
        """Test that all GetProductsRequest fields are optional per spec."""
        # Empty request is valid
        req = GetProductsRequest()
        assert req.brand is None
        assert req.brief is None
        assert req.filters is None

    def test_spec_compliant_payload(self):
        """
        Test a full payload with all supported AdCP spec fields.

        adcp 3.6.0: brand_manifest replaced by brand (BrandReference with domain).
        """
        payload = {
            "brand": {"domain": "purinacatfood.com"},
            "brief": "video advertising campaigns",
            "filters": {"delivery_type": "guaranteed", "format_types": ["video"]},
        }

        req = GetProductsRequest(**payload)

        assert req.brand is not None
        assert req.brief == "video advertising campaigns"
        assert req.filters.delivery_type.value == "guaranteed"
        assert [ft.value for ft in req.filters.format_types] == ["video"]

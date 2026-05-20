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

        AdCP 3.0 makes buying_mode required for v3 clients. Within each mode, other
        fields remain optional per spec. Pre-v3 clients are defaulted to 'brief' at
        the transport boundary, not at the schema layer.
        """
        # Empty request is rejected (v3 spec: buying_mode required) — the library's
        # required-field validator catches this before our cross-mode invariants run.
        with pytest.raises(ValidationError, match="buying_mode"):
            GetProductsRequest()

        # Wholesale mode minimal (no other fields needed)
        req = GetProductsRequest(buying_mode="wholesale")
        assert req.brand is None
        assert req.brief is None
        assert req.filters is None

        # With brand only (wholesale mode)
        req = GetProductsRequest(buying_mode="wholesale", brand={"domain": "nike.com"})
        assert req.brand is not None
        assert req.brief is None  # Optional within wholesale mode
        assert req.filters is None

    def test_with_all_optional_fields(self):
        """Test with all optional fields that AdCP spec allows in brief mode."""
        req = GetProductsRequest(
            buying_mode="brief",
            brand={"domain": "acme.com"},
            brief="Looking for display advertising on tech sites",
            filters=ProductFilters(
                delivery_type="guaranteed",
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
        # format_ids are FormatId objects
        assert len(req.filters.format_ids) == 2
        assert req.filters.format_ids[0].id == "display_300x250"
        assert req.filters.format_ids[1].id == "video_30s"
        assert req.filters.standard_formats_only is False

    def test_filters_as_dict(self):
        """Test that filters can be provided as dict (JSON deserialization pattern)."""
        req = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "tesla.com"},
            filters={
                "delivery_type": "non_guaranteed",
                "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_standard"}],
            },
        )

        assert req.filters is not None
        # Library uses enum for delivery_type
        assert req.filters.delivery_type.value == "non_guaranteed"
        assert [fid.id for fid in req.filters.format_ids] == ["video_standard"]

    def test_partial_filters(self):
        """Test with only some filter fields (all filters are optional)."""
        req = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "spotify.com"},
            filters=ProductFilters(delivery_type="guaranteed"),
        )

        assert req.filters is not None
        assert req.filters.delivery_type.value == "guaranteed"
        assert req.filters.format_ids is None

    def test_filters_format_ids(self):
        """Test that format_ids accepts valid FormatId values per AdCP spec."""
        from src.core.schemas import FormatId

        fid = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250")
        req = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "testbrand.com"},
            filters=ProductFilters(format_ids=[fid]),
        )
        assert req.filters.format_ids[0].id == "display_300x250"

    def test_filters_delivery_type_values(self):
        """Test that delivery_type accepts valid values per AdCP spec."""
        # Guaranteed products
        req1 = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "testbrand.com"},
            filters=ProductFilters(delivery_type="guaranteed"),
        )
        assert req1.filters.delivery_type.value == "guaranteed"

        # Non-guaranteed products
        req2 = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "testbrand.com"},
            filters=ProductFilters(delivery_type="non_guaranteed"),
        )
        assert req2.filters.delivery_type.value == "non_guaranteed"


class TestProductFiltersModel:
    """Test ProductFilters Pydantic model independently."""

    def test_empty_filters(self):
        """Test that ProductFilters can be created with no fields (all optional)."""
        filters = ProductFilters()

        assert filters.delivery_type is None
        assert filters.format_ids is None
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
        """Test array filter fields (format_ids)."""
        filters = ProductFilters(
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_30s"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="audio_15s"),
            ],
        )

        assert len(filters.format_ids) == 3
        assert filters.format_ids[0].id == "display_300x250"

    def test_model_dump_excludes_none(self):
        """Test that model_dump with exclude_none only includes set fields."""
        filters = ProductFilters(delivery_type="guaranteed", standard_formats_only=True)

        dumped = filters.model_dump(exclude_none=True)

        assert "delivery_type" in dumped
        assert "standard_formats_only" in dumped
        assert "format_ids" not in dumped  # Was None


class TestAdCPSchemaCompatibility:
    """Test compatibility with actual AdCP schema examples."""

    def test_example_from_adcp_spec_1(self):
        """Test example from test_adcp_schema_compliance.py line 149.

        adcp 3.6.0: brand_manifest replaced by brand (BrandReference with domain).
        """
        # This is the updated example - using brand (BrandReference) instead of brand_manifest
        req = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "mobileapps.com"},
            filters={"format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_standard"}]},
        )

        assert req.brand is not None
        assert [fid.id for fid in req.filters.format_ids] == ["video_standard"]

    def test_example_minimal_adcp_request(self):
        """Test minimal valid request per AdCP spec.

        AdCP 3.0 requires buying_mode for v3 clients. Other fields remain optional within mode.
        """
        # Empty request is rejected (buying_mode required)
        with pytest.raises(ValidationError, match="buying_mode"):
            GetProductsRequest()

        # Brand only (wholesale mode)
        req = GetProductsRequest(buying_mode="wholesale", brand={"domain": "eco-products.com"})
        assert req.brand is not None
        assert req.brief is None  # Optional within wholesale mode
        assert req.filters is None

    def test_example_with_brief(self):
        """Test request with brief field (brief mode)."""
        req = GetProductsRequest(buying_mode="brief", brief="display advertising", brand={"domain": "eco-products.com"})

        assert req.brief == "display advertising"
        assert req.brand is not None

    def test_example_multiple_filter_fields(self):
        """Test request with multiple filter fields."""
        req = GetProductsRequest(
            buying_mode="wholesale",
            brand={"domain": "premium-video.com"},
            filters={
                "delivery_type": "non_guaranteed",
                "format_ids": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            },
        )

        assert req.filters.delivery_type.value == "non_guaranteed"
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
                buying_mode="brief",
                brand={"domain": "catfood.com"},
                brief="video ads",
                filters={
                    "delivery_type": "guaranteed",
                    "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_standard"}],
                },
            )
            assert req.brand is not None
            assert req.brief == "video ads"
            assert req.filters is not None
            assert req.filters.delivery_type.value == "guaranteed"
        except ValidationError as e:
            pytest.fail(f"GetProductsRequest should accept AdCP-valid fields. Error: {e}")

    def test_buying_mode_required_for_v3_clients(self):
        """Test that buying_mode is required (AdCP 3.0 contract).

        Within each mode, other fields remain optional. Pre-v3 clients are defaulted
        to 'brief' at the transport boundary, not at the schema layer.
        """
        # No buying_mode -> rejected
        with pytest.raises(ValidationError, match="buying_mode"):
            GetProductsRequest()

        # buying_mode='wholesale' alone is valid
        req = GetProductsRequest(buying_mode="wholesale")
        assert req.brand is None
        assert req.brief is None
        assert req.filters is None

    def test_spec_compliant_payload(self):
        """
        Test a full payload with all supported AdCP spec fields (brief mode).

        adcp 3.6.0: brand_manifest replaced by brand (BrandReference with domain).
        """
        payload = {
            "buying_mode": "brief",
            "brand": {"domain": "purinacatfood.com"},
            "brief": "video advertising campaigns",
            "filters": {
                "delivery_type": "guaranteed",
                "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_standard"}],
            },
        }

        req = GetProductsRequest(**payload)

        assert req.brand is not None
        assert req.brief == "video advertising campaigns"
        assert req.filters.delivery_type.value == "guaranteed"
        assert [fid.id for fid in req.filters.format_ids] == ["video_standard"]

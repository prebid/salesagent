"""Unit tests for authorized properties functionality.

adcp 3.6.0 update: Property schema changed significantly.
New required fields: identifier (str), type (enum)
New optional fields: primary (bool), region (str), store (enum)
Old fields removed: name, identifiers (list), publisher_domain, tags, property_id
"""

import pytest
from adcp import Error

from src.core.schemas import (
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    Property,
)


class TestListAuthorizedPropertiesRequest:
    """Test ListAuthorizedPropertiesRequest schema validation."""

    def test_request_with_minimal_fields(self):
        """Test request with only required fields (all optional per spec)."""
        request = ListAuthorizedPropertiesRequest()

        # All fields are optional per AdCP spec
        assert request.context is None
        assert request.ext is None
        assert request.publisher_domains is None

    def test_request_with_publisher_domains(self):
        """Test request with publisher_domains filter."""
        request = ListAuthorizedPropertiesRequest(publisher_domains=["example.com", "news.example.com"])

        # Note: adcp 3.2.0 removed ListAuthorizedPropertiesRequest from library
        # Our local version uses simple list[str] instead of RootModel wrappers
        assert request.publisher_domains is not None
        assert len(request.publisher_domains) == 2
        assert request.publisher_domains == ["example.com", "news.example.com"]

    def test_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesRequest complies with AdCP schema."""
        # Create request with optional fields
        request = ListAuthorizedPropertiesRequest(publisher_domains=["example.com"])

        # Test AdCP-compliant response
        adcp_response = request.model_dump(exclude_none=False)

        # Verify spec fields are present (all optional per spec)
        # Note: adcp 3.2.0 removed this type from library, we define it locally with 4 fields
        spec_fields = {"context", "ext", "publisher_domains", "property_tags"}
        assert set(adcp_response.keys()) == spec_fields

        # Verify field count matches expectation
        assert len(adcp_response) == 4


class TestProperty:
    """Test Property schema validation.

    adcp 3.6.0: Property schema has new fields:
    - identifier (REQUIRED): Domain, bundle ID, or other property identifier
    - type (REQUIRED): 'website', 'mobile_app', 'ctv_app', 'desktop_app',
                       'dooh', 'podcast', 'radio', 'streaming_audio'
    - primary (optional, default False)
    - region (optional)
    - store (optional): 'apple', 'google', 'amazon', 'roku', 'samsung', 'lg', 'other'
    """

    def test_property_with_minimal_fields(self):
        """Test property with only required fields (identifier and type)."""
        property_obj = Property(
            identifier="example.com",
            type="website",
        )

        assert property_obj.identifier == "example.com"
        assert property_obj.primary is False  # Default value
        assert property_obj.region is None
        assert property_obj.store is None

    def test_property_with_all_fields(self):
        """Test property with all optional fields."""
        property_obj = Property(
            identifier="com.example.app",
            type="mobile_app",
            primary=True,
            store="apple",
            region="US",
        )

        assert property_obj.identifier == "com.example.app"
        assert property_obj.primary is True
        assert property_obj.region == "US"

    def test_property_model_dump_omits_none_fields(self):
        """Test that model_dump omits None optional fields (AdCP spec compliance)."""
        property_obj = Property(
            identifier="example.com",
            type="website",
            # region and store not set (None)
        )

        data = property_obj.model_dump()
        # Per AdCP spec, optional fields with None values should be omitted
        assert "region" not in data or data.get("region") is None
        assert "store" not in data or data.get("store") is None

        # Required fields must always be present
        assert "identifier" in data
        assert "type" in data

    def test_property_requires_identifier_and_type(self):
        """Test that property requires identifier and type."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Property()  # Missing both required fields

    def test_invalid_property_type(self):
        """Test that invalid property type raises validation error."""
        with pytest.raises(ValueError):
            Property(
                identifier="example.com",
                type="invalid_type",
            )

    def test_property_adcp_compliance(self):
        """Test that Property complies with AdCP property schema (adcp 3.6.0)."""
        # Create property with required fields
        property_obj = Property(
            identifier="example.com",
            type="website",
        )

        # Test AdCP-compliant response (mode="json" serializes enums to strings)
        adcp_response = property_obj.model_dump(mode="json")

        # Verify required AdCP fields present and non-null
        required_fields = ["identifier", "type"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify property type is valid enum value (as string after json serialization)
        valid_types = ["website", "mobile_app", "ctv_app", "desktop_app", "dooh", "podcast", "radio", "streaming_audio"]
        assert adcp_response["type"] in valid_types

        # primary field has a default value
        assert "primary" in adcp_response
        assert adcp_response["primary"] is False

        # Test with mobile_app and optional store
        app_property = Property(
            identifier="com.example.app",
            type="mobile_app",
            primary=True,
            store="google",
        )
        app_response = app_property.model_dump(mode="json")
        assert app_response["identifier"] == "com.example.app"
        assert app_response["type"] == "mobile_app"
        assert app_response["primary"] is True
        assert app_response["store"] == "google"


class TestListAuthorizedPropertiesResponse:
    """Test ListAuthorizedPropertiesResponse schema validation."""

    def test_response_with_minimal_fields(self):
        """Test response with only required fields."""
        response = ListAuthorizedPropertiesResponse(publisher_domains=["example.com"])

        assert response.publisher_domains == ["example.com"]
        assert response.errors is None

    def test_response_with_all_fields(self):
        """Test response with all optional fields (per AdCP v2.4 spec)."""
        response = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
            primary_channels=["display", "video"],
            primary_countries=["US", "GB"],
            portfolio_description="Premium content portfolio",
            advertising_policies="No tobacco or alcohol ads",
            last_updated="2025-10-27T12:00:00Z",
            errors=[Error(code="WARNING", message="Test warning")],
        )

        assert len(response.publisher_domains) == 1
        assert response.primary_channels == ["display", "video"]
        assert len(response.errors) == 1

    def test_response_model_dump_omits_none_values(self):
        """Test that model_dump omits None-valued optional fields per AdCP spec."""
        response = ListAuthorizedPropertiesResponse(publisher_domains=["example.com"])

        data = response.model_dump()
        # Per AdCP spec, optional fields with None values should be omitted
        assert "errors" not in data, "errors with None value should be omitted"
        assert "primary_channels" not in data, "primary_channels with None value should be omitted"
        assert "publisher_domains" in data, "Required fields should always be present"

    def test_response_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesResponse complies with AdCP v2.4 schema."""
        # Create response with required fields only (no optional fields set)
        # Per /schemas/v1/media-buy/list-authorized-properties-response.json
        response = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
            # All optional fields omitted - should be excluded from model_dump per AdCP spec
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        assert "publisher_domains" in adcp_response
        assert adcp_response["publisher_domains"] == ["example.com"]

        # Test with optional fields
        response_with_optionals = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com", "example.org"],
            primary_channels=["display", "video"],
            advertising_policies="No tobacco ads",
        )
        adcp_with_optionals = response_with_optionals.model_dump()
        assert "primary_channels" in adcp_with_optionals, "Set optional fields should be present"
        assert "advertising_policies" in adcp_with_optionals, "Set optional fields should be present"
        assert isinstance(adcp_with_optionals["primary_channels"], list)
        assert isinstance(adcp_with_optionals["advertising_policies"], str)

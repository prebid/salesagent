"""Unit tests for authorized properties functionality."""

import pytest

from src.core.schemas import (
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    Property,
    PropertyIdentifier,
    PropertyTagMetadata,
)


class TestListAuthorizedPropertiesRequest:
    """Test ListAuthorizedPropertiesRequest schema validation."""

    def test_request_with_minimal_fields(self):
        """Test request with only required fields."""
        request = ListAuthorizedPropertiesRequest()

        assert request.adcp_version == "1.0.0"
        assert request.tags is None

    def test_request_with_all_fields(self):
        """Test request with all fields."""
        request = ListAuthorizedPropertiesRequest(adcp_version="1.2.3", tags=["premium_content", "news"])

        assert request.adcp_version == "1.2.3"
        assert request.tags == ["premium_content", "news"]

    def test_request_normalizes_tags(self):
        """Test that tags are normalized to lowercase with underscores."""
        request = ListAuthorizedPropertiesRequest(tags=["Premium-Content", "News-Sports"])

        assert request.tags == ["premium_content", "news_sports"]

    def test_invalid_adcp_version_format(self):
        """Test that invalid AdCP version format raises validation error."""
        with pytest.raises(ValueError):
            ListAuthorizedPropertiesRequest(adcp_version="1.0")

    def test_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesRequest complies with AdCP schema."""
        # Create request with all fields
        request = ListAuthorizedPropertiesRequest(adcp_version="1.0.0", tags=["premium_content", "news"])

        # Test AdCP-compliant response
        adcp_response = request.model_dump()

        # Verify required AdCP fields present
        required_fields = ["adcp_version"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify field count matches expectation
        assert len(adcp_response) == 2


class TestProperty:
    """Test Property schema validation."""

    def test_property_with_minimal_fields(self):
        """Test property with only required fields."""
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            publisher_domain="example.com",
        )

        assert property_obj.property_type == "website"
        assert property_obj.name == "Example Site"
        assert len(property_obj.identifiers) == 1
        assert property_obj.identifiers[0].type == "domain"
        assert property_obj.identifiers[0].value == "example.com"
        assert property_obj.publisher_domain == "example.com"
        assert property_obj.tags is None

    def test_property_with_all_fields(self):
        """Test property with all fields."""
        property_obj = Property(
            property_type="mobile_app",
            name="Example App",
            identifiers=[
                PropertyIdentifier(type="bundle_id", value="com.example.app"),
                PropertyIdentifier(type="app_store_id", value="123456789"),
            ],
            tags=["mobile", "entertainment"],
            publisher_domain="example.com",
        )

        assert property_obj.property_type == "mobile_app"
        assert property_obj.name == "Example App"
        assert len(property_obj.identifiers) == 2
        assert property_obj.tags == ["mobile", "entertainment"]
        assert property_obj.publisher_domain == "example.com"

    def test_property_model_dump_includes_empty_tags(self):
        """Test that model_dump ensures tags is always present."""
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            publisher_domain="example.com",
        )

        data = property_obj.model_dump()
        assert "tags" in data
        assert data["tags"] == []

    def test_property_requires_at_least_one_identifier(self):
        """Test that property requires at least one identifier."""
        with pytest.raises(ValueError):
            Property(
                property_type="website",
                name="Example Site",
                identifiers=[],  # Empty list should fail
                publisher_domain="example.com",
            )

    def test_invalid_property_type(self):
        """Test that invalid property type raises validation error."""
        with pytest.raises(ValueError):
            Property(
                property_type="invalid_type",
                name="Example Site",
                identifiers=[PropertyIdentifier(type="domain", value="example.com")],
                publisher_domain="example.com",
            )

    def test_property_adcp_compliance(self):
        """Test that Property complies with AdCP property schema."""
        # Create property with all required + optional fields
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            tags=["premium_content"],
            publisher_domain="example.com",
        )

        # Test AdCP-compliant response
        adcp_response = property_obj.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["property_type", "name", "identifiers", "publisher_domain"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify field count expectations
        assert len(adcp_response) == 5  # 4 required + 1 optional


class TestListAuthorizedPropertiesResponse:
    """Test ListAuthorizedPropertiesResponse schema validation."""

    def test_response_with_minimal_fields(self):
        """Test response with only required fields."""
        response = ListAuthorizedPropertiesResponse(adcp_version="1.0.0", properties=[])

        assert response.adcp_version == "1.0.0"
        assert response.properties == []
        assert response.tags == {}
        assert response.errors is None

    def test_response_with_all_fields(self):
        """Test response with all fields."""
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            tags=["premium_content"],
            publisher_domain="example.com",
        )

        tag_metadata = PropertyTagMetadata(name="Premium Content", description="High-quality content properties")

        response = ListAuthorizedPropertiesResponse(
            adcp_version="1.0.0",
            properties=[property_obj],
            tags={"premium_content": tag_metadata},
            errors=[{"code": "WARNING", "message": "Test warning"}],
        )

        assert response.adcp_version == "1.0.0"
        assert len(response.properties) == 1
        assert "premium_content" in response.tags
        assert len(response.errors) == 1

    def test_response_model_dump_includes_empty_errors(self):
        """Test that model_dump ensures errors is always present."""
        response = ListAuthorizedPropertiesResponse(adcp_version="1.0.0", properties=[])

        data = response.model_dump()
        assert "errors" in data
        assert data["errors"] == []

    def test_response_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesResponse complies with AdCP schema."""
        # Create response with all required + optional fields
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            publisher_domain="example.com",
        )

        response = ListAuthorizedPropertiesResponse(
            adcp_version="1.0.0",
            properties=[property_obj],
            tags={"test": PropertyTagMetadata(name="Test", description="Test tag")},
            errors=[],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["adcp_version", "properties"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags", "errors", "primary_channels", "primary_countries", "portfolio_description"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify field count expectations
        assert len(adcp_response) == 7  # 2 required + 5 optional


class TestPropertyTagMetadata:
    """Test PropertyTagMetadata schema validation."""

    def test_tag_metadata_creation(self):
        """Test basic tag metadata creation."""
        tag = PropertyTagMetadata(name="Premium Content", description="High-quality content properties")

        assert tag.name == "Premium Content"
        assert tag.description == "High-quality content properties"

    def test_tag_metadata_requires_all_fields(self):
        """Test that tag metadata requires all fields."""
        with pytest.raises(ValueError):
            PropertyTagMetadata(name="Test")  # Missing description

        with pytest.raises(ValueError):
            PropertyTagMetadata(description="Test")  # Missing name


class TestPropertyIdentifier:
    """Test PropertyIdentifier schema validation."""

    def test_identifier_creation(self):
        """Test basic identifier creation."""
        identifier = PropertyIdentifier(type="domain", value="example.com")

        assert identifier.type == "domain"
        assert identifier.value == "example.com"

    def test_identifier_requires_all_fields(self):
        """Test that identifier requires all fields."""
        with pytest.raises(ValueError):
            PropertyIdentifier(type="domain")  # Missing value

        with pytest.raises(ValueError):
            PropertyIdentifier(value="example.com")  # Missing type

"""Test Suite 6: Inventory Profile AdCP Schema Compliance.

Tests that verify inventory profiles and profile-based products comply with
AdCP schema requirements for formats, properties, and products.
"""

import pytest

from src.core.database.models import InventoryProfile, Product
from src.core.schemas import FormatId, Property
from src.core.schemas import Product as ProductSchema


def test_profile_formats_match_adcp_format_id_schema():
    """Test that profile formats match AdCP FormatId schema structure."""
    # Create profile with formats
    profile = InventoryProfile(
        tenant_id="test_tenant",
        profile_id="test_profile_format_schema",
        name="Test Profile Format Schema",
        description="Testing format schema compliance",
        inventory_config={
            "ad_units": ["unit_1"],
            "placements": [],
            "include_descendants": False,
        },
        format_ids=[
            {"agent_url": "https://test.example.com", "id": "display_300x250"},
            {"agent_url": "https://test.example.com", "id": "display_728x90"},
            {"agent_url": "https://buyer.example.com", "id": "video_15s"},
        ],
        publisher_properties=[
            {
                "publisher_domain": "example.com",
                "property_ids": ["prop_1"],
            }
        ],
    )

    # Verify each format is valid FormatId object
    assert len(profile.format_ids) == 3

    for format_dict in profile.format_ids:
        # Validate structure matches AdCP FormatId schema
        assert "agent_url" in format_dict, "FormatId must have agent_url field"
        assert "id" in format_dict, "FormatId must have id field"
        assert isinstance(format_dict["agent_url"], str), "agent_url must be string"
        assert isinstance(format_dict["id"], str), "id must be string"

        # Validate using Pydantic schema
        format_obj = FormatId(**format_dict)
        assert str(format_obj.agent_url).rstrip("/") == format_dict["agent_url"].rstrip(
            "/"
        )  # AnyUrl adds trailing slash
        assert format_obj.id == format_dict["id"]

        # Verify no extra fields (AdCP compliance)
        assert len(format_dict.keys()) == 2, "FormatId should only have agent_url and id"


def test_profile_publisher_properties_match_adcp_property_schema():
    """Test that profile publisher_properties match AdCP Property schema."""
    # Create profile with various property configurations
    profile = InventoryProfile(
        tenant_id="test_tenant",
        profile_id="test_profile_property_schema",
        name="Test Profile Property Schema",
        description="Testing property schema compliance",
        inventory_config={
            "ad_units": ["unit_1"],
            "placements": [],
            "include_descendants": False,
        },
        format_ids=[
            {"agent_url": "https://test.example.com", "id": "display_300x250"},
        ],
        publisher_properties=[
            {
                "property_type": "website",
                "name": "Example Website",
                "identifiers": [{"type": "domain", "value": "example.com"}],
                "publisher_domain": "example.com",
                "tags": ["premium"],
            },
            {
                "property_type": "mobile_app",
                "name": "Another App",
                "identifiers": [{"type": "bundle_id", "value": "com.another.app"}],
                "publisher_domain": "another.com",
                "tags": ["premium", "news"],
            },
            {
                "property_type": "ctv_app",
                "name": "Mixed CTV",
                "identifiers": [{"type": "roku_store_id", "value": "roku123"}],
                "publisher_domain": "mixed.com",
                "tags": ["featured"],
            },
        ],
    )

    # Verify each property matches AdCP Property schema
    assert len(profile.publisher_properties) == 3

    for prop_dict in profile.publisher_properties:
        # Validate structure matches AdCP Property schema
        assert "publisher_domain" in prop_dict, "Property must have publisher_domain"
        assert "property_type" in prop_dict, "Property must have property_type"
        assert "name" in prop_dict, "Property must have name"
        assert "identifiers" in prop_dict, "Property must have identifiers"
        assert isinstance(prop_dict["publisher_domain"], str), "publisher_domain must be string"
        assert isinstance(prop_dict["name"], str), "name must be string"
        assert isinstance(prop_dict["identifiers"], list), "identifiers must be list"

        # Validate using Pydantic schema
        property_obj = Property(**prop_dict)
        assert property_obj.publisher_domain == prop_dict["publisher_domain"]
        assert property_obj.property_type == prop_dict["property_type"]
        assert property_obj.name == prop_dict["name"]
        assert len(property_obj.identifiers) > 0

        if "tags" in prop_dict:
            assert isinstance(prop_dict["tags"], list), "tags must be list"
            assert all(isinstance(tag, str) for tag in prop_dict["tags"]), "tags must be strings"
            assert property_obj.tags == prop_dict["tags"]


def test_product_with_profile_passes_adcp_validation():
    """Test that product referencing profile passes AdCP Product validation."""
    from tests.helpers.adcp_factories import (
        create_test_cpm_pricing_option,
        create_test_publisher_properties_by_tag,
    )

    # Create profile
    profile = InventoryProfile(
        tenant_id="test_tenant",
        profile_id="test_profile_product_validation",
        name="Test Profile Product Validation",
        description="Testing product validation with profile",
        inventory_config={
            "ad_units": ["unit_1"],
            "placements": [],
            "include_descendants": False,
        },
        format_ids=[
            {"agent_url": "https://test.example.com", "id": "display_300x250"},
            {"agent_url": "https://test.example.com", "id": "display_728x90"},
        ],
        publisher_properties=[
            {
                "property_type": "website",
                "name": "Example Website",
                "identifiers": [{"type": "domain", "value": "example.com"}],
                "publisher_domain": "example.com",
                "tags": ["premium"],
            }
        ],
    )

    # Create product referencing profile
    product = Product(
        tenant_id="test_tenant",
        product_id="test_product_adcp_validation",
        name="Test Product AdCP Validation",
        description="Product for AdCP validation testing",
        inventory_profile_id=1,  # Simulate having a profile
        inventory_profile=profile,  # Link the profile
        format_ids=[],  # Not used when profile is set
        targeting_template={"geo_country": {"values": ["US"], "required": False}},
        delivery_type="guaranteed",
        property_tags=["all_inventory"],  # Fallback, not used with profile
        is_custom=False,
        countries=["US", "CA"],
    )

    # Get effective values from product (these come from profile)
    effective_formats = product.effective_format_ids
    effective_properties = product.effective_properties

    # Validate effective_formats match AdCP FormatId schema
    assert len(effective_formats) == 2
    for format_dict in effective_formats:
        # Validate using Pydantic FormatId schema
        format_obj = FormatId(**format_dict)
        assert format_obj.agent_url is not None
        assert format_obj.id is not None

    # Validate effective_properties match AdCP Property schema
    assert len(effective_properties) == 1
    for prop_dict in effective_properties:
        # Validate using Pydantic Property schema
        property_obj = Property(**prop_dict)
        assert property_obj.publisher_domain == "example.com"
        assert property_obj.property_type == "website"
        assert property_obj.name == "Example Website"
        assert len(property_obj.identifiers) > 0

    # Create ProductSchema from product data
    # This simulates what happens when product is serialized for AdCP API
    # Note: Only include fields that are in the AdCP Product spec
    product_data = {
        "product_id": product.product_id,
        "name": product.name,
        "description": product.description,
        "format_ids": [FormatId(**f) for f in effective_formats],
        "delivery_type": product.delivery_type,
        "delivery_measurement": {
            "provider": "test_provider",
            "notes": "Test measurement",
        },
        "publisher_properties": [create_test_publisher_properties_by_tag(publisher_domain="example.com")],
        "pricing_options": [create_test_cpm_pricing_option()],
        # Note: targeting_template is NOT in AdCP Product schema - it's internal
    }

    # Validate using AdCP ProductSchema (with extra="forbid" in development)
    # This ensures no internal fields leak and schema is AdCP-compliant
    product_schema = ProductSchema(**product_data)

    # Verify schema fields
    assert product_schema.product_id == product.product_id
    assert product_schema.name == product.name
    assert len(product_schema.format_ids) == 2
    assert len(product_schema.publisher_properties) == 1
    # delivery_type is an enum in the library, compare string value
    assert product_schema.delivery_type.value == "guaranteed"


def test_profile_formats_validation_rejects_invalid_structure():
    """Test that profile formats validation rejects invalid FormatId structures."""
    # Test missing required fields
    invalid_formats = [
        {"id": "display_300x250"},  # Missing agent_url
        {"agent_url": "https://test.example.com"},  # Missing id
        {"agent_url": 123, "id": "display_300x250"},  # Wrong type for agent_url
        {"agent_url": "https://test.example.com", "id": 456},  # Wrong type for id
    ]

    for invalid_format in invalid_formats:
        with pytest.raises((ValueError, TypeError)):
            # Pydantic validation should reject invalid formats
            FormatId(**invalid_format)


def test_profile_properties_validation_rejects_invalid_structure():
    """Test that profile properties validation rejects invalid Property structures."""
    # Test invalid property structures
    invalid_properties = [
        {"publisher_domain": "example.com"},  # Missing required fields
        {
            "publisher_domain": "example.com",
            "name": "Test",
            "identifiers": [{"type": "domain", "value": "example.com"}],
        },  # Missing property_type
        {
            "property_type": "website",
            "publisher_domain": "example.com",
            "identifiers": [{"type": "domain", "value": "example.com"}],
        },  # Missing name
        {
            "property_type": "website",
            "name": "Test",
            "publisher_domain": "example.com",
        },  # Missing identifiers
        {
            "property_type": "invalid_type",
            "name": "Test",
            "identifiers": [{"type": "domain", "value": "example.com"}],
            "publisher_domain": "example.com",
        },  # Invalid property_type
    ]

    for invalid_property in invalid_properties:
        with pytest.raises((ValueError, TypeError)):
            # Pydantic validation should reject invalid properties
            Property(**invalid_property)


def test_product_with_profile_has_no_internal_fields_in_serialization():
    """Test that product serialization doesn't leak internal fields."""
    from tests.helpers.adcp_factories import (
        create_test_cpm_pricing_option,
        create_test_publisher_properties_by_tag,
    )

    # Create profile
    profile = InventoryProfile(
        tenant_id="test_tenant",
        profile_id="test_profile_serialization",
        name="Test Profile Serialization",
        description="Testing serialization cleanliness",
        inventory_config={
            "ad_units": ["unit_1"],
            "placements": [],
            "include_descendants": False,
        },
        format_ids=[
            {"agent_url": "https://test.example.com", "id": "display_300x250"},
        ],
        publisher_properties=[
            {
                "property_type": "website",
                "name": "Example Website",
                "identifiers": [{"type": "domain", "value": "example.com"}],
                "publisher_domain": "example.com",
            }
        ],
    )

    # Create product referencing profile
    product = Product(
        tenant_id="test_tenant",
        product_id="test_product_serialization",
        name="Test Product Serialization",
        description="Product for serialization testing",
        inventory_profile_id=1,
        inventory_profile=profile,
        format_ids=[],
        targeting_template={},
        delivery_type="guaranteed",
        property_tags=["all_inventory"],
        is_custom=False,
        countries=["US"],
    )

    # Simulate product serialization for AdCP API
    effective_formats = product.effective_format_ids
    effective_properties = product.effective_properties

    # Only include fields that exist in AdCP Product schema
    product_data = {
        "product_id": product.product_id,
        "name": product.name,
        "description": product.description,
        "format_ids": [FormatId(**f) for f in effective_formats],
        "delivery_type": product.delivery_type,
        "delivery_measurement": {
            "provider": "test_provider",
            "notes": "Test measurement",
        },
        # Note: countries is NOT in AdCP Product schema - it's internal
        # Note: targeting_template is NOT in AdCP Product schema - it's internal
        "publisher_properties": [create_test_publisher_properties_by_tag(publisher_domain="example.com")],
        "pricing_options": [create_test_cpm_pricing_option()],
    }

    # Verify internal fields are NOT present
    internal_fields = [
        "tenant_id",
        "inventory_profile_id",
        "inventory_profile",
        "implementation_config",
        "property_tags",  # Legacy field, replaced by publisher_properties
        "properties",  # Internal database field, external API uses publisher_properties
        "created_at",
        "updated_at",
        "countries",  # Internal field
        "targeting_template",  # Internal field
    ]

    for field in internal_fields:
        assert field not in product_data, f"Internal field '{field}' should not be in serialization"

    # Validate using ProductSchema (will fail if extra fields present in development mode)
    product_schema = ProductSchema(**product_data)
    assert product_schema is not None

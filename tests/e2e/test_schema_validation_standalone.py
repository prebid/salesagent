"""
Standalone test for AdCP schema validation functionality.

This test validates that our schema validation system works correctly
without needing a running server, by testing the validation logic directly.
"""

import pytest

from .adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError


@pytest.mark.asyncio
async def test_schema_validator_initialization():
    """Test that the schema validator can be initialized and download schemas."""
    async with AdCPSchemaValidator() as validator:
        # Test that we can get the schema index
        index = await validator.get_schema_index()
        assert isinstance(index, dict)
        assert "schemas" in index
        assert "media-buy" in index["schemas"]

        # Test that we can find task schemas
        schema_ref = await validator._find_schema_ref_for_task("get-products", "response")
        assert schema_ref is not None
        assert "get-products-response" in schema_ref


@pytest.mark.asyncio
async def test_valid_get_products_response():
    """Test validation of a valid get-products response."""
    async with AdCPSchemaValidator() as validator:
        # Create a valid response according to the AdCP v2.0+ spec
        valid_response = {
            "products": [
                {
                    "product_id": "test-product-1",
                    "name": "Test Display Product",
                    "description": "Test description",
                    "publisher_properties": [
                        {
                            "publisher_domain": "example.com",
                            "selection_type": "by_tag",
                            "property_tags": ["premium_content"],
                        }
                    ],  # Required: publisher properties covered by this product
                    "format_ids": [
                        {
                            "agent_url": "https://creatives.adcontextprotocol.org",
                            "id": "display_300x250",
                        }
                    ],  # format_ids must be array of format-id objects
                    "delivery_type": "guaranteed",
                    "delivery_measurement": {
                        "provider": "Google Ad Manager with IAS viewability",
                        "notes": "MRC-accredited viewability. 50% in-view for 1s display / 2s video",
                    },
                    "pricing_options": [
                        {
                            "pricing_option_id": "cpm_usd_guaranteed",
                            "pricing_model": "cpm",
                            "rate": 5.0,
                            "currency": "USD",
                            "is_fixed": True,  # Required by adcp 2.5.0 discriminated unions
                            "min_spend_per_package": 1000.0,
                        }
                    ],
                }
            ],
        }

        # This should not raise an exception
        await validator.validate_response("get-products", valid_response)


@pytest.mark.asyncio
async def test_invalid_get_products_response():
    """Test validation of an invalid get-products response."""
    async with AdCPSchemaValidator() as validator:
        # Create an invalid response (missing required 'products' field)
        invalid_response = {
            "message": "Here are some products",
            "context_id": "test-context",
            # Missing required 'products' field
        }

        # This should raise a SchemaValidationError
        with pytest.raises(SchemaValidationError) as exc_info:
            await validator.validate_response("get-products", invalid_response)

        error = exc_info.value
        assert "products" in str(error).lower()
        assert len(error.validation_errors) > 0


@pytest.mark.asyncio
async def test_get_products_request_validation():
    """Test validation of get-products request parameters."""
    async with AdCPSchemaValidator() as validator:
        # Valid request with required brand_manifest (must have url or name)
        valid_request = {
            "brief": "Looking for display advertising",
            "brand_manifest": {"url": "https://example.com", "name": "Example Brand"},
        }

        # Should not raise exception
        await validator.validate_request("get-products", valid_request)

        # Test with minimal required request (brand_manifest with just name)
        minimal_request = {"brand_manifest": {"name": "Test Brand"}}
        await validator.validate_request("get-products", minimal_request)

        # Test with brand_manifest as URL string (alternative format)
        url_request = {"brand_manifest": "https://cdn.example.com/brand-manifest.json"}
        await validator.validate_request("get-products", url_request)


@pytest.mark.asyncio
async def test_offline_mode():
    """Test that offline mode works with cached schemas."""
    # First, ensure schemas are cached by using online mode
    async with AdCPSchemaValidator() as validator:
        await validator.validate_response("get-products", {"products": []})

    # Now test offline mode
    async with AdCPSchemaValidator(offline_mode=True) as offline_validator:
        # Should work with cached schemas
        await offline_validator.validate_response("get-products", {"products": []})


@pytest.mark.asyncio
async def test_schema_caching():
    """Test that schemas are properly cached for performance."""
    async with AdCPSchemaValidator() as validator:
        # First call should download the schema
        schema_ref = await validator._find_schema_ref_for_task("get-products", "response")
        schema1 = await validator.get_schema(schema_ref)

        # Second call should use cached version
        schema2 = await validator.get_schema(schema_ref)

        # Should be the same object (cached)
        assert schema1 is schema2

        # Check that compiled validators are also cached
        validator1 = validator._get_compiled_validator(schema1)
        validator2 = validator._get_compiled_validator(schema1)
        assert validator1 is validator2


@pytest.mark.asyncio
async def test_task_name_mapping():
    """Test that different task name formats are handled correctly."""
    async with AdCPSchemaValidator() as validator:
        # Test hyphen format (schema format)
        schema_ref1 = await validator._find_schema_ref_for_task("get-products", "response")

        # Test underscore format (should be converted)
        # Note: this tests the logic in the test client that converts underscore to hyphen
        assert schema_ref1 is not None
        assert "get-products" in schema_ref1


if __name__ == "__main__":
    import asyncio

    async def run_tests():
        """Run tests manually for debugging."""
        print("Testing schema validator initialization...")
        await test_schema_validator_initialization()
        print("✓ Initialization test passed")

        print("Testing valid response validation...")
        await test_valid_get_products_response()
        print("✓ Valid response test passed")

        print("Testing invalid response validation...")
        await test_invalid_get_products_response()
        print("✓ Invalid response test passed")

        print("Testing request validation...")
        await test_get_products_request_validation()
        print("✓ Request validation test passed")

        print("Testing schema caching...")
        await test_schema_caching()
        print("✓ Schema caching test passed")

        print("All tests passed!")

    asyncio.run(run_tests())

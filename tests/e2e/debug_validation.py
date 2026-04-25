#!/usr/bin/env python3
"""
Debug script to understand validation issues.
"""

import asyncio
import json

from adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError


async def debug_validation():
    """Debug schema validation step by step."""

    async with AdCPSchemaValidator() as validator:
        print("🔍 Debugging AdCP Schema Validation")

        # Test 1: Simple minimal valid response
        print("\n1️⃣ Testing minimal valid response...")
        minimal_response = {"products": []}

        try:
            await validator.validate_response("get-products", minimal_response)
            print("✅ Minimal response validation PASSED")
        except SchemaValidationError as e:
            print("❌ Minimal response validation FAILED")
            for error in e.validation_errors:
                print(f"   - {error}")
        except Exception as e:
            print(f"💥 Unexpected error: {e}")

        # Test 2: Look at actual product schema to understand requirements
        print("\n2️⃣ Examining product schema requirements...")
        try:
            product_schema_ref = "/schemas/latest/core/product.json"
            product_schema = await validator.get_schema(product_schema_ref)

            required_fields = product_schema.get("required", [])
            print(f"📋 Product required fields: {required_fields}")

            properties = product_schema.get("properties", {})
            print("📝 Product properties:")
            for prop, details in properties.items():
                prop_type = details.get("type", "unknown")
                is_required = prop in required_fields
                marker = "⚡" if is_required else "  "
                print(f"   {marker} {prop}: {prop_type}")

        except Exception as e:
            print(f"💥 Error examining product schema: {e}")

        # Test 3: Create properly structured product based on schema
        print("\n3️⃣ Testing schema-compliant product...")
        try:
            # Look at product schema required fields
            product_schema = await validator.get_schema("/schemas/latest/core/product.json")
            required = product_schema.get("required", [])
            print(f"Product schema requires: {required}")

            # Create minimal compliant product
            minimal_product = {}
            for field in required:
                if field == "product_id":
                    minimal_product[field] = "test-product-1"
                elif field == "name":
                    minimal_product[field] = "Test Product"
                elif field == "description":
                    minimal_product[field] = "Test description"
                elif field == "formats":
                    minimal_product[field] = []
                else:
                    # Add other required fields as needed
                    pass

            print(f"📦 Minimal product: {json.dumps(minimal_product, indent=2)}")

            # Test the response with this minimal product
            test_response = {"products": [minimal_product]}

            await validator.validate_response("get-products", test_response)
            print("✅ Schema-compliant response validation PASSED")

        except SchemaValidationError as e:
            print("❌ Schema-compliant response validation FAILED")
            print(f"   Main error: {e}")
            for error in e.validation_errors:
                print(f"   - {error}")
        except Exception as e:
            print(f"💥 Unexpected error: {e}")

        # Test 4: Test request validation (should be simpler)
        print("\n4️⃣ Testing request validation...")
        try:
            empty_request = {}
            await validator.validate_request("get-products", empty_request)
            print("✅ Empty request validation PASSED")

            basic_request = {"brief": "display advertising"}
            await validator.validate_request("get-products", basic_request)
            print("✅ Basic request validation PASSED")

        except SchemaValidationError as e:
            print("❌ Request validation FAILED")
            print(f"   Main error: {e}")
            for error in e.validation_errors:
                print(f"   - {error}")
        except Exception as e:
            print(f"💥 Unexpected error: {e}")


if __name__ == "__main__":
    asyncio.run(debug_validation())

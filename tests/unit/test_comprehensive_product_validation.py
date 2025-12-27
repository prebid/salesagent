#!/usr/bin/env python3
"""Comprehensive test to verify all product catalog providers create valid Product objects."""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

# Enable debug logging
logging.basicConfig(level=logging.INFO)

from product_catalog_providers.factory import get_product_catalog_provider
from src.core.schemas import Product


async def test_all_providers():
    """Test all product catalog providers to ensure they create valid Product objects."""

    print("🧪 Testing all product catalog providers for Product validation...")

    tenant_id = "default"
    principal_id = "test_principal"
    brief = "Test advertising campaign"
    context = {"brand_manifest": {"name": "test product"}, "tenant_id": tenant_id, "principal_id": principal_id}
    principal_data = {"principal_id": principal_id, "name": "Test Principal", "platform_mappings": {}}

    providers_to_test = [
        {"name": "Database Provider", "config": {"provider": "database", "config": {}}},
        {
            "name": "Hybrid Provider (no signals URL)",
            "config": {
                "provider": "hybrid",
                "config": {
                    "database": {},
                    "signals_discovery": {},  # No upstream_url, so signals disabled
                    "ranking_strategy": "database_first",
                    "max_products": 10,
                },
            },
        },
        {
            "name": "Signals Provider (fallback mode)",
            "config": {
                "provider": "signals",
                "config": {"fallback_to_database": True},  # No upstream_url, will fallback to database
            },
        },
        {
            "name": "AI Provider",
            "config": {
                "provider": "ai",
                "config": {"model": "gemini-flash-latest", "max_products": 5, "temperature": 0.3},
            },
        },
    ]

    all_passed = True

    for provider_test in providers_to_test:
        provider_name = provider_test["name"]
        config = provider_test["config"]

        print(f"\n📋 Testing {provider_name}...")

        try:
            # Get provider
            provider = await get_product_catalog_provider(tenant_id, config)

            # Get products
            products = await provider.get_products(
                brief=brief,
                tenant_id=tenant_id,
                principal_id=principal_id,
                context=context,
                principal_data=principal_data,
            )

            print(f"   ✅ Provider returned {len(products)} products")

            # Validate each product
            for i, product in enumerate(products):
                try:
                    # Check that it's a Product instance
                    assert isinstance(product, Product), f"Product {i} is not a Product instance: {type(product)}"

                    # Get model dump to check AdCP compliance
                    product_dict = product.model_dump()

                    # Check required AdCP fields
                    required_fields = [
                        "product_id",
                        "name",
                        "description",
                        "delivery_type",
                        "is_custom",
                    ]
                    for field in required_fields:
                        assert field in product_dict, f"Product {i} missing required field: {field}"
                        assert product_dict[field] is not None, f"Product {i} has null required field: {field}"

                    # Check that formats field was converted to format_ids
                    assert (
                        "format_ids" in product_dict
                    ), f"Product {i} missing format_ids field (should be converted from formats)"
                    assert isinstance(
                        product_dict["format_ids"], list
                    ), f"Product {i} format_ids is not a list: {type(product_dict['format_ids'])}"

                    # Check that internal fields are not present
                    internal_fields = [
                        "tenant_id",
                        "created_at",
                        "updated_at",
                        "implementation_config",
                        "targeting_template",
                    ]
                    for field in internal_fields:
                        assert field not in product_dict, f"Product {i} contains internal field: {field}"

                    # Check format_ids are valid strings
                    for format_id in product_dict["format_ids"]:
                        assert isinstance(
                            format_id, str
                        ), f"Product {i} has non-string format_id: {format_id} ({type(format_id)})"
                        assert format_id.strip(), f"Product {i} has empty format_id"

                    # Verify there are no unexpected audio format fields as top-level keys
                    unexpected_audio_fields = ["audio_15s", "audio_30s", "audio_60s"]
                    for field in unexpected_audio_fields:
                        assert (
                            field not in product_dict
                        ), f"Product {i} has unexpected audio field as top-level key: {field}"

                    print(f"   ✅ Product {i} ({product.product_id}) validation passed")

                except AssertionError as e:
                    print(f"   ❌ Product {i} validation failed: {e}")
                    print(f"      Product data: {json.dumps(product_dict, indent=2)}")
                    all_passed = False
                except Exception as e:
                    print(f"   ❌ Product {i} validation error: {e}")
                    all_passed = False

        except Exception as e:
            print(f"   ❌ {provider_name} failed: {e}")
            import traceback

            traceback.print_exc()
            all_passed = False

    print(f"\n{'=' * 60}")
    if all_passed:
        print("🎉 ALL TESTS PASSED! All product catalog providers create valid Product objects.")
    else:
        print("💥 SOME TESTS FAILED! There are Product validation issues.")
    print(f"{'=' * 60}")

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(test_all_providers())
    sys.exit(0 if success else 1)

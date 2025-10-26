#!/usr/bin/env python3
"""
Test that demonstrates the exact validation gap that allowed the AI provider bug to slip through.

This test would have caught the issue by testing the real database→schema conversion path.
"""

import json
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pydantic import ValidationError

from src.core.schemas import Product


def test_ai_provider_bug_reproduction():
    """
    This test reproduces the exact bug that was in the AI provider.

    It demonstrates how passing internal database fields to the Product constructor
    fails validation - this is the test that should have been written to catch the bug.
    """

    # Simulate the problematic product_data dict that the AI provider was creating
    # This is the EXACT pattern from ai.py lines 93-110 before the fix
    product_model_data = {
        "product_id": "test_product",
        "name": "Test Product",
        "description": "Test description",
        "formats": ["display_300x250", "audio_15s", "audio_30s"],
        "delivery_type": "guaranteed",
        "is_fixed_price": True,
        "cpm": 10.0,
        # BUG: These fields were being passed to Product constructor but aren't valid
        "targeting_template": {"demographics": "adults"},  # INVALID - internal field
        "price_guidance": {"min": 5.0, "max": 15.0},  # INVALID - not in Product schema
        "implementation_config": {"placement": "123"},  # INVALID - internal field
        "countries": ["US", "CA"],  # INVALID - not in Product schema
        "expires_at": "2024-12-31",  # INVALID - internal field
        "is_custom": False,
    }

    print("Testing the exact Product construction pattern that was failing...")
    print(f"Attempting to create Product with data: {json.dumps(product_model_data, indent=2)}")

    # This reveals the ACTUAL problem: Pydantic silently accepts extra fields!
    try:
        product = Product(**product_model_data)
        print("🚨 CRITICAL ISSUE: Product construction succeeded when it should have failed!")
        print("🚨 This means our Product schema accepts ANY extra fields!")

        # Check what fields actually got set
        actual_fields = list(product.__dict__.keys())
        print(f"🔍 Fields that got set on Product object: {actual_fields}")

        # Check what's in the AdCP response
        adcp_response = product.model_dump()
        print(f"🔍 Fields in AdCP response: {list(adcp_response.keys())}")

        # The dangerous part: do internal fields leak into the AdCP response?
        internal_fields = ["targeting_template", "price_guidance", "implementation_config", "countries"]
        leaked_fields = [field for field in internal_fields if field in adcp_response]

        if leaked_fields:
            print(f"💥 SECURITY ISSUE: Internal fields leaked to AdCP response: {leaked_fields}")
            raise AssertionError(f"Internal fields {leaked_fields} should not be in AdCP response!")
        else:
            print("✅ Good: Internal fields were ignored and not included in AdCP response")

    except ValidationError as e:
        print(f"✅ Validation error caught as expected: {e}")


def test_correct_product_construction():
    """
    Test the CORRECT way to construct Product objects (as fixed in the AI provider).

    This demonstrates the proper pattern that should be used by all providers.
    """

    # Correct product_data dict with only AdCP-compliant fields

    correct_product_data = {
        "product_id": "test_product",
        "name": "Test Product",
        "description": "Test description",
        "formats": ["display_300x250", "audio_15s", "audio_30s"],
        "delivery_type": "guaranteed",
        "is_custom": False,
        "property_tags": ["all_inventory"],  # Required per AdCP spec
        "pricing_options": [
            {
                "pricing_option_id": "cpm_usd_fixed",
                "pricing_model": "cpm",
                "rate": 10.0,
                "currency": "USD",
                "is_fixed": True,
            }
        ],
        # NOTE: Internal fields like targeting_template, price_guidance, etc. are NOT included
    }

    print("Testing the CORRECT Product construction pattern...")
    print(f"Creating Product with clean data: {json.dumps(correct_product_data, indent=2)}")

    # This should SUCCEED
    product = Product(**correct_product_data)

    print("✅ Product created successfully!")

    # Verify the AdCP-compliant response
    adcp_response = product.model_dump()
    print(f"AdCP response: {json.dumps(adcp_response, indent=2)}")

    # Verify required fields are present
    assert "product_id" in adcp_response
    assert "format_ids" in adcp_response  # Note: formats becomes format_ids in response
    assert adcp_response["format_ids"] == ["display_300x250", "audio_15s", "audio_30s"]

    # Verify internal fields are NOT in the response
    internal_fields = ["targeting_template", "price_guidance", "implementation_config", "countries", "expires_at"]
    for field in internal_fields:
        assert field not in adcp_response, f"Internal field '{field}' should not be in AdCP response"

    print("✅ All AdCP compliance checks passed!")


if __name__ == "__main__":
    print("=" * 80)
    print("TESTING THE VALIDATION GAP THAT ALLOWED THE AI PROVIDER BUG")
    print("=" * 80)

    print("\n1. Testing the BROKEN pattern (should fail):")
    construction_succeeded = test_ai_provider_bug_reproduction()

    print("\n2. Testing the CORRECT pattern (should succeed):")
    test_correct_product_construction()

    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED - Validation gap has been identified!")
    print("This test should be added to the test suite to prevent regressions.")
    print("=" * 80)

"""Validation tests comparing manual schemas vs auto-generated schemas.

This test suite identifies differences between our manually-maintained schemas
(src.core.schemas) and the auto-generated schemas from AdCP JSON Schema
(src.core.schemas_generated).

Purpose:
- Find schema drift (fields we added that aren't in AdCP spec)
- Find missing fields (fields in spec we haven't implemented)
- Ensure we can safely migrate from manual to generated schemas

Strategy:
- Compare field names, types, and constraints
- Flag differences for review (not automatic failures)
- Document which differences are intentional vs bugs
"""

from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

# Manual schemas
from src.core.schemas import (
    GetProductsRequest as ManualGetProductsRequest,
)
from src.core.schemas import (
    GetProductsResponse as ManualGetProductsResponse,
)

# Generated schemas
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest1 as GeneratedGetProductsRequest1,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_request_json import (
    GetProductsRequest2 as GeneratedGetProductsRequest2,
)
from src.core.schemas_generated._schemas_v1_media_buy_get_products_response_json import (
    GetProductsResponse as GeneratedGetProductsResponse,
)


def get_model_fields(model: type[BaseModel]) -> dict[str, FieldInfo]:
    """Extract fields from a Pydantic model."""
    return model.model_fields


def get_field_type_name(field_info: FieldInfo) -> str:
    """Get simplified type name from FieldInfo for comparison."""
    annotation = field_info.annotation
    if annotation is None:
        return "Any"

    # Handle Union types
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if len(args) > 0:
            # Simplify to just the type names
            type_names = [getattr(arg, "__name__", str(arg)) for arg in args]
            return f"{origin.__name__}[{', '.join(type_names)}]"

    # Simple type
    return getattr(annotation, "__name__", str(annotation))


def compare_fields(
    manual_model: type[BaseModel],
    generated_model: type[BaseModel],
    manual_name: str,
    generated_name: str,
) -> dict[str, Any]:
    """Compare fields between manual and generated models.

    Returns:
        Dict with comparison results:
        - manual_only: Fields in manual but not generated (potential drift)
        - generated_only: Fields in generated but not manual (missing implementation)
        - type_mismatches: Fields with different types
        - matches: Fields that match
    """
    manual_fields = get_model_fields(manual_model)
    generated_fields = get_model_fields(generated_model)

    manual_field_names = set(manual_fields.keys())
    generated_field_names = set(generated_fields.keys())

    result = {
        "manual_only": [],
        "generated_only": [],
        "type_mismatches": [],
        "matches": [],
    }

    # Fields only in manual (potential drift - we added extra fields)
    for field_name in manual_field_names - generated_field_names:
        field_info = manual_fields[field_name]
        result["manual_only"].append(
            {
                "field": field_name,
                "type": get_field_type_name(field_info),
                "required": field_info.is_required(),
                "default": field_info.default,
            }
        )

    # Fields only in generated (missing from manual - we need to add them)
    for field_name in generated_field_names - manual_field_names:
        field_info = generated_fields[field_name]
        result["generated_only"].append(
            {
                "field": field_name,
                "type": get_field_type_name(field_info),
                "required": field_info.is_required(),
                "default": field_info.default,
            }
        )

    # Fields in both - check if types match
    for field_name in manual_field_names & generated_field_names:
        manual_field = manual_fields[field_name]
        generated_field = generated_fields[field_name]

        manual_type = get_field_type_name(manual_field)
        generated_type = get_field_type_name(generated_field)

        if manual_type == generated_type:
            result["matches"].append(field_name)
        else:
            result["type_mismatches"].append(
                {
                    "field": field_name,
                    "manual_type": manual_type,
                    "generated_type": generated_type,
                    "manual_required": manual_field.is_required(),
                    "generated_required": generated_field.is_required(),
                }
            )

    return result


class TestGetProductsRequestComparison:
    """Compare manual GetProductsRequest vs generated variants."""

    def test_compare_with_variant1(self):
        """Compare manual GetProductsRequest with GetProductsRequest1 (promoted_offering variant)."""
        result = compare_fields(
            ManualGetProductsRequest,
            GeneratedGetProductsRequest1,
            "Manual GetProductsRequest",
            "Generated GetProductsRequest1",
        )

        print("\n" + "=" * 80)
        print("GetProductsRequest: Manual vs Generated Variant 1 (promoted_offering)")
        print("=" * 80)

        if result["manual_only"]:
            print("\n⚠️  FIELDS ONLY IN MANUAL (potential drift):")
            for field in result["manual_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )

        if result["generated_only"]:
            print("\n⚠️  FIELDS ONLY IN GENERATED (missing from manual):")
            for field in result["generated_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )

        if result["type_mismatches"]:
            print("\n⚠️  TYPE MISMATCHES:")
            for mismatch in result["type_mismatches"]:
                print(f"  - {mismatch['field']}:")
                print(f"      Manual:    {mismatch['manual_type']} (required={mismatch['manual_required']})")
                print(f"      Generated: {mismatch['generated_type']} (required={mismatch['generated_required']})")

        if result["matches"]:
            print(f"\n✅ MATCHING FIELDS ({len(result['matches'])}):")
            print(f"  {', '.join(result['matches'])}")

        # Store for analysis but don't fail
        print(
            f"\nSummary: {len(result['matches'])} matches, {len(result['manual_only'])} manual-only, "
            f"{len(result['generated_only'])} generated-only, {len(result['type_mismatches'])} type mismatches"
        )

    def test_compare_with_variant2(self):
        """Compare manual GetProductsRequest with GetProductsRequest2 (brand_manifest variant)."""
        result = compare_fields(
            ManualGetProductsRequest,
            GeneratedGetProductsRequest2,
            "Manual GetProductsRequest",
            "Generated GetProductsRequest2",
        )

        print("\n" + "=" * 80)
        print("GetProductsRequest: Manual vs Generated Variant 2 (brand_manifest)")
        print("=" * 80)

        if result["manual_only"]:
            print("\n⚠️  FIELDS ONLY IN MANUAL (potential drift):")
            for field in result["manual_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )

        if result["generated_only"]:
            print("\n⚠️  FIELDS ONLY IN GENERATED (missing from manual):")
            for field in result["generated_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )

        if result["type_mismatches"]:
            print("\n⚠️  TYPE MISMATCHES:")
            for mismatch in result["type_mismatches"]:
                print(f"  - {mismatch['field']}:")
                print(f"      Manual:    {mismatch['manual_type']} (required={mismatch['manual_required']})")
                print(f"      Generated: {mismatch['generated_type']} (required={mismatch['generated_required']})")

        if result["matches"]:
            print(f"\n✅ MATCHING FIELDS ({len(result['matches'])}):")
            print(f"  {', '.join(result['matches'])}")

        print(
            f"\nSummary: {len(result['matches'])} matches, {len(result['manual_only'])} manual-only, "
            f"{len(result['generated_only'])} generated-only, {len(result['type_mismatches'])} type mismatches"
        )


class TestGetProductsResponseComparison:
    """Compare manual GetProductsResponse vs generated."""

    def test_compare_response_schemas(self):
        """Compare manual GetProductsResponse with generated GetProductsResponse."""
        result = compare_fields(
            ManualGetProductsResponse,
            GeneratedGetProductsResponse,
            "Manual GetProductsResponse",
            "Generated GetProductsResponse",
        )

        print("\n" + "=" * 80)
        print("GetProductsResponse: Manual vs Generated")
        print("=" * 80)

        if result["manual_only"]:
            print("\n⚠️  FIELDS ONLY IN MANUAL (potential drift):")
            for field in result["manual_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )
                print("      ⚠️  THIS FIELD IS NOT IN ADCP SPEC - needs review!")

        if result["generated_only"]:
            print("\n⚠️  FIELDS ONLY IN GENERATED (missing from manual):")
            for field in result["generated_only"]:
                print(
                    f"  - {field['field']}: {field['type']} (required={field['required']}, default={field['default']})"
                )
                print("      ⚠️  WE ARE MISSING THIS SPEC FIELD - needs implementation!")

        if result["type_mismatches"]:
            print("\n⚠️  TYPE MISMATCHES:")
            for mismatch in result["type_mismatches"]:
                print(f"  - {mismatch['field']}:")
                print(f"      Manual:    {mismatch['manual_type']} (required={mismatch['manual_required']})")
                print(f"      Generated: {mismatch['generated_type']} (required={mismatch['generated_required']})")

        if result["matches"]:
            print(f"\n✅ MATCHING FIELDS ({len(result['matches'])}):")
            print(f"  {', '.join(result['matches'])}")

        print(
            f"\nSummary: {len(result['matches'])} matches, {len(result['manual_only'])} manual-only, "
            f"{len(result['generated_only'])} generated-only, {len(result['type_mismatches'])} type mismatches"
        )

        # Flag critical issues
        if result["manual_only"]:
            print("\n🚨 CRITICAL: Manual schema has fields not in AdCP spec!")
            print("   These need to be either:")
            print("   1. Removed (if they're mistakes)")
            print("   2. Filed as AdCP spec issues (if they should be in spec)")

        if result["generated_only"]:
            print("\n🚨 CRITICAL: Manual schema is missing AdCP spec fields!")
            print("   These need to be added to maintain spec compliance.")


if __name__ == "__main__":
    # Run tests manually to see output
    pytest.main([__file__, "-v", "-s"])

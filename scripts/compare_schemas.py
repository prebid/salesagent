#!/usr/bin/env python3
"""
Compare manual Pydantic schemas with auto-generated schemas.

This script analyzes differences between:
- src/core/schemas.py (manual schemas)
- src/core/schemas_generated/ (auto-generated schemas)

Shows what breaks if we switch to generated schemas.
"""

import inspect
import sys
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import both schema modules
import src.core.schemas as manual_schemas


def get_classes_from_module(module) -> dict[str, type]:
    """Extract all classes from a module."""
    classes = {}
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and obj.__module__ == module.__name__:
            classes[name] = obj
    return classes


def get_fields_from_pydantic_model(model_class) -> dict[str, Any]:
    """Extract fields from a Pydantic model."""
    if not hasattr(model_class, "model_fields"):
        return {}

    fields = {}
    for field_name, field_info in model_class.model_fields.items():
        fields[field_name] = {
            "type": str(field_info.annotation),
            "required": field_info.is_required(),
            "default": field_info.default if field_info.default is not None else None,
        }
    return fields


def main():
    print("=" * 80)
    print("SCHEMA COMPARISON: Manual vs Auto-Generated")
    print("=" * 80)
    print()

    # Get all manual classes
    manual_classes = get_classes_from_module(manual_schemas)

    print(f"📊 Found {len(manual_classes)} manual schema classes")
    print()

    # Focus on key AdCP models
    key_models = [
        "CreateMediaBuyRequest",
        "CreateMediaBuyResponse",
        "GetProductsRequest",
        "GetProductsResponse",
        "Product",
        "Package",
        "Budget",
        "Targeting",
        "CreativeAsset",
        "Format",
    ]

    print("🔍 Analyzing key AdCP models:")
    print()

    for model_name in key_models:
        if model_name not in manual_classes:
            print(f"⚠️  {model_name}: NOT FOUND in manual schemas")
            continue

        manual_model = manual_classes[model_name]
        manual_fields = get_fields_from_pydantic_model(manual_model)

        print(f"✅ {model_name}:")
        print(f"   - {len(manual_fields)} fields")

        # Check for custom methods
        custom_methods = []
        for name, obj in inspect.getmembers(manual_model):
            if (
                not name.startswith("_")
                and callable(obj)
                and name
                not in [
                    "model_validate",
                    "model_dump",
                    "model_dump_json",
                    "model_construct",
                    "model_fields",
                    "model_config",
                ]
            ):
                custom_methods.append(name)

        if custom_methods:
            print(f"   - Custom methods: {', '.join(custom_methods)}")

        # Check for validators
        if hasattr(manual_model, "__pydantic_decorators__"):
            validators = []
            decorators = manual_model.__pydantic_decorators__
            if hasattr(decorators, "model_validators"):
                validators.extend(decorators.model_validators.keys())
            if hasattr(decorators, "field_validators"):
                validators.extend(decorators.field_validators.keys())

            if validators:
                print(f"   - Custom validators: {', '.join(validators)}")

        print()

    print("=" * 80)
    print("BREAKING CHANGES IF SWITCHING TO GENERATED SCHEMAS")
    print("=" * 80)
    print()

    breaking_changes = []

    # 1. Custom validators
    print("1. 🔴 CUSTOM VALIDATORS")
    print("   Models with custom validation logic that won't be in generated schemas:")
    print()

    models_with_validators = []
    for model_name, model_class in manual_classes.items():
        if hasattr(model_class, "__pydantic_decorators__"):
            decorators = model_class.__pydantic_decorators__
            has_validators = False

            if hasattr(decorators, "model_validators") and decorators.model_validators:
                has_validators = True
            if hasattr(decorators, "field_validators") and decorators.field_validators:
                has_validators = True

            if has_validators:
                models_with_validators.append(model_name)

    if models_with_validators:
        for name in models_with_validators[:10]:  # Show first 10
            print(f"   - {name}")
        if len(models_with_validators) > 10:
            print(f"   ... and {len(models_with_validators) - 10} more")
        breaking_changes.append(f"{len(models_with_validators)} models have custom validators")
    else:
        print("   ✅ No custom validators found")
    print()

    # 2. Custom methods
    print("2. 🔴 CUSTOM METHODS")
    print("   Models with custom methods that won't be in generated schemas:")
    print()

    models_with_methods = []
    for model_name, model_class in manual_classes.items():
        custom_methods = []
        for name, obj in inspect.getmembers(model_class):
            if (
                not name.startswith("_")
                and callable(obj)
                and name
                not in [
                    "model_validate",
                    "model_dump",
                    "model_dump_json",
                    "model_construct",
                    "model_fields",
                    "model_config",
                    "model_copy",
                    "model_json_schema",
                ]
            ):
                custom_methods.append(name)

        if custom_methods:
            models_with_methods.append((model_name, custom_methods))

    if models_with_methods:
        for name, methods in models_with_methods[:10]:
            print(f"   - {name}: {', '.join(methods)}")
        if len(models_with_methods) > 10:
            print(f"   ... and {len(models_with_methods) - 10} more")
        breaking_changes.append(f"{len(models_with_methods)} models have custom methods")
    else:
        print("   ✅ No custom methods found")
    print()

    # 3. Internal fields
    print("3. 🟡 INTERNAL FIELDS")
    print("   Fields in manual schemas not in AdCP spec (will be missing in generated):")
    print()

    # These are known internal fields
    internal_fields = {
        "CreateMediaBuyRequest": ["webhook_url", "webhook_auth_token", "campaign_name", "currency"],
        # Add more as discovered
    }

    for model_name, fields in internal_fields.items():
        print(f"   - {model_name}: {', '.join(fields)}")
        breaking_changes.append(f"{model_name} has {len(fields)} internal fields")
    print()

    # 4. Import paths
    print("4. 🟡 IMPORT PATHS")
    print("   Import paths will change:")
    print()
    print("   OLD: from src.core.schemas import CreateMediaBuyRequest")
    print(
        "   NEW: from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import CreateMediaBuyRequest"
    )
    print()
    breaking_changes.append("All import paths will change")
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()

    print(f"🔴 Breaking Changes: {len(breaking_changes)}")
    for i, change in enumerate(breaking_changes, 1):
        print(f"   {i}. {change}")
    print()

    print("💡 RECOMMENDATION:")
    print()
    print("   Phase 1: Keep manual schemas, use generated for validation reference")
    print("   Phase 2: Migrate simple models (no validators/methods)")
    print("   Phase 3: Wrap complex models with generated schemas as base")
    print()

    print("   Example hybrid approach:")
    print()
    print("   ```python")
    print("   from src.core.schemas_generated._schemas_v1_core_budget_json import Budget as BudgetBase")
    print()
    print("   class Budget(BudgetBase):")
    print("       # Add custom methods")
    print("       def validate_minimum(self): ...")
    print("   ```")
    print()


if __name__ == "__main__":
    main()

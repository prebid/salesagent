#!/usr/bin/env python3
"""
Schema-Database Field Alignment Validator

This script validates that Pydantic schema fields align with database model fields
to prevent AttributeError bugs like 'Product' object has no attribute 'pricing'.

Used as a pre-commit hook to catch schema-database mismatches before they reach production.
"""

import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.database.models import Principal
from src.core.database.models import Product as ProductModel
from src.core.schemas import Principal as PrincipalSchema
from src.core.schemas import Product


def get_database_fields(model_class) -> set[str]:
    """Get all column names from a SQLAlchemy model."""
    return {column.name for column in model_class.__table__.columns}


def get_schema_fields(schema_class) -> set[str]:
    """Get all field names from a Pydantic model."""
    return set(schema_class.model_fields.keys())


def validate_field_alignment(
    model_class, schema_class, internal_fields: set[str] = None, computed_fields: set[str] = None
) -> tuple[bool, list[str]]:
    """
    Validate that schema fields align with database fields.

    Args:
        model_class: SQLAlchemy model class
        schema_class: Pydantic schema class
        internal_fields: Database fields that should not be in external schema
        computed_fields: Schema fields that are computed/derived (not in database)

    Returns:
        Tuple of (is_valid, error_messages)
    """
    internal_fields = internal_fields or set()
    computed_fields = computed_fields or set()

    db_fields = get_database_fields(model_class)
    schema_fields = get_schema_fields(schema_class)

    errors = []

    # Fields that must exist in database for schema fields (excluding computed)
    required_db_fields = schema_fields - computed_fields
    missing_db_fields = required_db_fields - db_fields - internal_fields

    if missing_db_fields:
        errors.append(
            f"{schema_class.__name__} schema has fields missing from {model_class.__name__} database: "
            f"{sorted(missing_db_fields)}. "
            f"Add these columns to {model_class.__tablename__} table or mark as computed_fields. "
            f"This could cause AttributeError when accessing these fields from ORM objects."
        )

    # Check for common problematic field patterns
    problematic_patterns = {
        "pricing": "Use cpm field instead of pricing",
        "cost": "Cost calculation should be computed, not stored",
        "margin": "Margin should be computed from cpm and cost",
        "profit": "Profit should be computed, not stored",
    }

    for schema_field in schema_fields:
        if schema_field in problematic_patterns:
            if schema_field not in computed_fields:
                errors.append(
                    f"Field '{schema_field}' in {schema_class.__name__} schema should be computed field: "
                    f"{problematic_patterns[schema_field]}. "
                    f"Add '{schema_field}' to computed_fields set in validate_{schema_class.__name__.lower()}_alignment()."
                )

    return len(errors) == 0, errors


def validate_product_alignment() -> tuple[bool, list[str]]:
    """Validate Product schema alignment with ProductModel database."""
    internal_fields = {
        "tenant_id",  # Multi-tenancy field
        "targeting_template",  # Internal targeting configuration
        "price_guidance",  # Legacy field not in AdCP spec
        "countries",  # Not part of AdCP Product schema
        "implementation_config",  # Ad server-specific config
    }

    computed_fields = {
        "brief_relevance",  # Populated when brief is provided
        "currency",  # AdCP PR #79: Calculated dynamically, not stored
        "estimated_exposures",  # AdCP PR #79: Calculated from historical data
        "floor_cpm",  # AdCP PR #79: Calculated dynamically
        "recommended_cpm",  # AdCP PR #79: Calculated to meet exposure goals
    }

    return validate_field_alignment(ProductModel, Product, internal_fields, computed_fields)


def validate_principal_alignment() -> tuple[bool, list[str]]:
    """Validate Principal schema alignment with Principal database model."""
    internal_fields = {
        "tenant_id",  # Multi-tenancy field
        "access_token",  # Security field not exposed externally
    }

    computed_fields = {
        "adapter_mappings",  # Derived from platform_mappings
    }

    return validate_field_alignment(Principal, PrincipalSchema, internal_fields, computed_fields)


def check_database_access_patterns() -> tuple[bool, list[str]]:
    """Check for unsafe database field access patterns in code."""
    import glob
    import re

    errors = []

    # Files to check for database access patterns
    python_files = glob.glob("src/**/*.py", recursive=True)
    python_files.extend(glob.glob("product_catalog_providers/**/*.py", recursive=True))

    # Patterns that indicate unsafe field access
    unsafe_patterns = [
        (r"\.pricing\b", "pricing field access (use cpm instead)"),
        (r"\.format_ids\b", "format_ids is schema property (use formats from database)"),
        (r"\.cost_basis\b", "cost_basis field does not exist in database"),
        (r"\.margin\b", "margin should be computed, not accessed from database"),
    ]

    for file_path in python_files:
        if "test" in file_path or "__pycache__" in file_path:
            continue

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            for pattern, description in unsafe_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    errors.append(
                        f"Unsafe database field access in {file_path}: {description}. "
                        f"Found {len(matches)} instance(s). "
                        f"Use safe field access patterns or update field name to match database schema."
                    )
        except Exception as e:
            # Skip files that can't be read
            continue

    return len(errors) == 0, errors


def main():
    """Main validation function."""
    parser = argparse.ArgumentParser(description="Validate schema-database field alignment")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only show errors")
    parser.add_argument("--check-code", action="store_true", help="Also check code for unsafe patterns")
    args = parser.parse_args()

    all_valid = True
    all_errors = []

    # Validate Product alignment
    if not args.quiet:
        print("🔍 Validating Product schema-database alignment...")

    is_valid, errors = validate_product_alignment()
    if not is_valid:
        all_valid = False
        all_errors.extend([f"Product: {error}" for error in errors])
    elif not args.quiet:
        print("✅ Product schema-database alignment OK")

    # Validate Principal alignment
    if not args.quiet:
        print("🔍 Validating Principal schema-database alignment...")

    is_valid, errors = validate_principal_alignment()
    if not is_valid:
        all_valid = False
        all_errors.extend([f"Principal: {error}" for error in errors])
    elif not args.quiet:
        print("✅ Principal schema-database alignment OK")

    # Check code patterns if requested
    if args.check_code:
        if not args.quiet:
            print("🔍 Checking code for unsafe database access patterns...")

        is_valid, errors = check_database_access_patterns()
        if not is_valid:
            all_valid = False
            all_errors.extend([f"Code pattern: {error}" for error in errors])
        elif not args.quiet:
            print("✅ No unsafe database access patterns found")

    # Report results
    if not all_valid:
        print("\n❌ Schema-database alignment validation FAILED:")
        for error in all_errors:
            print(f"  • {error}")
        print(f"\nTotal issues: {len(all_errors)}")
        print("\nThese issues could cause 'object has no attribute' errors in production.")
        print("Fix alignment issues before committing.")
        return 1
    else:
        if not args.quiet:
            print("✅ All schema-database alignment checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())

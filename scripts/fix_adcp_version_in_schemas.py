#!/usr/bin/env python3
"""
Remove adcp_version field from cached AdCP schemas.

The adcp_version field was removed from the official AdCP spec but our
cached schemas still have it. This script removes it from all affected files.
"""

import json
from pathlib import Path


def remove_adcp_version(schema: dict) -> bool:
    """
    Remove adcp_version from schema properties and required array.
    Returns True if changes were made.
    """
    changed = False

    # Remove from properties
    if "properties" in schema and "adcp_version" in schema["properties"]:
        del schema["properties"]["adcp_version"]
        changed = True
        print("    - Removed from properties")

    # Remove from required array
    if "required" in schema and "adcp_version" in schema["required"]:
        schema["required"].remove("adcp_version")
        changed = True
        print("    - Removed from required")

    return changed


def main():
    schema_dir = Path("tests/e2e/schemas/v1")

    if not schema_dir.exists():
        print(f"❌ Schema directory not found: {schema_dir}")
        return

    # Find all schema files with adcp_version
    schema_files = list(schema_dir.glob("*.json"))
    # Skip these non-schema files
    skip_files = {"index.json", "SCHEMAS_INFO.md"}
    schema_files = [f for f in schema_files if f.name not in skip_files]

    print(f"🔍 Checking {len(schema_files)} schema files for adcp_version\n")

    total_changed = 0

    for schema_file in sorted(schema_files):
        # Load schema
        with open(schema_file) as f:
            schema = json.load(f)

        # Check if it has adcp_version
        has_adcp_version = False
        if "properties" in schema:
            has_adcp_version = "adcp_version" in schema["properties"]

        if not has_adcp_version:
            continue

        print(f"📝 {schema_file.name}")

        # Remove adcp_version
        if remove_adcp_version(schema):
            # Write back to file
            with open(schema_file, "w") as f:
                json.dump(schema, f, indent=2)

            total_changed += 1
            print("    ✅ Updated\n")

    print(f"\n✅ Updated {total_changed} schema files")
    print("\n📊 Next steps:")
    print("  1. Run: uv run python scripts/generate_schemas.py")
    print("  2. Review changes in src/core/schemas_generated/")
    print("  3. Update schema_adapters.py to match")


if __name__ == "__main__":
    main()

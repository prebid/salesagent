#!/usr/bin/env python3
"""
Refresh all cached AdCP schemas from the official website.

This script downloads the latest schemas from adcontextprotocol.org
and updates our cached copies in tests/e2e/schemas/v1/.
"""

import json
import sys
from pathlib import Path

import httpx


def filename_to_url(filename: str) -> str:
    """Convert our flattened filename to AdCP URL path."""
    # _schemas_v1_media-buy_get-products-response_json.json
    # -> /schemas/v1/media-buy/get-products-response.json

    # Remove leading underscore and trailing .json
    path = filename[1:] if filename.startswith("_") else filename
    if path.endswith(".json"):
        path = path[:-5]  # Remove .json

    # Replace underscores with appropriate separators
    # _schemas_v1_ -> /schemas/v1/
    # _json -> .json
    path = path.replace("_schemas_v1_", "/schemas/v1/")
    path = path.replace("_json", ".json")
    path = path.replace("_", "-")  # Remaining underscores are hyphens

    return path


def download_schema(url: str, output_path: Path) -> bool:
    """Download a schema from AdCP website."""
    base_url = "https://adcontextprotocol.org"
    full_url = f"{base_url}{url}"

    try:
        print(f"  📥 {url}")
        response = httpx.get(full_url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()

        schema = response.json()

        # Save to cache
        with open(output_path, "w") as f:
            json.dump(schema, f, indent=2)

        return True

    except Exception as e:
        print(f"  ❌ Failed: {e}", file=sys.stderr)
        return False


def main():
    schema_dir = Path("tests/e2e/schemas/v1")

    if not schema_dir.exists():
        print(f"❌ Schema directory not found: {schema_dir}", file=sys.stderr)
        sys.exit(1)

    # Get all existing schema files
    schema_files = list(schema_dir.glob("*.json"))
    # Skip these non-schema files
    skip_files = {"index.json", "SCHEMAS_INFO.md"}
    schema_files = [f for f in schema_files if f.name not in skip_files]

    print(f"🔄 Refreshing {len(schema_files)} schemas from adcontextprotocol.org\n")

    success_count = 0
    fail_count = 0

    for schema_file in sorted(schema_files):
        url = filename_to_url(schema_file.name)

        if download_schema(url, schema_file):
            success_count += 1
        else:
            fail_count += 1

    print(f"\n✅ Downloaded {success_count} schemas")
    if fail_count > 0:
        print(f"❌ Failed {fail_count} schemas", file=sys.stderr)
        sys.exit(1)

    print("\n📊 Next steps:")
    print("  1. Run: uv run python scripts/generate_schemas.py")
    print("  2. Review changes in src/core/schemas_generated/")
    print("  3. Update schema_adapters.py to match")


if __name__ == "__main__":
    main()

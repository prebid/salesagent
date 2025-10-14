#!/usr/bin/env python3
"""
Generate Pydantic models from AdCP JSON schemas.

This script uses datamodel-code-generator to auto-generate Pydantic models
from the official AdCP JSON schemas cached in tests/e2e/schemas/v1/.

The script handles $ref resolution by creating a custom loader that maps
the official $ref paths to our flattened file structure.

Usage:
    python scripts/generate_schemas.py [--output OUTPUT_FILE]

The generated models should match the official AdCP spec exactly.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx


def load_schema_with_resolver(schema_path: Path, schema_dir: Path) -> dict:
    """
    Load a schema and create a custom loader for $ref resolution.

    This function creates a loader that maps AdCP $ref paths like
    "/schemas/v1/enums/pacing.json" to our flattened file structure
    "_schemas_v1_enums_pacing_json.json".
    """

    def ref_to_filename(ref: str) -> str:
        """Convert $ref path to our flattened filename format."""
        # /schemas/v1/enums/pacing.json -> _schemas_v1_enums_pacing_json.json
        return ref.replace("/", "_").replace(".", "_") + ".json"

    def load_ref(ref: str) -> dict:
        """Load a schema from a $ref path."""
        filename = ref_to_filename(ref)
        ref_path = schema_dir / filename

        if not ref_path.exists():
            raise FileNotFoundError(f"Referenced schema not found: {ref} (looked for {ref_path})")

        with open(ref_path) as f:
            return json.load(f)

    return load_ref  # type: ignore[return-value]


def download_missing_schema(ref: str, schema_dir: Path) -> bool:
    """
    Download a missing schema from AdCP website.

    Returns True if download successful, False otherwise.
    """
    base_url = "https://adcontextprotocol.org"
    schema_url = f"{base_url}{ref}"
    ref_filename = ref.replace("/", "_").replace(".", "_") + ".json"
    ref_path = schema_dir / ref_filename

    try:
        print(f"   📥 Downloading missing schema: {ref}")
        response = httpx.get(schema_url, timeout=10.0)
        response.raise_for_status()

        schema = response.json()

        # Save to cache
        with open(ref_path, "w") as f:
            json.dump(schema, f, indent=2)

        print(f"   ✅ Downloaded: {ref_filename}")
        return True

    except Exception as e:
        print(f"   ❌ Failed to download {ref}: {e}", file=sys.stderr)
        return False


def resolve_refs_in_schema(schema: dict, schema_dir: Path, visited: set | None = None) -> dict:
    """
    Recursively resolve all $ref references in a schema.

    Returns a new schema dict with all references inlined.
    Downloads missing schemas from AdCP website automatically.
    """
    if visited is None:
        visited = set()

    # Handle $ref
    if "$ref" in schema:
        ref = schema["$ref"]

        # Avoid circular references
        if ref in visited:
            return {"description": f"Circular reference to {ref}"}

        visited.add(ref)

        # Load referenced schema
        ref_filename = ref.replace("/", "_").replace(".", "_") + ".json"
        ref_path = schema_dir / ref_filename

        if not ref_path.exists():
            # Try downloading missing schema
            if not download_missing_schema(ref, schema_dir):
                print(f"⚠️  Warning: Cannot resolve $ref: {ref}", file=sys.stderr)
                return schema

        with open(ref_path) as f:
            ref_schema = json.load(f)

        # Recursively resolve references in the loaded schema
        resolved = resolve_refs_in_schema(ref_schema, schema_dir, visited)

        # Merge any properties from original schema (e.g., description)
        for key, value in schema.items():
            if key != "$ref" and key not in resolved:
                resolved[key] = value

        return resolved

    # Recursively process nested schemas
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            result[key] = resolve_refs_in_schema(value, schema_dir, visited)
        elif isinstance(value, list):
            result[key] = [
                resolve_refs_in_schema(item, schema_dir, visited) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value

    return result


def generate_schemas_from_json(schema_dir: Path, output_file: Path):
    """
    Generate Pydantic models from JSON schemas with proper $ref resolution.
    """
    print(f"📂 Processing schemas from: {schema_dir}")

    # Create temporary directory for resolved schemas
    temp_dir = Path("temp_resolved_schemas")
    temp_dir.mkdir(exist_ok=True)

    try:
        # Process each JSON schema file
        schema_files = list(schema_dir.glob("*.json"))
        print(f"📝 Found {len(schema_files)} schema files")

        # Skip these non-schema files
        skip_files = {"index.json", "SCHEMAS_INFO.md"}

        for schema_file in schema_files:
            if schema_file.name in skip_files:
                continue

            print(f"   Processing: {schema_file.name}")

            # Load and resolve all $refs
            with open(schema_file) as f:
                schema = json.load(f)

            resolved_schema = resolve_refs_in_schema(schema, schema_dir)

            # Write resolved schema to temp directory
            temp_file = temp_dir / schema_file.name
            with open(temp_file, "w") as f:
                json.dump(resolved_schema, f, indent=2)

        print(f"✅ Resolved all $refs, generated {len(list(temp_dir.glob('*.json')))} schemas")

        # Now run datamodel-codegen on resolved schemas
        print("\n🔧 Generating Pydantic models...")

        cmd = [
            "datamodel-codegen",
            "--input",
            str(temp_dir),
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--field-constraints",
            "--use-standard-collections",
            "--collapse-root-models",
            "--use-double-quotes",
            "--snake-case-field",
            "--target-python-version",
            "3.12",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("❌ Generation failed:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            sys.exit(1)

        print(f"✅ Generated Pydantic models: {output_file}")

        # Add header comment to __init__.py
        init_file = output_file / "__init__.py"
        if not init_file.exists():
            init_file.touch()

        header = '''"""
Auto-generated Pydantic models from AdCP JSON schemas.

⚠️  DO NOT EDIT FILES IN THIS DIRECTORY MANUALLY!

Generated from: tests/e2e/schemas/v1/
Generator: scripts/generate_schemas.py
Tool: datamodel-code-generator + custom $ref resolution

To regenerate:
    python scripts/generate_schemas.py

Source: https://adcontextprotocol.org/schemas/v1/
AdCP Version: v2.4 (schemas v1)
"""
'''

        with open(init_file, "w") as f:
            f.write(header)

        print("✅ Added header to __init__.py")

    finally:
        # Clean up temp directory
        import shutil

        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            print("🧹 Cleaned up temporary files")


def main():
    parser = argparse.ArgumentParser(description="Generate Pydantic models from AdCP JSON schemas")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/core/schemas_generated"),
        help="Output directory for generated schemas (default: src/core/schemas_generated/)",
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path("tests/e2e/schemas/v1"),
        help="Directory containing JSON schemas (default: tests/e2e/schemas/v1)",
    )
    args = parser.parse_args()

    if not args.schema_dir.exists():
        print(f"❌ Schema directory not found: {args.schema_dir}", file=sys.stderr)
        sys.exit(1)

    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)

    generate_schemas_from_json(args.schema_dir, args.output)

    print("\n📊 Next steps:")
    print("  1. Review generated schemas in", args.output)
    print("  2. Compare with manual schemas in src/core/schemas.py")
    print("  3. Identify which models to use (generated vs manual)")
    print("  4. Run tests to ensure compatibility")


if __name__ == "__main__":
    main()

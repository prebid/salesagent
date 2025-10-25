#!/usr/bin/env python3
"""
Generate Pydantic models from AdCP JSON schemas.

This script uses datamodel-code-generator to auto-generate Pydantic models
from the official AdCP JSON schemas cached in schemas/v1/.

The script handles $ref resolution by creating a custom loader that maps
the official $ref paths to our flattened file structure.

Usage:
    python scripts/generate_schemas.py [--output OUTPUT_FILE]

The generated models should match the official AdCP spec exactly.
"""

import argparse
import hashlib
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
            f.write("\n")  # Add trailing newline for pre-commit compatibility

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


def add_etag_metadata_to_generated_files(output_dir: Path, schema_dir: Path):
    """
    Add source schema ETag metadata to generated Python files.

    This replaces the generation timestamp (which changes on every run)
    with the source schema's ETag (which only changes when schema changes).
    """
    generated_files = list(output_dir.glob("_schemas_*.py"))
    updated_count = 0

    for py_file in generated_files:
        # Find corresponding .meta file
        # Python file: _schemas_v1_core_creative_asset_json.py (underscores)
        # JSON file:   _schemas_v1_core_creative-asset_json.json (hyphens)
        # Meta file:   _schemas_v1_core_creative-asset_json.json.meta
        #
        # datamodel-codegen converts hyphens to underscores in filenames,
        # so we need to convert back to find the original JSON file

        # Extract the filename part from the generated header comment
        # Read first few lines to find the original filename
        with open(py_file) as f:
            header_lines = [f.readline() for _ in range(5)]

        # Look for: #   filename:  _schemas_v1_core_creative-asset_json.json
        original_json_filename = None
        for line in header_lines:
            if line.strip().startswith("#   filename:"):
                original_json_filename = line.split(":", 1)[1].strip()
                break

        if not original_json_filename:
            continue

        meta_file = schema_dir / f"{original_json_filename}.meta"

        if not meta_file.exists():
            continue

        # Load ETag from .meta file
        try:
            with open(meta_file) as f:
                metadata = json.load(f)
                etag = metadata.get("etag", "unknown")
                last_modified = metadata.get("last-modified", "unknown")
        except (json.JSONDecodeError, OSError):
            continue

        # Read generated file
        with open(py_file) as f:
            content = f.read()

        # Add ETag comment after the datamodel-codegen header
        # Look for the pattern:
        # # generated by datamodel-codegen:
        # #   filename:  ...
        #
        # And insert after it:
        # #   source_etag: W/"..."
        # #   source_last_modified: ...

        lines = content.split("\n")
        new_lines = []
        inserted = False

        for line in lines:
            new_lines.append(line)

            # Insert after the filename line
            if not inserted and line.startswith("#   filename:"):
                new_lines.append(f"#   source_etag: {etag}")
                new_lines.append(f"#   source_last_modified: {last_modified}")
                inserted = True

        if inserted:
            # Write updated content
            with open(py_file, "w") as f:
                f.write("\n".join(new_lines))
            updated_count += 1

    print(f"✅ Added ETag metadata to {updated_count} generated files")


def compute_schema_hash(schema_dir: Path) -> str:
    """Compute a hash of all JSON schema files for sync checking."""
    hash_md5 = hashlib.md5()

    # Get all JSON schema files, sorted for consistency
    schema_files = sorted(schema_dir.glob("*.json"))

    for schema_file in schema_files:
        # Read and normalize JSON (to handle formatting differences)
        try:
            with open(schema_file) as f:
                data = json.load(f)
            # Convert back to JSON with consistent formatting
            normalized = json.dumps(data, sort_keys=True)
            hash_md5.update(normalized.encode())
        except (json.JSONDecodeError, FileNotFoundError):
            # Skip invalid JSON files
            continue

    return hash_md5.hexdigest()


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
                f.write("\n")  # Add trailing newline for pre-commit compatibility

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
            "--use-union-operator",  # Use X | Y syntax instead of Union[X, Y] and Optional[X]
            "--disable-timestamp",  # Don't add timestamp comments (causes unnecessary git noise)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("❌ Generation failed:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            sys.exit(1)

        print(f"✅ Generated Pydantic models: {output_file}")

        # Add ETag metadata to generated Python files
        print("\n🔖 Adding source schema ETag metadata to generated files...")
        add_etag_metadata_to_generated_files(output_file, schema_dir)

        # Add header comment to __init__.py with schema hash for sync checking
        init_file = output_file / "__init__.py"
        if not init_file.exists():
            init_file.touch()

        # Compute hash of source schemas for sync checking
        schema_hash = compute_schema_hash(schema_dir)

        header = f'''# SCHEMA_HASH: {schema_hash}
"""
Auto-generated Pydantic models from AdCP JSON schemas.

⚠️  DO NOT EDIT FILES IN THIS DIRECTORY MANUALLY!

Generated from: schemas/v1/
Generator: scripts/generate_schemas.py
Tool: datamodel-code-generator + custom $ref resolution

To regenerate:
    python scripts/generate_schemas.py

Source: https://adcontextprotocol.org/schemas/v1/
AdCP Version: v2.4 (schemas v1)

The SCHEMA_HASH above is used by pre-commit hooks to detect when
generated schemas are out of sync with JSON schemas.
"""
'''

        with open(init_file, "w") as f:
            f.write(header)

        print(f"✅ Added header to __init__.py (schema hash: {schema_hash[:8]}...)")

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
        default=Path("schemas/v1"),
        help="Directory containing JSON schemas (default: schemas/v1)",
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

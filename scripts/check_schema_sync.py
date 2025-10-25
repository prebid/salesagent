#!/usr/bin/env python3
"""
Schema Sync Checker for AdCP Protocol

This script validates that our locally cached schemas are in sync with the
latest schemas from adcontextprotocol.org. It checks for:
1. Schema content differences between local cache and live registry
2. Missing or extra schema files
3. Version mismatches
4. Critical schema field changes

Usage:
    uv run python scripts/check_schema_sync.py
    uv run python scripts/check_schema_sync.py --update  # Auto-update schemas
    uv run python scripts/check_schema_sync.py --ci      # CI mode (exit 1 on failures)
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class SchemaSyncError(Exception):
    """Raised when schema sync check fails."""

    pass


class SchemaSyncChecker:
    """Compares local cached schemas with live schemas from AdCP registry."""

    # Official AdCP schema registry endpoints
    BASE_SCHEMA_URL = "https://adcontextprotocol.org/schemas/v1"
    INDEX_URL = "https://adcontextprotocol.org/schemas/v1/index.json"

    def __init__(self, ci_mode: bool = False, auto_update: bool = False):
        self.ci_mode = ci_mode
        self.auto_update = auto_update
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.updates_applied: list[str] = []

        # Local schema cache directory
        self.cache_dir = Path("schemas/v1")

        # HTTP client for fetching live schemas
        self.http_client = httpx.AsyncClient(timeout=30.0)

        # Key schemas to check (high priority) - only for endpoints we implement
        self.critical_schemas = [
            "/schemas/v1/core/product.json",
            "/schemas/v1/media-buy/get-products-response.json",
            "/schemas/v1/media-buy/get-products-request.json",
            "/schemas/v1/core/creative-asset.json",
            "/schemas/v1/core/targeting.json",
            "/schemas/v1/media-buy/package-request.json",  # Critical for format_ids structure
            "/schemas/v1/core/format-id.json",  # Critical for format_ids validation
        ]

        # Endpoints we actually implement (only validate schemas for these)
        self.implemented_endpoints = {
            "get_products": [
                "/schemas/v1/media-buy/get-products-request.json",
                "/schemas/v1/media-buy/get-products-response.json",
            ],
            "list_creative_formats": [
                "/schemas/v1/media-buy/list-creative-formats-request.json",
                "/schemas/v1/media-buy/list-creative-formats-response.json",
            ],
            "sync_creatives": [
                "/schemas/v1/media-buy/sync-creatives-request.json",
                "/schemas/v1/media-buy/sync-creatives-response.json",
            ],
            "list_creatives": [
                "/schemas/v1/media-buy/list-creatives-request.json",
                "/schemas/v1/media-buy/list-creatives-response.json",
            ],
            "get_signals": [
                "/schemas/v1/signals/get-signals-request.json",
                "/schemas/v1/signals/get-signals-response.json",
            ],
            "create_media_buy": [
                "/schemas/v1/media-buy/create-media-buy-request.json",
                "/schemas/v1/media-buy/create-media-buy-response.json",
            ],
            "update_media_buy": [
                "/schemas/v1/media-buy/update-media-buy-request.json",
                "/schemas/v1/media-buy/update-media-buy-response.json",
            ],
            "get_media_buy_delivery": [
                "/schemas/v1/media-buy/get-media-buy-delivery-request.json",
                "/schemas/v1/media-buy/get-media-buy-delivery-response.json",
            ],
            "list_authorized_properties": [
                "/schemas/v1/media-buy/list-authorized-properties-request.json",
                "/schemas/v1/media-buy/list-authorized-properties-response.json",
            ],
            # Core schemas needed by implemented endpoints
            "core_schemas": [
                "/schemas/v1/core/product.json",
                "/schemas/v1/core/creative-asset.json",
                "/schemas/v1/core/targeting.json",
                "/schemas/v1/core/budget.json",
                "/schemas/v1/core/measurement.json",
                "/schemas/v1/core/creative-policy.json",
                "/schemas/v1/core/format.json",
                "/schemas/v1/core/format-id.json",  # Added per PR #123
                "/schemas/v1/core/frequency-cap.json",
                "/schemas/v1/core/package.json",
                "/schemas/v1/media-buy/package-request.json",  # Added per PR #123
                "/schemas/v1/core/media-buy.json",
                "/schemas/v1/core/error.json",
                "/schemas/v1/core/response.json",
            ],
        }

        # Flatten into a single set of required schemas
        self.required_schemas = set()
        for endpoint_schemas in self.implemented_endpoints.values():
            self.required_schemas.update(endpoint_schemas)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.http_client.aclose()

    def log_error(self, message: str):
        """Log an error that will cause CI failure."""
        self.errors.append(message)
        if self.ci_mode:
            print(f"❌ ERROR: {message}", file=sys.stderr)
        else:
            print(f"❌ {message}")

    def log_warning(self, message: str):
        """Log a warning that won't cause CI failure."""
        self.warnings.append(message)
        print(f"⚠️ WARNING: {message}")

    def log_success(self, message: str):
        """Log a successful check."""
        print(f"✅ {message}")

    def log_update(self, message: str):
        """Log an applied update."""
        self.updates_applied.append(message)
        print(f"🔄 UPDATED: {message}")

    def _get_cached_schema_path(self, schema_ref: str) -> Path:
        """Convert schema reference to local cache file path."""
        # Convert "/schemas/v1/core/product.json" to "_schemas_v1_core_product_json.json"
        filename = schema_ref.replace("/", "_").replace(".", "_") + ".json"
        return self.cache_dir / filename

    async def _fetch_live_schema(self, schema_ref: str, max_retries: int = 3) -> dict[str, Any]:
        """Fetch a schema from the live AdCP registry with retry logic for transient errors."""
        schema_url = f"https://adcontextprotocol.org{schema_ref}"

        for attempt in range(max_retries):
            try:
                response = await self.http_client.get(schema_url)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                # Retry on 502 Bad Gateway (common transient error)
                if e.response.status_code == 502 and attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    print(
                        f"⚠️  502 error for {schema_ref}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise SchemaSyncError(f"Failed to fetch live schema {schema_ref}: {e}")
            except Exception as e:
                raise SchemaSyncError(f"Failed to fetch live schema {schema_ref}: {e}")

    async def _fetch_live_index(self, max_retries: int = 3) -> dict[str, Any]:
        """Fetch the live schema index/registry with retry logic for transient errors."""
        for attempt in range(max_retries):
            try:
                response = await self.http_client.get(self.INDEX_URL)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                # Retry on 502 Bad Gateway (common transient error)
                if e.response.status_code == 502 and attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"⚠️  502 error for index, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                raise SchemaSyncError(f"Failed to fetch live schema index: {e}")
            except Exception as e:
                raise SchemaSyncError(f"Failed to fetch live schema index: {e}")

    def _load_cached_schema(self, schema_ref: str) -> dict[str, Any] | None:
        """Load a schema from local cache."""
        cache_path = self._get_cached_schema_path(schema_ref)
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except Exception as e:
                self.log_warning(f"Failed to load cached schema {schema_ref}: {e}")
        return None

    def _save_cached_schema(self, schema_ref: str, schema_data: dict[str, Any]):
        """Save a schema to local cache."""
        cache_path = self._get_cached_schema_path(schema_ref)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        with open(cache_path, "w") as f:
            json.dump(schema_data, f, indent=2)
            f.write("\n")  # Add trailing newline for pre-commit compatibility

    def _schemas_are_equal(self, schema1: dict[str, Any], schema2: dict[str, Any]) -> bool:
        """Compare two schemas for equality (ignoring metadata)."""

        # Remove metadata fields that might differ but don't affect schema validation
        def normalize_schema(schema):
            normalized = schema.copy()
            # Remove fields that can vary without affecting validation
            for field in ["$id", "lastUpdated", "generated"]:
                normalized.pop(field, None)
            return normalized

        norm1 = normalize_schema(schema1)
        norm2 = normalize_schema(schema2)

        return norm1 == norm2

    def _get_schema_hash(self, schema_data: dict[str, Any]) -> str:
        """Get a hash of schema content for comparison."""
        # Normalize and hash the schema
        normalized = json.dumps(schema_data, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def validate_format_ids_structure(self) -> bool:
        """Validate that package-request.json uses FormatId objects, not strings.

        This prevents the issue where cached schemas had format_ids as strings
        instead of FormatId objects with $ref to format-id.json.
        """
        print("🔍 Validating format_ids structure in package-request.json...")

        try:
            schema_ref = "/schemas/v1/media-buy/package-request.json"
            cached_schema = self._load_cached_schema(schema_ref)

            if not cached_schema:
                self.log_error(f"Missing cached schema: {schema_ref}")
                return False

            # Check if format_ids uses $ref to format-id.json
            format_ids_def = cached_schema.get("properties", {}).get("format_ids", {})
            items_def = format_ids_def.get("items", {})

            if "$ref" in items_def:
                ref_value = items_def["$ref"]
                if ref_value == "/schemas/v1/core/format-id.json":
                    self.log_success("✅ package-request.json correctly uses FormatId objects (not strings)")
                    return True
                else:
                    self.log_error(
                        f"format_ids has unexpected $ref: {ref_value} (expected /schemas/v1/core/format-id.json)"
                    )
                    return False
            elif items_def.get("type") == "string":
                self.log_error("❌ format_ids uses strings instead of FormatId objects! Schema is outdated.")
                self.log_error("   Run: uv run python scripts/check_schema_sync.py --update")
                return False
            else:
                self.log_warning(f"format_ids has unexpected structure: {items_def}")
                return False

        except Exception as e:
            self.log_error(f"Failed to validate format_ids structure: {e}")
            return False

    async def check_critical_schemas(self) -> bool:
        """Check if critical schemas are in sync."""
        print("🔍 Checking critical schemas...")

        all_synced = True

        for schema_ref in self.critical_schemas:
            try:
                # Fetch live schema
                live_schema = await self._fetch_live_schema(schema_ref)

                # Load cached schema
                cached_schema = self._load_cached_schema(schema_ref)

                if cached_schema is None:
                    self.log_error(f"Missing cached schema: {schema_ref}")
                    if self.auto_update:
                        self._save_cached_schema(schema_ref, live_schema)
                        self.log_update(f"Downloaded missing schema: {schema_ref}")
                    all_synced = False
                    continue

                # Use _schemas_are_equal() to ignore metadata fields (consistent with check_all_schemas_in_index)
                if not self._schemas_are_equal(live_schema, cached_schema):
                    self.log_error(f"Schema out of sync: {schema_ref}")
                    if self.auto_update:
                        self._save_cached_schema(schema_ref, live_schema)
                        self.log_update(f"Updated schema: {schema_ref}")
                    all_synced = False
                else:
                    self.log_success(f"Schema in sync: {schema_ref}")

            except Exception as e:
                self.log_error(f"Failed to check schema {schema_ref}: {e}")
                all_synced = False

        return all_synced

    async def check_schema_index(self) -> bool:
        """Check if the schema index is in sync."""
        print("\n🔍 Checking schema index...")

        try:
            # Fetch live index
            live_index = await self._fetch_live_index()

            # Check cached index
            cached_index_path = self.cache_dir / "index.json"
            if cached_index_path.exists():
                with open(cached_index_path) as f:
                    cached_index = json.load(f)

                # Compare versions
                live_version = live_index.get("version", "unknown")
                cached_version = cached_index.get("version", "unknown")

                if live_version != cached_version:
                    self.log_error(f"Index version mismatch: live={live_version}, cached={cached_version}")
                    if self.auto_update:
                        with open(cached_index_path, "w") as f:
                            json.dump(live_index, f, indent=2)
                            f.write("\n")  # Add trailing newline for pre-commit compatibility
                        self.log_update(f"Updated index version: {live_version}")
                    return False
                else:
                    self.log_success(f"Index version in sync: {live_version}")
                    return True
            else:
                self.log_error("Missing cached schema index")
                if self.auto_update:
                    with open(cached_index_path, "w") as f:
                        json.dump(live_index, f, indent=2)
                        f.write("\n")  # Add trailing newline for pre-commit compatibility
                    self.log_update("Downloaded missing index")
                return False

        except Exception as e:
            self.log_error(f"Failed to check schema index: {e}")
            return False

    async def check_all_schemas_in_index(self) -> bool:
        """Check all schemas referenced in the index."""
        print("\n🔍 Checking all schemas from index...")

        try:
            live_index = await self._fetch_live_index()
            all_synced = True
            schemas_checked = 0

            # Extract all schema references from index
            schema_refs = set()

            def extract_refs(obj, path=""):
                if isinstance(obj, dict):
                    if "$ref" in obj:
                        schema_refs.add(obj["$ref"])
                    for key, value in obj.items():
                        extract_refs(value, f"{path}.{key}")
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        extract_refs(item, f"{path}[{i}]")

            extract_refs(live_index)

            # Check each schema reference (only those we actually implement)
            for schema_ref in sorted(schema_refs):
                if not schema_ref.startswith("/schemas/v1/"):
                    continue

                # Skip schemas for endpoints we don't implement
                if schema_ref not in self.required_schemas:
                    continue

                try:
                    live_schema = await self._fetch_live_schema(schema_ref)
                    cached_schema = self._load_cached_schema(schema_ref)

                    if cached_schema is None:
                        # In CI mode, missing schemas are errors (block commits)
                        # In dev mode, they're warnings (allow local experimentation)
                        log_func = self.log_error if self.ci_mode else self.log_warning
                        log_func(f"Missing cached schema: {schema_ref}")
                        if self.auto_update:
                            self._save_cached_schema(schema_ref, live_schema)
                            self.log_update(f"Downloaded missing schema: {schema_ref}")
                        all_synced = False
                    elif not self._schemas_are_equal(live_schema, cached_schema):
                        # In CI mode, schema drift is an error (block commits)
                        # In dev mode, it's a warning (allow local experimentation)
                        log_func = self.log_error if self.ci_mode else self.log_warning
                        log_func(f"Schema differs: {schema_ref}")
                        if self.auto_update:
                            self._save_cached_schema(schema_ref, live_schema)
                            self.log_update(f"Updated schema: {schema_ref}")
                        all_synced = False

                    schemas_checked += 1

                except Exception as e:
                    self.log_warning(f"Failed to check schema {schema_ref}: {e}")

            # Report what we checked vs. total available
            total_refs = len([ref for ref in schema_refs if ref.startswith("/schemas/v1/")])
            skipped_refs = total_refs - schemas_checked

            self.log_success(f"Checked {schemas_checked} implemented endpoint schemas from index")
            if skipped_refs > 0:
                self.log_success(f"Skipped {skipped_refs} schemas for unimplemented endpoints")
            return all_synced

        except Exception as e:
            self.log_error(f"Failed to check schemas from index: {e}")
            return False

    async def _resolve_schema_dependencies(self, schema_refs: set[str]) -> set[str]:
        """Recursively resolve all schema dependencies."""
        resolved_refs = set(schema_refs)
        to_process = list(schema_refs)

        while to_process:
            current_ref = to_process.pop()

            try:
                # Load the schema and find its dependencies
                schema_data = self._load_cached_schema(current_ref)
                if not schema_data:
                    # Try to fetch it live if not cached
                    schema_data = await self._fetch_live_schema(current_ref)

                if schema_data:
                    # Extract all $ref dependencies from this schema
                    def extract_refs(obj):
                        refs = set()
                        if isinstance(obj, dict):
                            if "$ref" in obj:
                                refs.add(obj["$ref"])
                            for value in obj.values():
                                refs.update(extract_refs(value))
                        elif isinstance(obj, list):
                            for item in obj:
                                refs.update(extract_refs(item))
                        return refs

                    dependencies = extract_refs(schema_data)

                    # Add new dependencies to our resolved set and processing queue
                    for dep_ref in dependencies:
                        if dep_ref.startswith("/schemas/v1/") and dep_ref not in resolved_refs:
                            resolved_refs.add(dep_ref)
                            to_process.append(dep_ref)

            except Exception as e:
                self.log_warning(f"Failed to resolve dependencies for {current_ref}: {e}")

        return resolved_refs

    async def check_media_buy_endpoint_coverage(self) -> bool:
        """Check media-buy endpoint implementation coverage."""
        print("\n🔍 Checking media-buy endpoint coverage...")

        try:
            live_index = await self._fetch_live_index()
            media_buy_tasks = live_index.get("schemas", {}).get("media-buy", {}).get("tasks", {})

            # Our implemented media-buy endpoints (based on @mcp.tool functions)
            implemented_endpoints = {
                "get-products",
                "list-creative-formats",
                "create-media-buy",
                "sync-creatives",
                "list-creatives",
                "update-media-buy",
                "get-media-buy-delivery",
                "list-authorized-properties",  # AdCP PR #174 implementation
            }

            # Check coverage
            registry_endpoints = set(media_buy_tasks.keys())
            missing_endpoints = registry_endpoints - implemented_endpoints
            extra_endpoints = implemented_endpoints - registry_endpoints

            # Report results
            implemented_count = len(implemented_endpoints & registry_endpoints)
            total_count = len(registry_endpoints)

            self.log_success(f"Implemented {implemented_count}/{total_count} media-buy endpoints")

            if missing_endpoints:
                self.log_warning(f"Missing media-buy endpoints: {sorted(missing_endpoints)}")
                for endpoint in sorted(missing_endpoints):
                    task_info = media_buy_tasks.get(endpoint, {})
                    description = task_info.get("request", {}).get("description", "No description")
                    self.log_warning(f"  • {endpoint}: {description}")

            if extra_endpoints:
                self.log_warning(f"Implemented endpoints not in registry: {sorted(extra_endpoints)}")

            # Check for media-buy schemas not in current task index (but available on server)
            unlisted_schemas = []
            if self.cache_dir.exists():
                for cached_file in self.cache_dir.glob("_schemas_v1_media-buy_*.json"):
                    filename = cached_file.stem
                    # Extract endpoint name from filename like "_schemas_v1_media-buy_add-creative-assets-request_json"
                    parts = filename.split("_")
                    if len(parts) >= 5:
                        # For "_schemas_v1_media-buy_add-creative-assets-request_json"
                        # Take parts[4:-1] and join, then remove -request/-response suffix
                        endpoint_part = "_".join(parts[4:-1])  # Skip "_schemas_v1_media-buy_" and "_json"
                        endpoint_name = endpoint_part.replace("-request", "").replace("-response", "")
                        if (
                            endpoint_name
                            and endpoint_name not in registry_endpoints
                            and endpoint_name not in implemented_endpoints
                        ):
                            unlisted_schemas.append(endpoint_name)

            if unlisted_schemas:
                unique_endpoints = sorted(set(unlisted_schemas))
                self.log_success(
                    f"Synced {len(unique_endpoints)} additional media-buy schemas not in task index: {unique_endpoints}"
                )
                self.log_success("(These schemas exist on server but aren't listed as official tasks)")

            # Return success if we have reasonable coverage (allow missing non-critical endpoints)
            critical_missing = [ep for ep in missing_endpoints if ep in ["get-products", "create-media-buy"]]
            return len(critical_missing) == 0

        except Exception as e:
            self.log_error(f"Failed to check media-buy endpoint coverage: {e}")
            return False

    async def run_all_checks(self) -> bool:
        """Run all schema sync checks."""
        print("🔍 Running AdCP Schema Sync Checks...\n")
        print("📡 Comparing local cached schemas with live schemas from adcontextprotocol.org\n")

        checks = [
            ("Format IDs Structure Validation", self.validate_format_ids_structure),
            ("Schema Index", self.check_schema_index),
            ("Critical Schemas", self.check_critical_schemas),
            ("All Schemas in Index", self.check_all_schemas_in_index),
            ("Media-Buy Endpoint Coverage", self.check_media_buy_endpoint_coverage),
        ]

        all_passed = True
        for check_name, check_func in checks:
            try:
                passed = await check_func()
                if not passed:
                    all_passed = False
            except Exception as e:
                self.log_error(f"{check_name} check crashed: {e}")
                all_passed = False

        # Summary
        print("\n📊 Schema Sync Check Summary:")
        print(f"   ✅ Checks completed: {len(checks)}")
        print(f"   ❌ Errors: {len(self.errors)}")
        print(f"   ⚠️ Warnings: {len(self.warnings)}")
        print(f"   🔄 Updates applied: {len(self.updates_applied)}")

        if self.errors:
            print("\n❌ ERRORS:")
            for error in self.errors:
                print(f"   • {error}")

        if self.warnings:
            print("\n⚠️ WARNINGS:")
            for warning in self.warnings:
                print(f"   • {warning}")

        if self.updates_applied:
            print("\n🔄 UPDATES APPLIED:")
            for update in self.updates_applied:
                print(f"   • {update}")

        if all_passed:
            print("\n🎉 All schemas are in sync with adcontextprotocol.org!")
        else:
            print("\n💥 Schema sync check failed!")
            if self.ci_mode:
                print("\nTo fix schema sync issues, run:")
                print("   uv run python scripts/check_schema_sync.py --update")
                print("   git add tests/e2e/schemas/")
                print("   git commit -m 'Update AdCP schemas to latest from registry'")

        return all_passed


async def main():
    parser = argparse.ArgumentParser(description="Check AdCP schema sync with official registry")
    parser.add_argument("--ci", action="store_true", help="CI mode (exit 1 on failures)")
    parser.add_argument("--update", action="store_true", help="Auto-update schemas from registry")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")

    args = parser.parse_args()

    async with SchemaSyncChecker(ci_mode=args.ci, auto_update=args.update) as checker:
        try:
            success = await checker.run_all_checks()

            if args.ci and not success:
                sys.exit(1)
            elif not success:
                print("\n💡 Run with --update to auto-update schemas from registry")
                sys.exit(1)

        except KeyboardInterrupt:
            print("\n⚠️ Schema sync check interrupted")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ Schema sync check failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

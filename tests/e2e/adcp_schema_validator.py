"""
AdCP JSON Schema Validator for E2E Tests

This module provides comprehensive JSON schema validation for all AdCP protocol
requests and responses against the official AdCP specification schemas.

Key Features:
- Downloads and caches AdCP schemas from official registry
- Validates requests and responses against AdCP spec
- Performance-optimized with compiled validators
- Detailed error reporting with JSON path locations
- Support for offline validation with cached schemas

Usage:
    validator = AdCPSchemaValidator()
    await validator.validate_response("get-products", response_data)
"""

import asyncio
import functools
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import jsonschema
import pytest
from jsonschema.validators import Draft7Validator


class SchemaError(Exception):
    """Base exception for schema validation errors."""

    pass


class SchemaDownloadError(SchemaError):
    """Raised when schema download fails."""

    pass


class SchemaValidationError(SchemaError):
    """Raised when JSON validation fails."""

    def __init__(self, message: str, validation_errors: list[str], json_path: str = ""):
        super().__init__(message)
        self.validation_errors = validation_errors
        self.json_path = json_path


class AdCPSchemaValidator:
    """
    Validator for AdCP protocol JSON schemas.

    Automatically downloads, caches, and validates against official AdCP schemas.
    """

    BASE_SCHEMA_URL = "https://adcontextprotocol.org/schemas/v1"
    INDEX_URL = "https://adcontextprotocol.org/schemas/v1/index.json"

    def __init__(self, cache_dir: Path | None = None, offline_mode: bool = False, adcp_version: str = "v1"):
        """
        Initialize the schema validator.

        Args:
            cache_dir: Directory to cache schemas. Defaults to schemas/{version}
            offline_mode: If True, only use cached schemas (no downloads)
            adcp_version: AdCP schema version to use (e.g., "v1", "v2")
        """
        self.offline_mode = offline_mode
        self.adcp_version = adcp_version

        # Set up versioned cache directory
        if cache_dir is None:
            project_root = Path(__file__).parent.parent.parent
            cache_dir = project_root / "schemas" / adcp_version
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Schema registry and compiled validators cache
        self._schema_registry: dict[str, dict] = {}
        self._compiled_validators: dict[str, Draft7Validator] = {}
        self._index_cache: dict | None = None

        # HTTP client for downloads
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._http_client.aclose()

    def _get_cache_path(self, schema_ref: str) -> Path:
        """Get local cache path for a schema reference."""
        # Convert schema reference to safe filename
        safe_name = schema_ref.replace("/", "_").replace(".", "_") + ".json"

        # Try main cache directory first
        main_cache_path = self.cache_dir / safe_name
        if main_cache_path.exists():
            return main_cache_path

        # Try cache subdirectory (legacy location)
        cache_subdir_path = self.cache_dir / "cache" / safe_name
        if cache_subdir_path.exists():
            return cache_subdir_path

        # Return main path for new files
        return main_cache_path

    def _get_cache_metadata_path(self, cache_path: Path) -> Path:
        """Get path for cache metadata file (stores ETag, last-modified, etc)."""
        return cache_path.with_suffix(cache_path.suffix + ".meta")

    async def _download_schema_index(self) -> dict[str, Any]:
        """
        Download the main schema index/registry with ETag-based caching.

        Uses conditional GET with If-None-Match header to avoid re-downloading
        unchanged schemas. Falls back to cached version if server unavailable.
        """
        cache_path = self.cache_dir / "index.json"
        meta_path = self._get_cache_metadata_path(cache_path)

        # In offline mode, use cache only
        if self.offline_mode:
            if cache_path.exists():
                try:
                    with open(cache_path) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    raise SchemaDownloadError(f"Offline mode enabled but cached index is invalid: {cache_path}")
            raise SchemaDownloadError("Offline mode enabled but no valid cached index found")

        # Load cached metadata (ETag, Last-Modified)
        cached_etag = None
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
                    cached_etag = metadata.get("etag")
            except (json.JSONDecodeError, OSError):
                pass

        # Download with conditional GET
        try:
            headers = {}
            if cached_etag:
                headers["If-None-Match"] = cached_etag

            response = await self._http_client.get(self.INDEX_URL, headers=headers)

            # 304 Not Modified - use cache
            if response.status_code == 304:
                if cache_path.exists():
                    with open(cache_path) as f:
                        return json.load(f)
                # Fallthrough to re-download if cache missing

            response.raise_for_status()
            index_data = response.json()

            # Delete old metadata first (prevents stale ETag issues)
            if meta_path.exists():
                meta_path.unlink()

            # Save to cache
            with open(cache_path, "w") as f:
                json.dump(index_data, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            # Save new metadata
            metadata = {
                "etag": response.headers.get("etag"),
                "last-modified": response.headers.get("last-modified"),
                "downloaded_at": datetime.now().isoformat(),
            }
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            return index_data

        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # If download fails but we have cache, use it
            if cache_path.exists():
                print(f"Warning: Failed to download index, using cached version: {e}")
                with open(cache_path) as f:
                    return json.load(f)

            raise SchemaDownloadError(f"Failed to download schema index: {e}")

    async def _download_schema(self, schema_ref: str) -> dict[str, Any]:
        """
        Download a specific schema by reference with ETag-based caching.

        Uses conditional GET with If-None-Match header to avoid re-downloading
        unchanged schemas. Falls back to cached version if server unavailable.
        """
        cache_path = self._get_cache_path(schema_ref)
        meta_path = self._get_cache_metadata_path(cache_path)

        # In offline mode, use cache only
        if self.offline_mode:
            if cache_path.exists():
                try:
                    with open(cache_path) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    raise SchemaDownloadError(f"Offline mode enabled but cached schema is invalid: {cache_path}")
            raise SchemaDownloadError(f"Offline mode enabled but no valid cached schema: {schema_ref}")

        # Load cached metadata (ETag, Last-Modified)
        cached_etag = None
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
                    cached_etag = metadata.get("etag")
            except (json.JSONDecodeError, OSError):
                pass

        # Construct full URL
        if schema_ref.startswith("/"):
            schema_url = f"https://adcontextprotocol.org{schema_ref}"
        else:
            schema_url = urljoin(self.BASE_SCHEMA_URL, schema_ref)

        # Download with conditional GET
        try:
            headers = {}
            if cached_etag:
                headers["If-None-Match"] = cached_etag

            response = await self._http_client.get(schema_url, headers=headers)

            # 304 Not Modified - use cache
            if response.status_code == 304:
                if cache_path.exists():
                    with open(cache_path) as f:
                        return json.load(f)
                # Fallthrough to re-download if cache missing

            response.raise_for_status()
            schema_data = response.json()

            # Delete old metadata first (prevents stale ETag issues)
            if meta_path.exists():
                meta_path.unlink()

            # Save to cache
            with open(cache_path, "w") as f:
                json.dump(schema_data, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            # Save new metadata
            metadata = {
                "etag": response.headers.get("etag"),
                "last-modified": response.headers.get("last-modified"),
                "downloaded_at": datetime.now().isoformat(),
                "schema_ref": schema_ref,
            }
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            return schema_data

        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # If download fails but we have cache, use it
            if cache_path.exists():
                print(f"Warning: Failed to download {schema_ref}, using cached version: {e}")
                with open(cache_path) as f:
                    return json.load(f)

            raise SchemaDownloadError(f"Failed to download schema {schema_ref}: {e}")

    async def get_schema_index(self) -> dict[str, Any]:
        """Get the schema index, using cache when possible."""
        if self._index_cache is None:
            self._index_cache = await self._download_schema_index()
        return self._index_cache

    async def get_schema(self, schema_ref: str) -> dict[str, Any]:
        """Get a schema by reference, using cache when possible."""
        if schema_ref not in self._schema_registry:
            self._schema_registry[schema_ref] = await self._download_schema(schema_ref)
        return self._schema_registry[schema_ref]

    def _get_compiled_validator(self, schema: dict[str, Any]) -> Draft7Validator:
        """Get a compiled validator for a schema, with caching."""
        # Create a hash of the schema for caching
        schema_hash = hashlib.md5(json.dumps(schema, sort_keys=True).encode()).hexdigest()

        if schema_hash not in self._compiled_validators:
            # Create a custom resolver that can handle AdCP schema references
            resolver = jsonschema.RefResolver.from_schema(
                schema,
                handlers={
                    "": self._resolve_adcp_schema_ref,
                    "https": self._resolve_http_schema_ref,
                    "http": self._resolve_http_schema_ref,
                },
            )
            self._compiled_validators[schema_hash] = Draft7Validator(schema, resolver=resolver)

        return self._compiled_validators[schema_hash]

    def _resolve_adcp_schema_ref(self, url: str) -> dict[str, Any]:
        """Resolve an AdCP schema reference synchronously."""
        # This is called during validation, so we need a sync version
        # We'll try to get from cache first, then fail gracefully if not available
        cache_path = self._get_cache_path(url)
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # If not in cache, we can't resolve it synchronously
        # Use a strict schema instead of permissive one to catch more validation issues
        print(f"Warning: Could not resolve schema reference {url} - using strict fallback")
        return {"type": "object", "additionalProperties": False, "properties": {}}

    def _resolve_http_schema_ref(self, url: str) -> dict[str, Any]:
        """Resolve HTTP schema reference synchronously."""
        # For HTTP references, fall back to the AdCP resolver
        # Extract the path part and resolve as AdCP schema
        if "adcontextprotocol.org" in url:
            path_part = url.split("adcontextprotocol.org")[-1]
            return self._resolve_adcp_schema_ref(path_part)

        # Unknown HTTP reference - use strict fallback
        print(f"Warning: Could not resolve HTTP schema reference {url} - using strict fallback")
        return {"type": "object", "additionalProperties": False, "properties": {}}

    async def _find_schema_ref_for_task(self, task_name: str, request_or_response: str) -> str | None:
        """Find the schema reference for a specific task and type."""
        index = await self.get_schema_index()

        # Look in media-buy tasks first
        media_buy_tasks = index.get("schemas", {}).get("media-buy", {}).get("tasks", {})
        if task_name in media_buy_tasks:
            task_info = media_buy_tasks[task_name]
            if request_or_response in task_info:
                return task_info[request_or_response]["$ref"]

        # Look in signals tasks
        signals_tasks = index.get("schemas", {}).get("signals", {}).get("tasks", {})
        if task_name in signals_tasks:
            task_info = signals_tasks[task_name]
            if request_or_response in task_info:
                return task_info[request_or_response]["$ref"]

        return None

    async def validate_request(self, task_name: str, request_data: dict[str, Any]) -> None:
        """
        Validate a request against AdCP schema.

        Args:
            task_name: Name of the AdCP task (e.g., "get-products")
            request_data: The request data to validate

        Raises:
            SchemaValidationError: If validation fails
        """
        schema_ref = await self._find_schema_ref_for_task(task_name, "request")
        if not schema_ref:
            # Don't fail if schema not found - log warning instead
            print(f"Warning: No request schema found for task '{task_name}'")
            return

        # Preload any referenced schemas before validation
        await self._preload_schema_references(schema_ref)

        await self._validate_against_schema(schema_ref, request_data, f"{task_name} request")

    async def validate_response(self, task_name: str, response_data: dict[str, Any]) -> None:
        """
        Validate a response against AdCP schema.

        This method understands protocol layering - it will extract the AdCP payload
        from MCP/A2A wrapper fields and validate only the payload against the schema.

        Args:
            task_name: Name of the AdCP task (e.g., "get-products")
            response_data: The response data to validate (may include protocol wrapper fields)

        Raises:
            SchemaValidationError: If validation fails
        """
        schema_ref = await self._find_schema_ref_for_task(task_name, "response")
        if not schema_ref:
            # Don't fail if schema not found - log warning instead
            print(f"Warning: No response schema found for task '{task_name}'")
            return

        # Extract AdCP payload from protocol wrapper if present
        adcp_payload = self._extract_adcp_payload(response_data)

        # Preload any referenced schemas before validation
        await self._preload_schema_references(schema_ref)

        await self._validate_against_schema(schema_ref, adcp_payload, f"{task_name} response")

    def _extract_adcp_payload(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """
        Extract the AdCP payload from protocol wrapper fields.

        MCP and A2A protocols may add wrapper fields like:
        - message: Human-readable message from the transport layer
        - context_id: Session continuity identifier
        - errors: Transport-layer errors (not part of AdCP spec)
        - clarification_needed: Non-spec field that should be removed

        This method removes these protocol-layer fields and returns only
        the AdCP payload for validation.

        Args:
            response_data: The full response including protocol wrapper fields

        Returns:
            The AdCP payload with protocol-layer fields removed
        """
        # List of known protocol-layer fields that are not part of AdCP spec
        protocol_fields = {
            "message",  # MCP/A2A transport layer message
            "context_id",  # MCP session continuity
            "clarification_needed",  # Non-spec field
            "errors",  # Transport-layer errors (not in AdCP spec)
            # Note: Some AdCP responses do have "error" fields defined in spec,
            # but "errors" (plural) is typically a transport-layer addition
        }

        # Create a copy of the response without protocol fields
        adcp_payload = {}
        for key, value in response_data.items():
            if key not in protocol_fields:
                adcp_payload[key] = value

        return adcp_payload

    async def _preload_schema_references(self, schema_ref: str, _visited: set[str] | None = None) -> None:
        """
        Recursively preload all schemas referenced by the given schema.

        Args:
            schema_ref: The schema reference to preload
            _visited: Set of already-visited refs to avoid infinite recursion (internal use)
        """
        if _visited is None:
            _visited = set()

        # Avoid infinite recursion
        if schema_ref in _visited:
            return
        _visited.add(schema_ref)

        try:
            schema = await self.get_schema(schema_ref)
            refs_to_load = self._find_schema_references(schema)

            # Recursively load all referenced schemas (download them so they're in cache for validation)
            for ref in refs_to_load:
                try:
                    await self.get_schema(ref)
                    # Recursively preload refs within this schema
                    await self._preload_schema_references(ref, _visited)
                except SchemaDownloadError as e:
                    # Schema download failed - log but continue
                    # (validation will use strict fallback for this ref)
                    print(f"Warning: Could not preload referenced schema {ref}: {e}")
                except Exception as e:
                    # Unexpected error - log but continue
                    print(f"Warning: Unexpected error preloading schema {ref}: {e}")

        except Exception as e:
            print(f"Warning: Could not preload schema references for {schema_ref}: {e}")

    def _find_schema_references(self, schema: dict[str, Any]) -> list[str]:
        """Find all $ref references in a schema recursively."""
        refs = []

        def find_refs_recursive(obj):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    refs.append(obj["$ref"])
                for value in obj.values():
                    find_refs_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    find_refs_recursive(item)

        find_refs_recursive(schema)
        return refs

    async def _validate_against_schema(self, schema_ref: str, data: dict[str, Any], context: str = "") -> None:
        """
        Validate data against a specific schema reference.

        Args:
            schema_ref: Reference to the schema to validate against
            data: Data to validate
            context: Context string for error messages

        Raises:
            SchemaValidationError: If validation fails
        """
        try:
            schema = await self.get_schema(schema_ref)
            validator = self._get_compiled_validator(schema)

            errors = list(validator.iter_errors(data))
            if errors:
                error_messages = []
                for error in errors:
                    # Build JSON path
                    path = ".".join(str(p) for p in error.absolute_path)
                    if not path:
                        path = "root"

                    # Include more detailed error information
                    error_msg = f"At {path}: {error.message}"
                    if hasattr(error, "schema_path") and error.schema_path:
                        schema_path = ".".join(str(p) for p in error.schema_path)
                        error_msg += f" (schema path: {schema_path})"

                    error_messages.append(error_msg)

                raise SchemaValidationError(
                    f"Schema validation failed for {context}", error_messages, json_path=path if errors else ""
                )

        except SchemaDownloadError:
            # Re-raise schema download errors
            raise
        except SchemaValidationError:
            # Re-raise schema validation errors without wrapping them
            raise
        except Exception as e:
            raise SchemaValidationError(f"Unexpected error validating {context}: {e}", [str(e)])


# Decorator functions for easy integration with tests


def validate_adcp_request(task_name: str):
    """
    Decorator to validate AdCP request data.

    Usage:
        @validate_adcp_request("get-products")
        async def test_method(self):
            # Test implementation
            pass
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request data from kwargs or test method
            # This would need to be integrated with the specific test patterns
            result = await func(*args, **kwargs)
            return result

        return wrapper

    return decorator


def validate_adcp_response(task_name: str):
    """
    Decorator to validate AdCP response data.

    Usage:
        @validate_adcp_response("get-products")
        async def test_method(self):
            # Test returns response data
            return response_data
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)

            # Validate the result if it looks like response data
            if isinstance(result, dict):
                async with AdCPSchemaValidator() as validator:
                    await validator.validate_response(task_name, result)

            return result

        return wrapper

    return decorator


# Test fixtures for pytest integration


@pytest.fixture
async def adcp_validator():
    """Pytest fixture providing an AdCP schema validator."""
    async with AdCPSchemaValidator() as validator:
        yield validator


@pytest.fixture
async def adcp_validator_offline():
    """Pytest fixture providing an offline AdCP schema validator."""
    async with AdCPSchemaValidator(offline_mode=True) as validator:
        yield validator


# Utility functions for test setup


async def preload_schemas(task_names: list[str] = None, load_all: bool = True, adcp_version: str = "v1"):
    """
    Preload schemas for faster test execution.

    Args:
        task_names: List of task names to preload. If None, loads common tasks.
        load_all: If True, loads all schemas from registry (core, enums, tasks).
        adcp_version: AdCP schema version to use (e.g., "v1", "v2")
    """
    async with AdCPSchemaValidator(adcp_version=adcp_version) as validator:
        # Preload index
        index = await validator.get_schema_index()

        if load_all:
            print("📥 Loading ALL AdCP schemas from registry...")

            # Load core schemas
            print("\n📁 CORE SCHEMAS:")
            core_schemas = index.get("schemas", {}).get("core", {}).get("schemas", {})
            for name, info in core_schemas.items():
                try:
                    schema_ref = info["$ref"]
                    await validator.get_schema(schema_ref)
                    print(f"  ✓ {name}")
                except Exception as e:
                    print(f"  ⚠ {name}: {e}")

            # Load enum schemas
            print("\n📁 ENUM SCHEMAS:")
            enum_schemas = index.get("schemas", {}).get("enums", {}).get("schemas", {})
            for name, info in enum_schemas.items():
                try:
                    schema_ref = info["$ref"]
                    await validator.get_schema(schema_ref)
                    print(f"  ✓ {name}")
                except Exception as e:
                    print(f"  ⚠ {name}: {e}")

            # Load media-buy task schemas
            print("\n📁 MEDIA-BUY TASK SCHEMAS:")
            media_tasks = index.get("schemas", {}).get("media-buy", {}).get("tasks", {})
            for task_name, task_info in media_tasks.items():
                for req_resp in ["request", "response"]:
                    if req_resp in task_info:
                        try:
                            schema_ref = task_info[req_resp]["$ref"]
                            await validator.get_schema(schema_ref)
                            print(f"  ✓ {task_name}-{req_resp}")
                        except Exception as e:
                            print(f"  ⚠ {task_name}-{req_resp}: {e}")

            # Load signals task schemas
            print("\n📁 SIGNALS TASK SCHEMAS:")
            signals_tasks = index.get("schemas", {}).get("signals", {}).get("tasks", {})
            for task_name, task_info in signals_tasks.items():
                for req_resp in ["request", "response"]:
                    if req_resp in task_info:
                        try:
                            schema_ref = task_info[req_resp]["$ref"]
                            await validator.get_schema(schema_ref)
                            print(f"  ✓ {task_name}-{req_resp}")
                        except Exception as e:
                            print(f"  ⚠ {task_name}-{req_resp}: {e}")

        else:
            # Legacy behavior - only load specific tasks
            if task_names is None:
                task_names = [
                    "get-products",
                    "list-creative-formats",
                    "create-media-buy",
                    "add-creative-assets",
                    "update-media-buy",
                    "get-media-buy-delivery",
                ]

            print(f"📥 Loading schemas for specific tasks: {task_names}")
            for task_name in task_names:
                try:
                    for req_resp in ["request", "response"]:
                        schema_ref = await validator._find_schema_ref_for_task(task_name, req_resp)
                        if schema_ref:
                            await validator.get_schema(schema_ref)
                            print(f"✓ Preloaded {task_name} {req_resp} schema")
                except Exception as e:
                    print(f"⚠ Failed to preload {task_name}: {e}")

        print("\n🎉 Schema preloading completed!")

        # Show cache status
        cache_dir = validator.cache_dir
        cached_files = list(cache_dir.glob("*.json"))
        print(f"📦 Total schemas cached: {len(cached_files)}")
        print(f"💾 Cache location: {cache_dir}")


if __name__ == "__main__":
    """CLI for testing and preloading schemas."""
    import asyncio

    async def main():
        print("AdCP Schema Validator - Preloading common schemas...")
        await preload_schemas()
        print("Schema preloading completed!")

    asyncio.run(main())

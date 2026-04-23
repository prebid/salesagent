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

import functools
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import pytest
import referencing
import referencing.exceptions
from jsonschema.validators import Draft7Validator
from referencing.jsonschema import DRAFT7

logger = logging.getLogger(__name__)


class SchemaError(Exception):
    """Base exception for schema validation errors."""

    pass


class SchemaDownloadError(SchemaError):
    """Raised when schema download fails."""

    pass


class SchemaResolutionError(SchemaError):
    """Raised when a $ref cannot be resolved from the local cache during validation.

    Distinct from SchemaDownloadError (network failure during download): a
    SchemaResolutionError means the local cache is incomplete and must be
    refreshed. Resolution happens synchronously inside referencing.Registry's
    retrieve callback and cannot re-download, so a missing cache entry is fatal.

    Fix: run `make schemas-refresh`.
    """

    def __init__(self, url: str, message: str | None = None):
        super().__init__(message or f"Could not resolve schema reference: {url}")
        self.url = url


class SchemaValidationError(SchemaError):
    """Raised when JSON validation fails."""

    def __init__(self, message: str, validation_errors: list[str], json_path: str = ""):
        super().__init__(message)
        self.validation_errors = validation_errors
        self.json_path = json_path


def collect_refs(node: object, out: set[str]) -> None:
    """Recursively collect every `$ref` string value found under `node`.

    Shared by the cache-completeness structural guard
    (`tests/unit/test_architecture_adcp_schema_cache_complete.py`) and the
    refresh CLI (`scripts/ops/refresh_adcp_schemas.py`). Both must agree on
    what "transitive closure" means, so the BFS lives here in one place.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.add(v)
            else:
                collect_refs(v, out)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, out)


def cache_filename(schema_ref: str) -> str:
    """Filesystem-safe filename for a schema `$ref`.

    Shared by `AdCPSchemaValidator._get_cache_path`, the cache-completeness
    structural guard, and regression tests — all three must agree on this
    mapping or lookups silently miss.
    """
    return schema_ref.replace("/", "_").replace(".", "_") + ".json"


async def walk_transitive_refs(
    initial: Iterable[str],
    fetch: Callable[[str], Awaitable[dict]],
    local_prefix: str,
) -> tuple[set[str], set[str]]:
    """BFS over the `$ref` graph starting from `initial`.

    Refs that match `local_prefix` and end with `.json` are fetched via
    `fetch(ref)`; the returned schema body is scanned with `collect_refs()`
    and any new refs are enqueued. Refs that don't match the filter are
    collected into the external set and never fetched.

    Args:
        initial: Starting set of refs (e.g. every `$ref` extracted from an
            index body, or the single ref for a root schema).
        fetch: Async callable returning the schema body for a ref. Callers
            plug in `validator.get_schema` (HTTP-backed) or a filesystem
            reader (the cache guard) as needed.
        local_prefix: Refs satisfying `ref.startswith(local_prefix) and
            ref.endswith(".json")` are followed transitively. All other
            refs are recorded as external and skipped.

    Returns:
        `(seen_local, external)` where `seen_local` is every local ref that
        was fetched (and thus transitively walked) and `external` is every
        ref encountered that did not match the prefix filter. Callers decide
        what to do with empty / missing / malformed results — this helper
        never asserts, exits, or raises on an empty start set.
    """
    queue: set[str] = set(initial)
    seen_local: set[str] = set()
    external: set[str] = set()

    while queue:
        ref = queue.pop()
        if ref in seen_local or ref in external:
            continue

        if not (ref.startswith(local_prefix) and ref.endswith(".json")):
            external.add(ref)
            continue

        seen_local.add(ref)
        schema = await fetch(ref)
        nested: set[str] = set()
        collect_refs(schema, nested)
        queue |= nested - seen_local - external

    return seen_local, external


class AdCPSchemaValidator:
    """
    Validator for AdCP protocol JSON schemas.

    Automatically downloads, caches, and validates against official AdCP schemas.
    """

    BASE_SCHEMA_URL = "https://adcontextprotocol.org/schemas/latest"
    INDEX_URL = "https://adcontextprotocol.org/schemas/latest/index.json"

    def __init__(
        self,
        cache_dir: Path | None = None,
        offline_mode: bool = False,
        adcp_version: str = "latest",
    ):
        """
        Initialize the schema validator.

        Validation is always strict: unresolvable $refs and schema-download
        failures raise rather than being silently swallowed. Callers are
        expected to maintain a current schema cache (`make schemas-refresh`).

        Args:
            cache_dir: Directory to cache schemas. Defaults to schemas/{version}
            offline_mode: If True, only use cached schemas (no downloads)
            adcp_version: AdCP schema version to use. Only "latest" is currently
                accepted — `BASE_SCHEMA_URL` / `INDEX_URL` are hardcoded to
                `/schemas/latest/`, so any other value would silently change
                the cache directory without changing what we download. True
                per-version pinning requires parameterizing those constants
                (tracked as a follow-up).

        Raises:
            ValueError: If ``adcp_version`` is not ``"latest"``.
        """
        if adcp_version != "latest":
            raise ValueError(
                f"adcp_version={adcp_version!r} is not supported. BASE_SCHEMA_URL is "
                f"hardcoded to /schemas/latest/; version pinning requires parameterizing "
                f"it (tracked as follow-up)."
            )

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
        safe_name = cache_filename(schema_ref)

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

    async def _download_schema_index(self, allow_stale_cache: bool = True) -> dict[str, Any]:
        """
        Download the main schema index/registry with ETag-based caching.

        Uses conditional GET with If-None-Match header to avoid re-downloading
        unchanged schemas. Falls back to cached version if server unavailable.

        Now includes content hash verification to prevent meta file updates when
        only weak ETags change but content is identical.

        Args:
            allow_stale_cache: If True (default), transient HTTP errors are
                logged and the cached index is returned. Appropriate for e2e
                tests where tolerating network flakes is preferable to failing
                every test on a transient blip. The refresh CLI
                (``scripts/ops/refresh_adcp_schemas.py``) must pass ``False``
                so upstream errors surface instead of silently leaving the
                on-disk cache stale.
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

        # Load cached metadata (ETag, Last-Modified, content hash)
        cached_etag = None
        cached_content_hash = None
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
                    cached_etag = metadata.get("etag")
                    cached_content_hash = metadata.get("content_hash")
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

            # Compute content hash to detect actual changes (weak ETags can change without content changes)
            content_str = json.dumps(index_data, sort_keys=True)
            content_hash = hashlib.sha256(content_str.encode()).hexdigest()

            # Check if content actually changed
            if cached_content_hash and content_hash == cached_content_hash:
                # Content is identical despite new ETag (weak ETag changed, content didn't)
                # Return cached data without updating files to avoid git noise
                if cache_path.exists():
                    with open(cache_path) as f:
                        return json.load(f)
                # If cache missing somehow, fall through to save

            # Content changed (or first download) - update cache and metadata

            # Delete old metadata first (prevents stale ETag issues)
            if meta_path.exists():
                meta_path.unlink()

            # Save to cache
            with open(cache_path, "w") as f:
                json.dump(index_data, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            # Save new metadata with content hash
            metadata = {
                "etag": response.headers.get("etag"),
                "last-modified": response.headers.get("last-modified"),
                "downloaded_at": datetime.now().isoformat(),
                "content_hash": content_hash,
            }
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            return index_data

        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # If download fails but we have cache, use it (default e2e behavior)
            if allow_stale_cache and cache_path.exists():
                logger.warning("Failed to download index, using cached version: %s", e)
                with open(cache_path) as f:
                    return json.load(f)

            if not allow_stale_cache and cache_path.exists():
                raise SchemaDownloadError(
                    f"Network fetch of {self.INDEX_URL} failed ({e}); refusing to fall "
                    f"back to stale cache. Fix upstream or retry when available."
                )

            raise SchemaDownloadError(f"Failed to download schema index: {e}")

    async def _download_schema(self, schema_ref: str, allow_stale_cache: bool = True) -> dict[str, Any]:
        """
        Download a specific schema by reference with ETag-based caching.

        Uses conditional GET with If-None-Match header to avoid re-downloading
        unchanged schemas. Falls back to cached version if server unavailable.

        Now includes content hash verification to prevent meta file updates when
        only weak ETags change but content is identical.

        Args:
            schema_ref: The schema reference (either a relative ref like
                ``core/product.json`` or an absolute path like
                ``/schemas/latest/core/product.json``).
            allow_stale_cache: See ``_download_schema_index``. Default ``True``
                preserves e2e-validator flake tolerance; the refresh CLI passes
                ``False`` so upstream errors surface loudly.
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

        # Load cached metadata (ETag, Last-Modified, content hash)
        cached_etag = None
        cached_content_hash = None
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
                    cached_etag = metadata.get("etag")
                    cached_content_hash = metadata.get("content_hash")
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

            # Compute content hash to detect actual changes (weak ETags can change without content changes)
            content_str = json.dumps(schema_data, sort_keys=True)
            content_hash = hashlib.sha256(content_str.encode()).hexdigest()

            # Check if content actually changed
            if cached_content_hash and content_hash == cached_content_hash:
                # Content is identical despite new ETag (weak ETag changed, content didn't)
                # Return cached data without updating files to avoid git noise
                if cache_path.exists():
                    with open(cache_path) as f:
                        return json.load(f)
                # If cache missing somehow, fall through to save

            # Content changed (or first download) - update cache and metadata

            # Delete old metadata first (prevents stale ETag issues)
            if meta_path.exists():
                meta_path.unlink()

            # Save to cache
            with open(cache_path, "w") as f:
                json.dump(schema_data, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            # Save new metadata with content hash
            metadata = {
                "etag": response.headers.get("etag"),
                "last-modified": response.headers.get("last-modified"),
                "downloaded_at": datetime.now().isoformat(),
                "schema_ref": schema_ref,
                "content_hash": content_hash,
            }
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
                f.write("\n")  # Add trailing newline for pre-commit compatibility

            return schema_data

        except (httpx.HTTPError, json.JSONDecodeError) as e:
            # If download fails but we have cache, use it (default e2e behavior)
            if allow_stale_cache and cache_path.exists():
                logger.warning("Failed to download %s, using cached version: %s", schema_ref, e)
                with open(cache_path) as f:
                    return json.load(f)

            if not allow_stale_cache and cache_path.exists():
                raise SchemaDownloadError(
                    f"Network fetch of {schema_url} failed ({e}); refusing to fall "
                    f"back to stale cache. Fix upstream or retry when available."
                )

            raise SchemaDownloadError(f"Failed to download schema {schema_ref}: {e}")

    async def get_schema_index(self, allow_stale_cache: bool = True) -> dict[str, Any]:
        """Get the schema index, using cache when possible.

        Args:
            allow_stale_cache: Forwarded to ``_download_schema_index`` on a
                cache miss. Default ``True`` preserves existing e2e behavior.
                Pass ``False`` when running the refresh CLI so upstream errors
                surface as ``SchemaDownloadError`` instead of being logged and
                papered over with the on-disk cache.
        """
        if self._index_cache is None:
            self._index_cache = await self._download_schema_index(allow_stale_cache=allow_stale_cache)
        return self._index_cache

    async def get_schema(self, schema_ref: str, allow_stale_cache: bool = True) -> dict[str, Any]:
        """Get a schema by reference, using cache when possible.

        Args:
            schema_ref: The schema reference to fetch.
            allow_stale_cache: Forwarded to ``_download_schema`` on a cache
                miss. Default ``True`` preserves existing e2e behavior; the
                refresh CLI passes ``False`` to surface upstream errors.
        """
        if schema_ref not in self._schema_registry:
            self._schema_registry[schema_ref] = await self._download_schema(
                schema_ref, allow_stale_cache=allow_stale_cache
            )
        return self._schema_registry[schema_ref]

    def _get_compiled_validator(self, schema: dict[str, Any]) -> Draft7Validator:
        """Get a compiled validator for a schema, with caching.

        The Registry is pre-seeded with every preloaded schema so the hot
        validation path never invokes the retrieve callback — this both
        avoids per-validation filesystem reads and ensures missing-ref
        failures surface as ``SchemaResolutionError`` (translated at the
        ``_validate_against_schema`` boundary) rather than being wrapped
        by the ``referencing`` library and misclassified as validation
        failures. See ``.claude/notes/typed-boundaries-principle.md``.
        """
        # Create a hash of the schema for caching
        schema_hash = hashlib.md5(json.dumps(schema, sort_keys=True).encode()).hexdigest()

        if schema_hash not in self._compiled_validators:
            registry = referencing.Registry(retrieve=self._retrieve_for_registry)

            # Seed every preloaded schema by both its ref URI and its $id
            # (when they differ). Belt-and-suspenders: callers look up by
            # whichever string ``$ref`` gave them, and upstream schemas
            # sometimes use absolute $ids that diverge from our cache keys.
            pairs: list[tuple[str, referencing.Resource]] = []
            for ref, body in self._schema_registry.items():
                resource = DRAFT7.create_resource(body)
                pairs.append((ref, resource))
                schema_id = body.get("$id") if isinstance(body, dict) else None
                if schema_id and schema_id != ref:
                    pairs.append((schema_id, resource))
            if pairs:
                registry = registry.with_resources(pairs)

            # Also seed the root schema under its $id (if present and not
            # already covered by the preloaded registry).
            root_id = schema.get("$id", "")
            if root_id and root_id not in self._schema_registry:
                registry = registry.with_resource(root_id, DRAFT7.create_resource(schema))

            self._compiled_validators[schema_hash] = Draft7Validator(schema, registry=registry)

        return self._compiled_validators[schema_hash]

    def _retrieve_for_registry(self, uri: str) -> referencing.Resource:
        """Defensive retrieve callback for ``referencing.Registry``.

        The hot path never hits this — ``_get_compiled_validator`` pre-seeds
        the Registry with every preloaded schema. This callback only fires
        on the cold path (caller skipped ``_preload_schema_references``, or
        the ref wasn't in the transitive closure).

        On a miss we raise ``referencing.exceptions.NoSuchResource`` — the
        library's own sentinel — so the library propagates it as
        ``Unresolvable(ref=uri)`` with ``.ref`` populated. We translate to
        ``SchemaResolutionError`` at the validation boundary per the
        typed-boundaries principle.
        """
        try:
            if "adcontextprotocol.org" in uri:
                resolved = self._resolve_http_schema_ref(uri)
            elif uri.startswith(("http://", "https://")):
                resolved = self._resolve_http_schema_ref(uri)
            else:
                resolved = self._resolve_adcp_schema_ref(uri)
        except SchemaResolutionError as e:
            raise referencing.exceptions.NoSuchResource(ref=uri) from e
        return DRAFT7.create_resource(resolved)

    def _resolve_adcp_schema_ref(self, url: str) -> dict[str, Any]:
        """Resolve an AdCP schema reference synchronously from the local cache.

        Called from the ``_retrieve_for_registry`` callback, which the
        referencing library requires to be synchronous. Unresolvable refs
        raise ``SchemaResolutionError`` — callers must ensure the schema
        cache is current (``make schemas-refresh``).
        """
        cache_path = self._get_cache_path(url)
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                raise SchemaResolutionError(
                    url,
                    f"Cached schema {url} is unreadable ({e}); run `make schemas-refresh`.",
                ) from e

        raise SchemaResolutionError(
            url,
            f"Schema {url} is not in the local cache; run `make schemas-refresh`.",
        )

    def _resolve_http_schema_ref(self, url: str) -> dict[str, Any]:
        """Resolve HTTP schema reference synchronously.

        Delegates adcontextprotocol.org URLs to the AdCP resolver. Other hosts
        raise — we don't cache schemas we don't own.
        """
        if "adcontextprotocol.org" in url:
            path_part = url.split("adcontextprotocol.org")[-1]
            return self._resolve_adcp_schema_ref(path_part)

        raise SchemaResolutionError(url, f"Unknown HTTP schema host: {url}")

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
            # Some AdCP tasks legitimately have no request schema (e.g. notifications).
            # Not strict-gated: a typoed task name is a separate concern, tracked in
            # tests/unit/test_architecture_adcp_schema_cache_complete.py via the index.
            logger.warning("No request schema found for task '%s'", task_name)
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
            # See note in validate_request — same legitimate-skip rationale.
            logger.warning("No response schema found for task '%s'", task_name)
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

    async def _preload_schema_references(self, schema_ref: str) -> None:
        """Preload the transitive closure of schemas referenced by ``schema_ref``.

        Delegates to the shared ``walk_transitive_refs`` BFS so this validator,
        the cache-completeness guard, and the refresh CLI all agree on what
        "transitive closure" means. Intra-document fragment refs
        (``#/$defs/...``) and non-JSON refs are filtered out by the
        ``local_prefix + ".json"`` gate — those resolve natively inside
        ``referencing.Registry`` against the parent schema and don't need
        pre-fetching.

        Populates ``self._schema_registry`` as a side effect of
        ``get_schema`` being used as the fetch callback.
        """
        local_prefix = f"/schemas/{self.adcp_version}/"
        await walk_transitive_refs({schema_ref}, self.get_schema, local_prefix)

    async def _validate_against_schema(self, schema_ref: str, data: dict[str, Any], context: str = "") -> None:
        """
        Validate data against a specific schema reference.

        Args:
            schema_ref: Reference to the schema to validate against
            data: Data to validate
            context: Context string for error messages

        Raises:
            SchemaDownloadError: If a schema could not be downloaded.
            SchemaResolutionError: If a ``$ref`` could not be resolved from
                the local cache (``Unresolvable`` from the referencing library
                is translated here).
            SchemaValidationError: If the data does not conform to the schema.
        """
        schema = await self.get_schema(schema_ref)
        validator = self._get_compiled_validator(schema)

        try:
            errors = list(validator.iter_errors(data))
        except referencing.exceptions.Unresolvable as e:
            # Translate at our boundary: ``referencing`` wraps callback
            # exceptions as ``Unresolvable`` (``_WrappedReferencingError`` is a
            # subclass), with ``.ref`` populated. Surface this as the typed
            # ``SchemaResolutionError`` with the actionable remediation hint
            # rather than letting callers see an opaque library exception.
            raise SchemaResolutionError(
                e.ref,
                f"Schema {e.ref} is not in the local cache; run `make schemas-refresh`.",
            ) from e

        if errors:
            error_messages = []
            path = ""
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

            raise SchemaValidationError(f"Schema validation failed for {context}", error_messages, json_path=path)


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

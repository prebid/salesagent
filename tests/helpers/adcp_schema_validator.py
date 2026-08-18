"""AdCP JSON Schema Validator — pinned to the installed SDK's schemas.

Validates requests and responses against the AdCP schemas shipped with the
pinned ``adcp`` SDK (see docs/adcp-spec-version.md for the current pin and
bump procedure) — never against the live adcontextprotocol.org registry. The
live registry serves "latest", which
drifts ahead of the repo's pin: on 2026-08-01 upstream PR adcp#6133 canonicalized
the live schemas' $ref URIs and broke remote ref resolution here overnight
with zero contract change, and before that #1308 tracked payload-vs-latest
drift. Validating against the pin makes CI deterministic and grades the same
contract production is built against. The SDK↔spec mapping is enforced by
tests/unit/test_adcp_spec_version.py.

Lives in tests/helpers/ (not tests/e2e/) because it's a cross-tier consumer:
tests/unit, tests/integration, and tests/e2e all import it, and importing an
e2e-tier module from unit/integration is backwards layering.

Schema loading and $ref resolution delegate to ``tests.helpers.pinned_schema``
— the single source of truth every pinned-schema consumer in this repo reads
through (also used by tests/unit/test_pydantic_schema_alignment.py and the
integration suite). That module resolves from the SDK's "plain" tree
(``adcp/_schemas/<major.minor>/``), not the ``bundled/`` subset: bundled only
physically ships 8 of the SDK's 16 top-level categories (no ``account/``,
``enums/``, ``governance/``, etc.) — validating a task whose schema lives in
one of the missing categories against bundled would raise "not found" even
though the schema exists and is fully valid. The plain tree is a strict
superset with equivalent (fully-resolvable) $ref behavior, so nothing is
lost by not using bundled.

Usage:
    async with AdCPSchemaValidator() as validator:
        await validator.validate_response("get-products", response_data)
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import jsonschema.exceptions
import referencing.exceptions
from jsonschema.validators import Draft7Validator

from tests.helpers import pinned_schema
from tests.helpers.pinned_schema import PinnedSchemaError

_T = TypeVar("_T")

# Failures of the validation INSTRUMENT rather than of the payload: the schema
# is not valid draft-07, a $ref cannot be resolved or retrieved, or a schema
# file on disk is not JSON. These map to this module's SchemaError, alongside
# the PinnedSchemaError that _wrap_pinned translates. Anything NOT in this
# tuple is a bug in this module and propagates unwrapped — see
# _validate_against_schema.
#
# All three referencing types are listed because they do NOT share a base:
# Unresolvable covers PointerToNowhere/NoSuchAnchor/InvalidAnchor (and
# jsonschema's own _WrappedReferencingError, which subclasses it), but
# Unretrievable and NoSuchResource subclass KeyError instead. Unretrievable is
# the one this repo actually produces at runtime — referencing wraps any
# exception out of a registry retrieve callable, and pinned_schema._retrieve
# raises PinnedSchemaError for a schema outside the pinned tree.
_INSTRUMENT_FAILURES = (
    jsonschema.exceptions.SchemaError,
    referencing.exceptions.Unresolvable,
    referencing.exceptions.Unretrievable,
    referencing.exceptions.NoSuchResource,
    json.JSONDecodeError,
)


class SchemaError(Exception):
    """Base exception for schema validation errors."""


class SchemaValidationError(SchemaError):
    """Raised when JSON validation fails."""

    def __init__(self, message: str, validation_errors: list[str], json_path: str = ""):
        super().__init__(message)
        self.validation_errors = validation_errors
        self.json_path = json_path


class AdCPSchemaValidator:
    """Validator for AdCP protocol JSON schemas, pinned to the installed SDK.

    All schemas load from the SDK package on disk; no network access.
    """

    def __init__(self) -> None:
        self.schema_root = self._wrap_pinned(pinned_schema.schema_root)

        # Compiled-validator cache, keyed by normalized ref.
        self._schema_registry: dict[str, dict[str, Any]] = {}
        self._compiled_validators: dict[str, Draft7Validator] = {}
        self._index_cache: dict[str, Any] | None = None

    async def __aenter__(self):
        """Async context manager entry (kept async for call-site compatibility)."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with open(path) as f:
            return json.load(f)

    def _normalize_ref(self, schema_ref: str) -> str:
        """Normalize via ``pinned_schema.normalize_ref``, in this module's error type.

        The rules live in ``pinned_schema`` so there is exactly one of them;
        this only translates the failure into ``SchemaError``, which is what
        this module's callers branch on.
        """
        return self._wrap_pinned(lambda: pinned_schema.normalize_ref(schema_ref))

    async def get_schema_index(self) -> dict[str, Any]:
        """Get the pinned schema index."""
        if self._index_cache is None:
            self._index_cache = self._load_json(self.schema_root / "index.json")
        return self._index_cache

    def _cached(self, cache: dict[str, _T], schema_ref: str, resolver: Callable[[str], _T]) -> _T:
        """Normalize schema_ref, then resolve (and cache) via resolver — shared by
        every schema/validator lookup that only differs by which cache dict and
        which ``pinned_schema`` resolver function it uses."""
        normalized = self._normalize_ref(schema_ref)
        if normalized not in cache:
            cache[normalized] = self._wrap_pinned(lambda: resolver(normalized))
        return cache[normalized]

    async def get_schema(self, schema_ref: str) -> dict[str, Any]:
        """Get a schema by reference, using cache when possible."""
        return self._cached(self._schema_registry, schema_ref, pinned_schema.load)

    def _get_compiled_validator(self, schema_ref: str) -> Draft7Validator:
        """Get a compiled validator for a schema ref, with caching.

        Takes the ref (not a loaded schema dict): ``pinned_schema.validator_for``
        owns both loading and $ref-registry wiring together, so there is no
        loaded-schema-only entry point to cache on independently.
        """
        return self._cached(self._compiled_validators, schema_ref, pinned_schema.validator_for)

    @staticmethod
    def _wrap_pinned(fn: Callable[[], _T]) -> _T:
        """Call a zero-arg resolver, translating its ``PinnedSchemaError``
        (missing schema, a ref that escapes the schema tree, or a missing SDK
        schema tree) into ``SchemaError`` — the type this module's callers
        branch on to mean "resolution failed", distinct from
        ``SchemaValidationError`` (payload violates the contract)."""
        try:
            return fn()
        except PinnedSchemaError as e:
            raise SchemaError(str(e)) from e

    async def _find_schema_ref_for_task(self, task_name: str, request_or_response: str) -> str | None:
        """Find the schema reference for a specific task and type.

        Searches every section of the pinned index, in the index's own key
        order (NOT sorted — at least one task name, 'list-creative-formats',
        is defined differently in two sections: media-buy's sales-agent-facing
        format_ids+creative_agents list vs. creative's authoritative full
        format definitions. Index order puts media-buy first, so that's the
        match returned; re-sorting the sections would silently flip it.
        """
        index = await self.get_schema_index()

        for section in index.get("schemas", {}).values():
            task_info = section.get("tasks", {}).get(task_name)
            if task_info and request_or_response in task_info:
                return task_info[request_or_response]["$ref"]

        return None

    async def validate_request(self, task_name: str, request_data: dict[str, Any]) -> None:
        """
        Validate a request against the pinned AdCP schema.

        Args:
            task_name: Name of the AdCP task (e.g., "get-products")
            request_data: The request data to validate

        Raises:
            SchemaValidationError: If validation fails
        """
        schema_ref = await self._find_schema_ref_for_task(task_name, "request")
        if not schema_ref:
            raise SchemaError(f"No request schema found for task {task_name!r} in the pinned index")

        await self._validate_against_schema(schema_ref, request_data, f"{task_name} request")

    async def validate_response(self, task_name: str, response_data: dict[str, Any]) -> None:
        """
        Validate a response against the pinned AdCP schema.

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
            raise SchemaError(f"No response schema found for task {task_name!r} in the pinned index")

        # Extract AdCP payload from protocol wrapper if present
        adcp_payload = self._extract_adcp_payload(response_data)

        await self._validate_against_schema(schema_ref, adcp_payload, f"{task_name} response")

    def _extract_adcp_payload(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """
        Extract the AdCP payload from protocol wrapper fields.

        The pinned schema's response envelope is an allOf of the version
        fields plus a "Protocol Envelope" arm (e.g.
        get-products-response.json allOf[1]) that spec-defines message and
        context_id — both are populated by the protocol layer, not the
        Pydantic response model, but they ARE modeled and typed on the wire
        envelope the buyer actually receives, so they must be graded, not
        exempted. "errors" is a spec-defined top-level property on several
        response schemas too (and required on some "failed"/partial-failure
        oneOf arms) — stripping it previously let a payload that should fail
        validation (e.g. status="failed" with no errors array) pass
        silently. Only "clarification_needed" has no basis in the pinned
        schemas and stays stripped.

        Args:
            response_data: The full response including protocol wrapper fields

        Returns:
            The AdCP payload with genuinely non-spec fields removed
        """
        # The only strip-set entry with no basis in the pinned schemas.
        protocol_fields = {
            "clarification_needed",
        }

        # Create a copy of the response without protocol fields
        adcp_payload = {}
        for key, value in response_data.items():
            if key not in protocol_fields:
                adcp_payload[key] = value

        return adcp_payload

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
            validator = self._get_compiled_validator(schema_ref)

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
                    if error.schema_path:
                        schema_path = ".".join(str(p) for p in error.schema_path)
                        error_msg += f" (schema path: {schema_path})"

                    error_messages.append(error_msg)

                raise SchemaValidationError(
                    f"Schema validation failed for {context}", error_messages, json_path=path if errors else ""
                )

        except SchemaValidationError:
            # Re-raise schema validation errors without wrapping them
            raise
        except SchemaError:
            # Schema-RESOLUTION failures (bad ref, missing file) must not be
            # wrapped as SchemaValidationError: callers branch on that type to
            # mean "the payload violates the contract". Subclass arm above,
            # base-class arm here — order matters.
            raise
        except _INSTRUMENT_FAILURES as e:
            # The INSTRUMENT is broken, not the payload: the schema itself is
            # not valid draft-07, a $ref cannot be retrieved, or a schema file
            # on disk is corrupt. Same class as the AssertionError that
            # _resolve_pinned already maps here, so it gets the same type.
            #
            # There is deliberately no `except Exception` arm after this one.
            # A bug in this validator (an AttributeError, a TypeError) must
            # propagate unwrapped: relabelling it SchemaValidationError tells
            # the reader "your payload violates the AdCP contract" and sends
            # them hunting a spec violation that does not exist — the exact
            # confusion #1843 exists to eliminate.
            raise SchemaError(f"Schema instrument failure validating {context}: {e}") from e

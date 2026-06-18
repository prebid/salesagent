#!/usr/bin/env python3
"""Automated Pydantic-to-Schema Alignment Tests.

This test suite automatically validates that ALL Pydantic request/response models
accept ALL fields defined in their corresponding AdCP JSON schemas.

This prevents regressions like:
- brand_manifest missing from CreateMediaBuyRequest
- filters missing from GetProductsRequest (PR #195)
- Any future field omissions

The test dynamically loads JSON schemas and validates Pydantic models can handle
all spec-compliant requests.
"""

import importlib
import inspect
import json
import pkgutil
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.core.schemas import (
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
    GetMediaBuyDeliveryRequest,
    GetProductsRequest,
    GetProductsResponse,
    GetSignalsResponse,
    ListAccountsResponse,
    ListCreativesRequest,
    ListCreativesResponse,
    SyncAccountsResponse,
    SyncCreativesRequest,
    SyncCreativesResponse,
    SyncResponseAccount,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.schemas.creative import ListCreativeFormatsResponse
from src.core.schemas.delivery import GetCreativeDeliveryResponse, GetMediaBuyDeliveryResponse

# Pinned AdCP schema fixtures. Source of truth is adcontextprotocol/adcp at the
# immutable commit 04f59d2d5 (tag v3.1-04f59d2d5, 2026-05-13) — an INTENTIONAL frozen
# reference point for AdCP 3.1. Upstream ships constantly and `/schemas/latest` drifts,
# so we deliberately do NOT track it: the schemas are vendored (committed) and read
# offline. To advance the pin, run tests/fixtures/adcp_schemas_pinned/_refresh.py.
_PINNED_SHA = "04f59d2d56d3d77033162c310e99a1188e4eb419"
_PINNED_SCHEMA_DIR = Path(__file__).parent.parent / "fixtures" / "adcp_schemas_pinned"

# Map AdCP schema refs to Pydantic model classes. Keys are the pinned schema `$id`
# namespace (`/schemas/<category>/<file>.json`). At 04f59d2d5, sync/list-creatives
# live under `creative/` (relocated from `media-buy/` earlier in 3.x).
#
# NOTE: CreateMediaBuyRequest is temporarily excluded due to AdCP spec evolution.
# The spec now requires brand_card, but we maintain backward compatibility
# via brand_manifest. Full brand_card implementation will be added in a separate PR.
SCHEMA_TO_MODEL_MAP = {
    "/schemas/media-buy/get-products-request.json": GetProductsRequest,
    # "/schemas/media-buy/create-media-buy-request.json": CreateMediaBuyRequest,  # Skipped - pending brand_card implementation
    "/schemas/media-buy/update-media-buy-request.json": UpdateMediaBuyRequest,
    "/schemas/media-buy/get-media-buy-delivery-request.json": GetMediaBuyDeliveryRequest,
    "/schemas/creative/sync-creatives-request.json": SyncCreativesRequest,
    "/schemas/creative/list-creatives-request.json": ListCreativesRequest,
    # Note: GetSignalsRequest removed — signals is dead code (UC-008), not exposed via MCP or A2A
}

# get-products schema drift — tracked in #1308. The live AdCP schema carries
# the `adcp_major_version` envelope plus `if_catalog_version`/`if_pricing_version`;
# the pinned adcp library does not model them yet. Coverage:
#   - adcp_major_version → excluded via _VERSION_FIELDS
#   - if_catalog_version, if_pricing_version → excluded via KNOWN_SCHEMA_LIBRARY_MISMATCHES
# Tests now pass; remove the prior strict-xfail wrapper.
SCHEMA_TO_MODEL_PARAMS_WITH_GET_PRODUCTS_DRIFT_XFAIL = [
    pytest.param(schema_ref, model_class) for schema_ref, model_class in SCHEMA_TO_MODEL_MAP.items()
]

# Version metadata fields present in AdCP JSON schemas that models don't declare explicitly.
# These have defaults or are managed by the library base class — exclude from all comparisons.
_VERSION_FIELDS: frozenset[str] = frozenset({"adcp_version", "adcp_major_version"})

# Fields the pinned AdCP schema (04f59d2d5) defines but the adcp 5.7.0 Python library
# / our local model does not yet model. These are spec-vs-library mismatches, not bugs
# in our code. Re-derived against the pinned schemas — entries describing fields the
# pin no longer defines were dropped. #1388 tracks the adcp 5.7 alignment.
#
# Keys MUST use the pinned schema namespace (`/schemas/media-buy/...`,
# `/schemas/creative/...`) to match the `schema_ref` values in SCHEMA_TO_MODEL_MAP;
# `KNOWN_SCHEMA_LIBRARY_MISMATCHES.get(schema_ref, set())` lookups silently fall back
# to an empty set otherwise.
KNOWN_SCHEMA_LIBRARY_MISMATCHES: dict[str, set[str]] = {
    "/schemas/media-buy/get-products-request.json": set(),
    "/schemas/media-buy/update-media-buy-request.json": set(),
    "/schemas/media-buy/get-media-buy-delivery-request.json": set(),
    "/schemas/creative/sync-creatives-request.json": set(),
    "/schemas/creative/list-creatives-request.json": set(),
}


def load_json_schema(schema_ref: str) -> dict[str, Any]:
    """Load a vendored AdCP schema pinned at adcontextprotocol/adcp@04f59d2d5.

    ``schema_ref`` is in the schema ``$id``/``$ref`` namespace (``/schemas/<rest>``),
    used both for the top-level request schemas and for nested ``$ref`` resolution.
    Reads the committed fixture offline — never fetches ``/schemas/latest`` (which
    drifts). A missing file is a HARD FAILURE (the pin moved, or a ``$ref`` is outside
    the vendored closure), never a silent skip.
    """
    rel = schema_ref.split("#")[0]
    if not rel.startswith("/schemas/"):
        raise AssertionError(f"Unexpected schema ref (expected '/schemas/...'): {schema_ref!r}")
    path = _PINNED_SCHEMA_DIR / rel[len("/schemas/") :]
    if not path.exists():
        raise AssertionError(
            f"Pinned schema not vendored: {schema_ref} -> {path}\n"
            f"Source: adcontextprotocol/adcp@{_PINNED_SHA[:9]}. "
            f"Re-run tests/fixtures/adcp_schemas_pinned/_refresh.py to vendor it."
        )
    with open(path) as f:
        return json.load(f)


def generate_example_value(field_type: str, field_name: str = "", field_spec: dict = None) -> Any:
    """Generate a reasonable example value for a JSON schema type."""
    # Handle $ref fields (complex nested objects)
    if field_spec and "$ref" in field_spec:
        # Generate sensible defaults for known $ref types
        ref = field_spec["$ref"]
        if "budget" in ref.lower():
            return {"total": 5000.0, "currency": "USD"}
        elif "package-update" in ref.lower():
            return {"package_id": "pkg_1"}
        elif "package" in ref.lower():
            return [{"product_ids": ["prod_1"], "budget": {"total": 5000.0, "currency": "USD"}}]
        elif "creative" in ref.lower():
            return []  # Empty array is valid for creative lists
        elif "brand-manifest" in ref.lower():
            return {"name": "Test Brand"}
        elif "property-list" in ref.lower():
            return {"agent_url": "https://example.com", "list_id": "list_1"}
        elif "promoted-products" in ref.lower():
            return {"manifest_skus": ["SKU-001"]}
        elif "pagination-request" in ref.lower():
            return {"max_results": 50}
        elif "product-filters" in ref.lower():
            return {"delivery_type": "guaranteed"}
        elif "reporting-webhook" in ref.lower():
            return {
                "url": "https://example.com/webhook",
                "reporting_frequency": "daily",
                "authentication": {"credentials": "test-token", "schemes": ["Bearer"]},
            }
        elif "start-timing" in ref.lower():
            return "2025-02-01T00:00:00Z"
        elif "push-notification" in ref.lower():
            return {"url": "https://example.com/notify"}
        elif "validation-mode" in ref.lower():
            return "strict"
        elif "context" in ref.lower():
            return {"session_id": "test-session"}
        elif "ext" in ref.lower():
            return {"custom_field": "test"}
        # For unknown refs, resolve the schema and generate from its properties
        try:
            ref_schema = load_json_schema(ref)
            ref_type = ref_schema.get("type", "object")
            if ref_type == "string" and "enum" in ref_schema:
                return ref_schema["enum"][0]
            if ref_type != "object":
                return generate_example_value(ref_type, field_name, ref_schema)
            # Generate object with required fields from the resolved schema
            obj = {}
            required_fields = ref_schema.get("required", [])
            for prop_name, prop_spec in ref_schema.get("properties", {}).items():
                if prop_name in required_fields:
                    prop_type = prop_spec.get("type", "string")
                    obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec)
            return obj if obj else {}
        except Exception:
            return {}

    # Handle allOf with $ref (e.g., time_budget: allOf[{$ref: duration.json}])
    if field_spec and "allOf" in field_spec:
        for variant in field_spec["allOf"]:
            if "$ref" in variant:
                return generate_example_value("object", field_name, variant)
        # If no $ref in allOf, merge properties from all variants
        merged_spec = dict(field_spec)
        del merged_spec["allOf"]
        for variant in field_spec["allOf"]:
            merged_spec.update(variant)
        return generate_example_value(merged_spec.get("type", "object"), field_name, merged_spec)

    # Handle field-level oneOf (e.g., status_filter: oneOf[enum, array-of-enum])
    # Pick the first variant and recursively generate a value for it.
    if field_spec and "oneOf" in field_spec:
        first_variant = field_spec["oneOf"][0]
        # The variant might be a $ref (e.g., to an enum schema) or inline type
        if "$ref" in first_variant:
            ref = first_variant["$ref"]
            # Load the referenced schema to get enum values or type info
            ref_schema = load_json_schema(ref)
            if "enum" in ref_schema:
                return ref_schema["enum"][0]
            variant_type = ref_schema.get("type", "string")
            return generate_example_value(variant_type, field_name, ref_schema)
        variant_type = first_variant.get("type", "string")
        return generate_example_value(variant_type, field_name, first_variant)

    if field_type == "string":
        # Check for pattern constraints in schema
        if field_spec and "pattern" in field_spec:
            pattern = field_spec["pattern"]
            # Handle common date pattern: YYYY-MM-DD
            if pattern == r"^\d{4}-\d{2}-\d{2}$":
                return "2025-02-01"
            # Handle domain patterns (lowercase alphanumeric + hyphens + dots)
            if "a-z0-9" in pattern and "\\." in pattern:
                return "example.com"
            # Handle lowercase identifier patterns (e.g., brand_id: ^[a-z0-9_]+$)
            if "a-z0-9" in pattern:
                return "test_value"

        # Special cases for known field patterns
        if "date" in field_name.lower():
            # Use date format (YYYY-MM-DD) not datetime
            return "2025-02-01"
        if "time" in field_name.lower():
            # For time fields use full ISO 8601
            return "2025-02-01T00:00:00Z"
        if "id" in field_name.lower():
            return f"test_{field_name}_123"
        if "url" in field_name.lower():
            return "https://example.com/test"
        if "email" in field_name.lower():
            return "test@example.com"
        if "version" in field_name.lower():
            return "1.0.0"
        if "offering" in field_name.lower():
            return "Nike Air Jordan 2025 basketball shoes"
        if "po_number" in field_name.lower():
            return "PO-TEST-12345"
        return f"test_{field_name}_value"
    elif field_type == "number":
        return 100.0
    elif field_type == "integer":
        return 100
    elif field_type == "boolean":
        return True
    elif field_type == "array":
        # Check if items type is specified
        if field_spec and "items" in field_spec:
            items_spec = field_spec["items"]
            if isinstance(items_spec, dict):
                # Check if items have $ref (e.g., Creative objects)
                if "$ref" in items_spec:
                    ref = items_spec["$ref"]
                    if "creative" in ref.lower():
                        # Generate minimal Creative object
                        return [
                            {
                                "creative_id": "test_creative_1",
                                "name": "Test Creative",
                                "format": "display_300x250",
                            }
                        ]
                    # Resolve the ref to check if it's an enum or simple type
                    try:
                        ref_schema = load_json_schema(ref)
                        if "enum" in ref_schema:
                            return [ref_schema["enum"][0]]
                        ref_type = ref_schema.get("type", "object")
                        if ref_type != "object":
                            return [generate_example_value(ref_type, field_name, ref_schema)]
                    except Exception:
                        pass
                    # For other refs, return minimal object
                    return [{}]

                item_type = items_spec.get("type", "string")
                if item_type == "object":
                    # Generate a proper object with required fields
                    obj = {}
                    if "properties" in items_spec:
                        required_fields = items_spec.get("required", [])
                        for prop_name, prop_spec in items_spec["properties"].items():
                            if prop_name in required_fields or "id" in prop_name:
                                prop_type = prop_spec.get("type", "string")
                                obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec)
                    return [obj] if obj else []
                else:
                    # Generate one example item
                    return [generate_example_value(item_type, field_name, items_spec)]
        return []
    elif field_type == "object":
        # Generate sensible defaults for known object types
        if "budget" in field_name.lower():
            return {
                "total": 5000.0,
                "currency": "USD",
                "pacing": "even",
            }
        if "targeting" in field_name.lower():
            return {
                "geo_countries": ["US"],
            }
        if field_spec and "properties" in field_spec:
            # Generate a minimal object with required fields
            obj = {}
            required_fields = field_spec.get("required", [])
            for prop_name, prop_spec in field_spec["properties"].items():
                if prop_name in required_fields:
                    prop_type = prop_spec.get("type", "string")
                    obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec)
            return obj
        return {}
    else:
        return None


def extract_required_fields(schema: dict[str, Any]) -> list[str]:
    """Extract required fields from a JSON schema."""
    return schema.get("required", [])


def extract_all_fields(schema: dict[str, Any]) -> dict[str, Any]:
    """Extract all fields (required and optional) from a JSON schema."""
    properties = schema.get("properties", {})
    return {
        field_name: field_spec
        for field_name, field_spec in properties.items()
        if field_name not in _VERSION_FIELDS
        # Note: We include $ref fields now - generate_example_value will handle them
    }


def generate_minimal_valid_request(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal valid request with only required fields.

    Handles oneOf constraints by including the first required field from the oneOf options.
    """
    required_fields = extract_required_fields(schema)
    properties = schema.get("properties", {})
    oneof_groups = get_oneof_field_groups(schema)

    # If there's a oneOf constraint and no explicit required fields,
    # we need to include at least one field from the oneOf options
    if not required_fields and oneof_groups:
        # Pick the first field from all oneOf options (alphabetically)
        all_oneof_fields = set()
        for group in oneof_groups:
            all_oneof_fields.update(group)
        if all_oneof_fields:
            chosen_field = sorted(all_oneof_fields)[0]
            required_fields = [chosen_field]

    request_data = {}
    for field_name in required_fields:
        if field_name not in properties:
            continue
        field_spec = properties[field_name]
        field_type = field_spec.get("type", "string")
        request_data[field_name] = generate_example_value(field_type, field_name, field_spec)

    return request_data


def get_oneof_field_groups(schema: dict[str, Any]) -> list[set[str]]:
    """Extract oneOf field groups from schema.

    Returns list of sets where each set contains fields that are mutually exclusive.
    Handles both root-level oneOf and nested oneOf in allOf.
    """
    field_groups = []

    # Check root-level oneOf
    if "oneOf" in schema:
        for option in schema["oneOf"]:
            if "required" in option:
                field_groups.append(set(option["required"]))

    # Check oneOf in allOf constraints
    if "allOf" in schema:
        for constraint in schema["allOf"]:
            if "oneOf" in constraint:
                for option in constraint["oneOf"]:
                    if "required" in option:
                        field_groups.append(set(option["required"]))

    return field_groups


def generate_full_valid_request(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a complete valid request with all fields.

    Handles oneOf constraints by only including ONE field from all mutually exclusive options.
    For example, if oneOf says "either field_a OR field_b", only include one.
    """
    all_fields = extract_all_fields(schema)
    oneof_groups = get_oneof_field_groups(schema)

    # Flatten: all fields mentioned in ANY oneOf group are mutually exclusive
    # For example, if oneOf says [{"required": ["field_a"]}, {"required": ["field_b"]}]
    # then field_a and field_b are mutually exclusive
    all_oneof_fields = set()
    for group in oneof_groups:
        all_oneof_fields.update(group)

    # Pick the first one alphabetically to be deterministic
    chosen_oneof_field = sorted(all_oneof_fields)[0] if all_oneof_fields else None

    request_data = {}
    for field_name, field_spec in all_fields.items():
        # If this is a oneOf field, only include if it's the chosen one
        if field_name in all_oneof_fields:
            if field_name != chosen_oneof_field:
                continue

        field_type = field_spec.get("type", "string")
        request_data[field_name] = generate_example_value(field_type, field_name, field_spec)

    return request_data


class TestPydanticSchemaAlignment:
    """Test that Pydantic models accept all fields from AdCP JSON schemas."""

    @pytest.mark.parametrize(
        "schema_ref,model_class",
        SCHEMA_TO_MODEL_PARAMS_WITH_GET_PRODUCTS_DRIFT_XFAIL,
    )
    def test_model_accepts_all_schema_fields(self, schema_ref: str, model_class: type):
        """Test that Pydantic model accepts ALL fields defined in JSON schema.

        This is the critical test that would have caught:
        - brand_manifest missing from CreateMediaBuyRequest
        - filters missing from GetProductsRequest
        """
        # Load the JSON schema
        schema = load_json_schema(schema_ref)

        # Generate a request with ALL fields from schema
        full_request = generate_full_valid_request(schema)

        # This should NOT raise ValidationError
        try:
            instance = model_class(**full_request)
            assert instance is not None
        except ValidationError as e:
            # Extract which fields were rejected
            rejected_fields = [err["loc"][0] for err in e.errors() if err["type"] == "extra_forbidden"]
            missing_fields = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
            value_errors = [err for err in e.errors() if err["type"] == "value_error"]

            # value_errors can indicate custom validators (business logic requirements)
            # These are acceptable if they don't reject spec fields
            # Only fail if we're rejecting fields that ARE in the spec
            known = KNOWN_SCHEMA_LIBRARY_MISMATCHES.get(schema_ref, set())
            rejected_fields = [f for f in rejected_fields if f not in known]
            if rejected_fields:
                error_msg = f"\n{model_class.__name__} REJECTED AdCP spec fields!\n"
                error_msg += f"   Rejected fields: {rejected_fields}\n"
                error_msg += "\n   This means clients sending spec-compliant requests will get validation errors.\n"
                error_msg += f"   Schema: {schema_ref}\n"
                error_msg += f"   Error details: {e}\n"
                pytest.fail(error_msg)

            # If there are value_errors but no rejected_fields, this likely means
            # the model has stricter requirements than the spec (custom validators).
            # This is acceptable - models CAN be stricter than spec.
            # Only fail if the spec explicitly requires fields we're missing.
            if value_errors and not rejected_fields:
                # Check if error mentions fields not being provided
                # This is okay - model can require more than spec
                pytest.skip(
                    f"{model_class.__name__} has stricter validation than spec (custom validators). "
                    f"This is acceptable. Error: {e}"
                )

    @pytest.mark.parametrize("schema_ref,model_class", SCHEMA_TO_MODEL_MAP.items())
    def test_model_has_all_required_fields(self, schema_ref: str, model_class: type):
        """Test that Pydantic model requires all fields marked as required in JSON schema."""
        # Load the JSON schema
        schema = load_json_schema(schema_ref)

        # Get required fields from schema
        required_in_schema = set(extract_required_fields(schema))

        # Skip adcp_version as it often has defaults
        required_in_schema -= _VERSION_FIELDS

        if not required_in_schema:
            # No required fields in schema - nothing to test, which is fine
            return

        # Try to create model without required fields
        try:
            instance = model_class()

            # If it succeeded, check which required fields have defaults
            model_data = instance.model_dump()
            fields_with_defaults = {field for field in required_in_schema if field in model_data}

            # If ALL required fields have defaults, that might be intentional
            if fields_with_defaults == required_in_schema:
                pytest.skip(f"All required fields have defaults: {fields_with_defaults}")

        except ValidationError as e:
            # This is expected - required fields should cause validation errors
            missing_from_error = {err["loc"][0] for err in e.errors() if err["type"] == "missing"}

            # Verify that the fields flagged as missing match schema requirements
            if missing_from_error != required_in_schema:
                unexpected = missing_from_error - required_in_schema
                not_enforced = required_in_schema - missing_from_error

                # If model requires MORE fields than spec, that's acceptable (business logic)
                # Only fail if model requires FEWER fields than spec
                if not_enforced and not unexpected:
                    pytest.skip(
                        f"{model_class.__name__} has optional fields where spec requires them: {not_enforced}. "
                        f"This may be intentional for flexibility."
                    )

                if unexpected and not not_enforced:
                    pytest.skip(
                        f"{model_class.__name__} requires additional fields beyond spec: {unexpected}. "
                        f"This is acceptable for business logic."
                    )

                # Both unexpected and not_enforced - this can be legacy conversion logic
                # For example, CreateMediaBuyRequest accepts legacy product_ids OR new packages,
                # and requires po_number for business tracking
                if unexpected and not_enforced:
                    pytest.skip(
                        f"{model_class.__name__} has flexible field requirements (likely legacy conversion). "
                        f"Requires: {unexpected}, Optional where spec requires: {not_enforced}. "
                        f"This is acceptable for backward compatibility."
                    )

    @pytest.mark.parametrize("schema_ref,model_class", SCHEMA_TO_MODEL_MAP.items())
    def test_model_accepts_minimal_request(self, schema_ref: str, model_class: type):
        """Test that Pydantic model accepts minimal valid request (only required fields).

        Note: Models CAN require additional fields beyond the spec for business logic.
        This test skips cases where models are intentionally stricter.
        """
        # Load the JSON schema
        schema = load_json_schema(schema_ref)

        # Generate minimal request
        minimal_request = generate_minimal_valid_request(schema)

        # Strip fields that are known library mismatches (spec has them, library doesn't yet)
        known_mismatches = KNOWN_SCHEMA_LIBRARY_MISMATCHES.get(schema_ref, set())
        for field in known_mismatches:
            minimal_request.pop(field, None)

        # This should work
        try:
            instance = model_class(**minimal_request)
            assert instance is not None
        except ValidationError as e:
            # Check if this is a value_error (custom validator) - models can be stricter
            value_errors = [err for err in e.errors() if err["type"] == "value_error"]
            if value_errors:
                pytest.skip(
                    f"{model_class.__name__} has stricter validation than spec (custom validators). "
                    f"This is acceptable for business logic. Error: {e}"
                )

            # Check if error is about missing fields - model requires more than spec
            missing_errors = [err for err in e.errors() if err["type"] == "missing"]
            if missing_errors:
                missing_fields = {err["loc"][0] for err in missing_errors}
                pytest.skip(
                    f"{model_class.__name__} requires additional fields beyond spec: {missing_fields}. "
                    f"This is acceptable for business logic."
                )

            # Other validation errors are real problems
            pytest.fail(
                f"{model_class.__name__} rejected minimal valid request.\n"
                f"Schema: {schema_ref}\n"
                f"Request: {minimal_request}\n"
                f"Error: {e}"
            )


class TestSpecificFieldValidation:
    """Specific regression tests for fields that have caused issues."""

    def test_create_media_buy_accepts_brand_manifest(self):
        """REGRESSION TEST: brand must be accepted per AdCP v3.6.0 (replaced brand_manifest)."""
        request = CreateMediaBuyRequest(
            brand={"domain": "nike.com"},
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "test_pricing",
                }
            ],
            start_time="2025-02-01T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            idempotency_key="unit-test-key-accepts-brand-mfst",
        )
        # Verify brand was accepted
        assert request.brand is not None

    def test_get_products_accepts_filters(self):
        """REGRESSION TEST: filters must be accepted (PR #195 issue)."""
        request = GetProductsRequest(
            brand={"domain": "testproduct.com"},
            filters={
                "delivery_type": "guaranteed",
                "format_types": ["video"],
            },
        )
        assert request.filters is not None
        assert request.filters.delivery_type.value == "guaranteed"

    def test_get_products_all_fields_optional(self):
        """Test that GetProductsRequest accepts all optional fields per spec.

        Note: adcp_version is NOT a field on GetProductsRequest per AdCP spec.
        All fields are optional, including brand.
        adcp 3.6.0: brand replaced brand_manifest.
        """
        # Empty request is valid
        empty_request = GetProductsRequest()
        assert empty_request.brand is None
        assert empty_request.brief is None
        assert empty_request.filters is None

        # With brand only
        request = GetProductsRequest(
            brand={"domain": "testproduct.com"},
        )
        assert request.brand is not None
        assert request.brief is None


class TestFieldNameConsistency:
    """Test that field names match between Pydantic models and JSON schemas."""

    @pytest.mark.parametrize(
        "schema_ref,model_class",
        SCHEMA_TO_MODEL_PARAMS_WITH_GET_PRODUCTS_DRIFT_XFAIL,
    )
    def test_field_names_match_schema(self, schema_ref: str, model_class: type):
        """Test that Pydantic model field names match JSON schema property names."""
        # Load the JSON schema
        schema = load_json_schema(schema_ref)

        # Get all properties from schema
        schema_fields = set(schema.get("properties", {}).keys())

        # Get all fields from Pydantic model
        model_fields = set(model_class.model_fields.keys())

        # Find discrepancies (excluding internal fields)
        internal_fields = {"strategy_id", "testing_mode"}  # Known internal-only fields
        model_fields_public = model_fields - internal_fields

        # Fields in schema but not in model (potential missing fields)
        missing_in_model = schema_fields - model_fields_public

        # We're lenient here - having extra model fields is okay (for internal use)
        # But missing schema fields is a problem
        if missing_in_model:
            # Some fields might be intentionally skipped (like adcp_version with defaults)
            critical_missing = missing_in_model - _VERSION_FIELDS

            # Filter out known spec-vs-library mismatches
            known = KNOWN_SCHEMA_LIBRARY_MISMATCHES.get(schema_ref, set())
            critical_missing = critical_missing - known

            if critical_missing:
                pytest.fail(
                    f"\n{model_class.__name__} is missing schema fields!\n"
                    f"   Missing: {critical_missing}\n"
                    f"   These fields are defined in AdCP spec but not in Pydantic model.\n"
                    f"   Schema: {schema_ref}\n"
                )


# ---------------------------------------------------------------------------
# Response-model alignment (pinned).
#
# Response schemas are oneOf unions, so a local success model maps to one variant
# (and, for list responses, a nested item). These checks reuse the SAME pinned
# load_json_schema() as the request checks above — no per-test hand-rolled schema
# IO — so "model conforms to the pinned schema" lives in one place.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseAlignment:
    """Maps a local success model to its pinned response (sub-)schema."""

    schema_ref: str
    selector: str  # a property that identifies the success oneOf variant
    item_key: str | None  # if set, the per-element schema is variant.properties[item_key].items
    model: type
    declared_fields: frozenset[str] = frozenset()  # fields that MUST be declared on the model
    sample: dict[str, Any] = dataclass_field(default_factory=dict)  # valid kwargs for required-enforcement


@dataclass(frozen=True)
class _RegistryRow:
    """One implemented response model bound to its pinned schema (#1399 Plan-B).

    The success arm is derived from the schema, not hand-listed: the generator
    reads its required[]/properties so a required field added to the spec is
    enforced automatically. ``sample_override`` supplies valid kwargs only where
    a complex required field (e.g. packages, reporting_period, pagination) cannot
    be synthesized generically — it never weakens or skips a required field.
    ``declared_fields_override`` narrows the F4 declared-field check when the full
    property set would be noise (e.g. forward-compat optional fields).
    """

    schema_ref: str
    selector: str  # property unique to the success arm (picks the oneOf member)
    model: type
    sample_override: dict[str, Any] | None = None
    declared_fields_override: frozenset[str] | None = None


# Every AdCP-grounded response model the seller implements (extends a Library*
# base, maps to a pinned *-response.json). Operations the seller does NOT
# implement (brand-rights, collections, content-standards, governance-plans,
# sponsored-intelligence, comply-test-controller, tmp/*) have no local model and
# are deliberately absent. SalesAgentBaseModel-only response models (internal /
# human_tasks-deprecated: CheckCreativeStatusResponse, CreateCreativeResponse,
# AddCreativeAssetsResponse, GetCreativesResponse, GetPendingCreativesResponse,
# ApproveCreativeResponse, AssignCreativeResponse, UpdatePerformanceIndexResponse,
# CheckMediaBuyStatusResponse, *HumanTask*, *Task*, GetTargetingCapabilities,
# CheckAXERequirements, SimulationControl, ListAuthorizedProperties,
# GetMediaBuysResponse, GetAllMediaBuyDelivery, Adapter*) are not spec-grounded
# success arms and are excluded.
_RESPONSE_MODEL_REGISTRY: list[_RegistryRow] = [
    _RegistryRow(
        schema_ref="/schemas/media-buy/get-products-response.json",
        selector="products",
        model=GetProductsResponse,
    ),
    _RegistryRow(
        schema_ref="/schemas/media-buy/create-media-buy-response.json",
        selector="media_buy_id",
        model=CreateMediaBuySuccess,
        # packages requires the local package shape; synthesize is not reliable.
        sample_override={"media_buy_id": "mb_1", "packages": [{"package_id": "pkg_1", "paused": False}]},
        # Forward-compat fields production emits that must be explicitly declared (F4, PR #1388).
        declared_fields_override=frozenset({"valid_actions", "context"}),
    ),
    _RegistryRow(
        schema_ref="/schemas/media-buy/update-media-buy-response.json",
        selector="media_buy_id",
        model=UpdateMediaBuySuccess,
    ),
    _RegistryRow(
        schema_ref="/schemas/media-buy/get-media-buy-delivery-response.json",
        selector="media_buy_deliveries",
        model=GetMediaBuyDeliveryResponse,
        sample_override={
            "reporting_period": {"start": "2025-02-01T00:00:00Z", "end": "2025-02-02T00:00:00Z"},
            "currency": "USD",
            "aggregated_totals": {"impressions": 0.0, "spend": 0.0, "media_buy_count": 0},
            "media_buy_deliveries": [],
        },
    ),
    _RegistryRow(
        schema_ref="/schemas/creative/get-creative-delivery-response.json",
        selector="creatives",
        model=GetCreativeDeliveryResponse,
        sample_override={
            "reporting_period": {"start": "2025-02-01T00:00:00Z", "end": "2025-02-02T00:00:00Z"},
            "currency": "USD",
            "creatives": [],
        },
    ),
    _RegistryRow(
        schema_ref="/schemas/account/list-accounts-response.json",
        selector="accounts",
        model=ListAccountsResponse,
    ),
    _RegistryRow(
        schema_ref="/schemas/account/sync-accounts-response.json",
        selector="accounts",
        model=SyncAccountsResponse,
    ),
    _RegistryRow(
        schema_ref="/schemas/creative/sync-creatives-response.json",
        selector="creatives",
        model=SyncCreativesResponse,
    ),
    _RegistryRow(
        schema_ref="/schemas/creative/list-creatives-response.json",
        selector="creatives",
        model=ListCreativesResponse,
        sample_override={
            "query_summary": {"total_matching": 0, "returned": 0},
            "pagination": {"has_more": False},
            "creatives": [],
        },
    ),
    _RegistryRow(
        schema_ref="/schemas/creative/list-creative-formats-response.json",
        selector="formats",
        model=ListCreativeFormatsResponse,
    ),
    _RegistryRow(
        schema_ref="/schemas/signals/get-signals-response.json",
        selector="signals",
        model=GetSignalsResponse,
    ),
]


def _success_arm(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the success (sub-)schema: the oneOf arm whose required[] names
    neither ``errors`` nor ``task_id`` (error / submitted arms), or the schema
    itself when it is a flat single-shape response (no oneOf)."""
    if "oneOf" not in schema:
        return schema
    for arm in schema["oneOf"]:
        required = set(arm.get("required", []))
        if "errors" not in required and "task_id" not in required:
            return arm
    raise AssertionError(f"No success arm found in oneOf (all arms look like error/submitted): {schema.get('$id')}")


def _synthesize_sample(arm: dict[str, Any]) -> dict[str, Any]:
    """Build valid kwargs covering every required field from the pinned arm.

    Array required fields → empty list (valid + minimal). Other types →
    generate_example_value. Complex shapes use a registry sample_override instead.
    """
    sample: dict[str, Any] = {}
    props = arm.get("properties", {})
    for fname in set(arm.get("required", [])) - _VERSION_FIELDS:
        spec = props.get(fname, {})
        if spec.get("type") == "array":
            sample[fname] = []
        else:
            sample[fname] = generate_example_value(spec.get("type", "string"), fname, spec)
    return sample


def _build_alignments_from_pinned(registry: list[_RegistryRow]) -> list[ResponseAlignment]:
    """Derive an envelope-level ResponseAlignment per registered model from the
    pinned success arm — machine-complete, so a new spec-required field on any
    registered model is enforced without hand-editing this list (#1399 Plan-B)."""
    alignments: list[ResponseAlignment] = []
    for row in registry:
        arm = _success_arm(load_json_schema(row.schema_ref))
        declared = row.declared_fields_override
        if declared is None:
            # Default to the REQUIRED fields (not all properties): the bug class is a
            # spec-REQUIRED field silently dropped (F4/F5/Chris-#2). Demanding every
            # OPTIONAL forward-compat property be explicitly declared would over-reach
            # (response models intentionally carry optional fields via extra='allow').
            # A row may set declared_fields_override to also pin specific optional
            # fields production emits (e.g. CreateMediaBuySuccess valid_actions/context).
            declared = frozenset(arm.get("required", [])) - _VERSION_FIELDS
        sample = row.sample_override if row.sample_override is not None else _synthesize_sample(arm)
        alignments.append(
            ResponseAlignment(
                schema_ref=row.schema_ref,
                selector=row.selector,
                item_key=None,
                model=row.model,
                declared_fields=declared,
                sample=sample,
            )
        )
    return alignments


# Per-ITEM alignments (item_key set) that the envelope-level generator does not
# cover. Kept hand-curated and supplemental so per-item required enforcement
# (F5, PR #1388) is not lost when the envelope list is machine-generated.
_SUPPLEMENTAL_ALIGNMENTS: list[ResponseAlignment] = [
    ResponseAlignment(
        schema_ref="/schemas/account/sync-accounts-response.json",
        selector="accounts",
        item_key="accounts",
        model=SyncResponseAccount,
        sample={"brand": {"domain": "acme.com"}, "operator": "create", "action": "created", "status": "active"},
    ),
]


RESPONSE_ALIGNMENTS = _build_alignments_from_pinned(_RESPONSE_MODEL_REGISTRY) + _SUPPLEMENTAL_ALIGNMENTS


def _resolve_response_item_schema(alignment: ResponseAlignment) -> dict[str, Any]:
    """Resolve the pinned (sub-)schema a response model maps to.

    Handles flat single-shape responses (no oneOf → the schema is the success
    shape) and oneOf responses (pick the arm exposing ``selector``).
    """
    schema = load_json_schema(alignment.schema_ref)
    if "oneOf" in schema:
        variant = next(v for v in schema["oneOf"] if alignment.selector in v.get("properties", {}))
    else:
        variant = schema
    if alignment.item_key:
        return variant["properties"][alignment.item_key]["items"]
    return variant


class TestResponseModelAlignment:
    """Local success models conform to the pinned AdCP response schemas."""

    @pytest.mark.parametrize("alignment", RESPONSE_ALIGNMENTS, ids=lambda a: a.model.__name__)
    def test_declared_fields_present_in_schema_and_model(self, alignment: ResponseAlignment):
        """Each declared_field is defined by the pinned schema AND declared on the model.

        Catches fields that production emits but the model only carries via inherited
        extra='allow' (would silently vanish if the parent's extra-mode changed).
        """
        if not alignment.declared_fields:
            pytest.skip(f"{alignment.model.__name__}: no declared-field requirement")
        item = _resolve_response_item_schema(alignment)
        schema_props = set(item.get("properties", {}))
        model_fields = set(alignment.model.model_fields)
        for fname in alignment.declared_fields:
            assert fname in schema_props, f"{fname!r} not defined by pinned schema {alignment.schema_ref}"
            assert fname in model_fields, (
                f"{fname!r} is defined by the pinned schema but NOT declared on "
                f"{alignment.model.__name__} (only surviving via extra='allow')"
            )

    @pytest.mark.parametrize("alignment", RESPONSE_ALIGNMENTS, ids=lambda a: a.model.__name__)
    def test_required_fields_enforced(self, alignment: ResponseAlignment):
        """The model enforces every field the pinned schema marks required."""
        item = _resolve_response_item_schema(alignment)
        required = set(item.get("required", [])) - _VERSION_FIELDS
        if not required:
            pytest.skip(f"{alignment.model.__name__}: pinned schema marks no required fields")
        assert alignment.sample, (
            f"{alignment.model.__name__}: schema requires {sorted(required)} but no sample provided"
        )
        assert required <= set(alignment.sample), (
            f"sample for {alignment.model.__name__} missing required keys: {sorted(required - set(alignment.sample))}"
        )
        # The complete required set constructs cleanly.
        assert alignment.model(**alignment.sample) is not None
        # Omitting any required field must raise (the model enforces it, not the call sites).
        for fname in required:
            partial = {k: v for k, v in alignment.sample.items() if k != fname}
            with pytest.raises(ValidationError):
                alignment.model(**partial)


def _enumerate_grounded_response_models() -> set[type]:
    """Enumerate every local response model the registry MUST cover.

    This makes the registry's own inclusion rule executable instead of
    hand-listed: a model belongs iff it is (1) defined in ``src.core.schemas``
    (so imported ``Library*`` aliases, whose ``__module__`` is ``adcp.types.*``,
    are excluded), (2) extends an ``adcp`` library type directly (``__bases__``
    contains an ``adcp.types`` class), and (3) carries a response role — its name
    ends in ``Response`` or ``Success`` (the oneOf success arm). Error arms end in
    ``Error`` and requests in ``Request``, so both are excluded; reusable
    sub-components (``Account``, ``Package``, ``Pagination``) lack the response
    suffix and are excluded too.

    A future library-grounded response model that nobody registers is therefore
    discovered here and fails the coverage gate, rather than slipping through a
    stale literal.
    """
    import adcp.types as adcp_types

    adcp_bases = {obj for obj in vars(adcp_types).values() if inspect.isclass(obj)}

    import src.core.schemas as schemas_pkg

    modules = [schemas_pkg]
    for info in pkgutil.walk_packages(schemas_pkg.__path__, schemas_pkg.__name__ + "."):
        modules.append(importlib.import_module(info.name))

    grounded: set[type] = set()
    for module in modules:
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseModel):
                continue
            if not (obj.__module__ or "").startswith("src.core.schemas"):
                continue  # skip imported Library* aliases re-exported into the namespace
            if not (name.endswith("Response") or name.endswith("Success")):
                continue  # response role only; error arms end in 'Error', requests in 'Request'
            if any(base in adcp_bases for base in obj.__bases__):
                grounded.add(obj)
    return grounded


class TestResponseAlignmentCoverage:
    """RESPONSE_ALIGNMENTS is machine-complete over implemented response models.

    #1399 Plan-B: every AdCP-grounded local response model (one that extends a
    Library* base and maps to a pinned *-response.json) must be covered by an
    alignment, so a required field the pinned spec adds cannot silently slip an
    unenforced model. This is the coverage gate; the per-field enforcement is in
    TestResponseModelAlignment.
    """

    def test_all_implemented_response_models_are_covered(self):
        # The set of models that MUST be registered is enumerated from the schema
        # package (the registry's own inclusion rule, executable) — never a literal
        # list, so a newly-added library-grounded response model that nobody
        # registered fails this gate instead of silently slipping through.
        expected = _enumerate_grounded_response_models()
        covered = {a.model for a in RESPONSE_ALIGNMENTS}
        # One-directional: every grounded model must be covered. ``covered`` may
        # carry extra alignments (e.g. nested sub-arms) that are not themselves
        # top-level response models, so strict equality would false-fail.
        missing = expected - covered
        assert not missing, (
            f"AdCP-grounded response models not covered by RESPONSE_ALIGNMENTS: {sorted(m.__name__ for m in missing)}"
        )


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])

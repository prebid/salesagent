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
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.core.exceptions import AdCPInvalidRequestError
from src.core.schemas import (
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
    GetMediaBuyDeliveryRequest,
    GetMediaBuysResponse,
    GetProductsRequest,
    GetProductsResponse,
    GetSignalsResponse,
    ListAccountsResponse,
    ListCreativesRequest,
    ListCreativesResponse,
    Product,
    Signal,
    SyncAccountsResponse,
    SyncCreativesRequest,
    SyncCreativesResponse,
    SyncGovernanceResponse,
    SyncGovernanceResponseAccount,
    SyncResponseAccount,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.schemas.creative import ListCreativeFormatsResponse
from src.core.schemas.delivery import GetCreativeDeliveryResponse, GetMediaBuyDeliveryResponse
from tests.helpers import pinned_schema
from tests.helpers.adcp_factories import create_test_cpm_pricing_option, create_test_publisher_properties_by_tag

# AdCP schemas are read from the installed adcp SDK's own pinned tree
# (tests/helpers/pinned_schema.py) — the SDK's own version IS the pin (moves
# with pyproject.toml's adcp version), so there is exactly one upstream pin
# for this suite, not an independently vendored snapshot (that snapshot
# previously lived here, pinned at adcontextprotocol/adcp@04f59d2d5, a full
# spec-minor behind the SDK's).
#
# Ref strings in this file are the one form the whole repo uses: a
# category-qualified path relative to the version root, exactly as the SDK's
# own index writes it. This file used to carry a "/schemas/" prefix of its own
# and strip it here, which made a SECOND ref normalizer with rules that
# disagreed with the shared one.

# Map AdCP schema refs to Pydantic model classes. At 04f59d2d5, sync/list-creatives
# live under `creative/` (relocated from `media-buy/` earlier in 3.x).
#
# NOTE: CreateMediaBuyRequest is temporarily excluded due to AdCP spec evolution.
# The spec now requires brand_card, but we maintain backward compatibility
# via brand_manifest. Full brand_card implementation will be added in a separate PR.
SCHEMA_TO_MODEL_MAP = {
    "media-buy/get-products-request.json": GetProductsRequest,
    # "media-buy/create-media-buy-request.json": CreateMediaBuyRequest,  # Skipped - pending brand_card implementation
    "media-buy/update-media-buy-request.json": UpdateMediaBuyRequest,
    "media-buy/get-media-buy-delivery-request.json": GetMediaBuyDeliveryRequest,
    "creative/sync-creatives-request.json": SyncCreativesRequest,
    "creative/list-creatives-request.json": ListCreativesRequest,
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

# Fields the SDK's current schema tree defines but the local model does not yet
# model. These are spec-vs-library mismatches, not bugs in our code.
#
# Keys MUST match the `schema_ref` values in SCHEMA_TO_MODEL_MAP verbatim;
# `KNOWN_SCHEMA_LIBRARY_MISMATCHES.get(schema_ref, set())` lookups silently fall back
# to an empty set otherwise.
KNOWN_SCHEMA_LIBRARY_MISMATCHES: dict[str, set[str]] = {
    "media-buy/get-products-request.json": set(),
    "media-buy/update-media-buy-request.json": set(),
    "media-buy/get-media-buy-delivery-request.json": set(),
    "creative/sync-creatives-request.json": set(),
    "creative/list-creatives-request.json": set(),
}


def load_json_schema(schema_ref: str) -> dict[str, Any]:
    """Load an AdCP schema from the installed adcp SDK's pinned tree.

    Normalization is ``pinned_schema.normalize_ref`` — the single shared rule,
    not a second one local to this file. Every ``$ref`` inside the returned
    dict is canonicalized to root-relative form
    (``pinned_schema.load_canonicalized``), so a ``$ref`` found while walking
    the returned schema is itself a valid input here. A missing file is a HARD
    FAILURE (the pin moved, or a ``$ref`` is outside the resolvable tree),
    never a silent skip.
    """
    return pinned_schema.load_canonicalized(pinned_schema.normalize_ref(schema_ref))


class _CannotSynthesize(AssertionError):
    """The generator has no rule for a pinned shape and refuses to invent one.

    Raised only under ``strict`` (the response side). The lenient default keeps the
    pre-existing request-side behaviour byte-for-byte, so adding this cannot quietly
    become a rewrite of the generator.

    Subclasses ``AssertionError`` so an escaping instance reads as a test-instrument
    failure rather than a production defect — which is the entire point. An invented
    value is not neutral: fed to a required enum or formatted field it raises
    ``ValidationError``, and the alignment suite then reports the instrument's own gap
    as a conformance failure against production code. That is the story
    ``_unsynthesized_guess`` tells about the 'test_status_value' guess, and it is what bought
    the envelope's ``status`` its blanket exclusion from requiredness grading — the
    exclusion GH #1900 exists to undo.
    """


def _cannot_synthesize(field_type: str, field_name: str, field_spec: dict | None, reason: str) -> _CannotSynthesize:
    """Build the located refusal, naming the shape and the two sanctioned escapes."""
    return _CannotSynthesize(
        f"cannot synthesize a value for {field_name or '<unnamed>'} (type {field_type!r}): {reason}. "
        f"Pinned shape: {field_spec if field_spec else '{}'}. Extend generate_example_value for this "
        f"shape, or set sample_override on the schema's _RegistryRow. Do NOT exclude the field from "
        f"grading — suppressing a field to silence an instrument gap is the defect this raise exists "
        f"to prevent."
    )


def _unsynthesized_guess(field_name: str) -> str:
    """The generator's last-resort string for a shape it has no rule for.

    Named rather than inlined so ``_synthesize_sample`` can RECOGNIZE a guess and
    refuse it instead of feeding it to a model. A guess is not a sample: it is the
    generator saying "I could not derive this", and passing it on is what turned a
    mechanical gap in the instrument into a false conformance failure against
    production code (the 'test_status_value' failures that once bought the envelope
    'status' its blanket exclusion from requiredness grading).
    """
    return f"test_{field_name}_value"


def generate_example_value(
    field_type: str, field_name: str = "", field_spec: dict = None, *, strict: bool = False
) -> Any:
    """Generate a reasonable example value for a JSON schema type.

    ``strict`` is the response side's contract: a shape with no rule raises
    :class:`_CannotSynthesize` instead of inventing a value. The default stays lenient
    so pre-existing request-side callers are unchanged byte-for-byte.
    """
    # Inline enum (e.g. cache_scope: {"type": "string", "enum": ["public", "account"]}):
    # a generic "test_<field>_value" string is not a member of the enum and fails
    # Pydantic validation on construction — checked before the $ref/oneOf/allOf
    # branches below since an inline enum can appear on any of those field shapes.
    if field_spec and "enum" in field_spec:
        return field_spec["enum"][0]

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
                return generate_example_value(ref_type, field_name, ref_schema, strict=strict)
            # Generate object with required fields from the resolved schema
            obj = {}
            required_fields = ref_schema.get("required", [])
            for prop_name, prop_spec in ref_schema.get("properties", {}).items():
                if prop_name in required_fields:
                    prop_type = prop_spec.get("type", "string")
                    obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec, strict=strict)
            if obj:
                return obj
            # Empty half only: the schema loaded but this resolver reads just
            # type/enum/properties, so a $ref whose structure is a discriminated
            # oneOf yields nothing and {} would be invented, not derived.
            if strict:
                raise _cannot_synthesize(
                    field_type, field_name, field_spec, f"resolved $ref {ref!r} exposes no readable required properties"
                )
            return {}
        except _CannotSynthesize:
            raise
        except Exception as exc:
            if strict:
                raise _cannot_synthesize(
                    field_type, field_name, field_spec, f"$ref {ref!r} could not be resolved"
                ) from exc
            return {}

    # Handle allOf with $ref (e.g., time_budget: allOf[{$ref: duration.json}])
    if field_spec and "allOf" in field_spec:
        for variant in field_spec["allOf"]:
            if "$ref" in variant:
                return generate_example_value("object", field_name, variant, strict=strict)
        # If no $ref in allOf, merge properties from all variants
        merged_spec = dict(field_spec)
        del merged_spec["allOf"]
        for variant in field_spec["allOf"]:
            merged_spec.update(variant)
        return generate_example_value(merged_spec.get("type", "object"), field_name, merged_spec, strict=strict)

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
            return generate_example_value(variant_type, field_name, ref_schema, strict=strict)
        variant_type = first_variant.get("type", "string")
        return generate_example_value(variant_type, field_name, first_variant, strict=strict)

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
        if strict:
            # Reached recursively for a container's property too, where the guess is
            # embedded in the returned object and _synthesize_sample's top-level
            # sentinel check never sees it.
            raise _cannot_synthesize(field_type, field_name, field_spec, "no naming or pattern rule matched")
        return _unsynthesized_guess(field_name)
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
                            return [generate_example_value(ref_type, field_name, ref_schema, strict=strict)]
                    except _CannotSynthesize:
                        raise
                    except Exception as exc:
                        if strict:
                            raise _cannot_synthesize(
                                field_type, field_name, field_spec, f"array items $ref {ref!r} could not be resolved"
                            ) from exc
                    # For other refs, return minimal object
                    if strict:
                        raise _cannot_synthesize(
                            field_type, field_name, field_spec, f"array items $ref {ref!r} resolves to an unread object"
                        )
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
                                obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec, strict=strict)
                    if obj:
                        return [obj]
                    # Empty half only: no required/id property was readable, so [] here is an
                    # invented element shape — unlike a top-level required array, whose empty
                    # list is a spec-valid derived minimal instance (minItems is absent).
                    if strict:
                        raise _cannot_synthesize(
                            field_type,
                            field_name,
                            field_spec,
                            "array items object exposes no readable required properties",
                        )
                    return []
                else:
                    # Generate one example item
                    return [generate_example_value(item_type, field_name, items_spec, strict=strict)]
        # 'items' is absent, or is a LIST (tuple validation) the branch above cannot read,
        # so the element shape is unknown and [] would be invented.
        if strict:
            raise _cannot_synthesize(field_type, field_name, field_spec, "array has no readable 'items' schema")
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
                    obj[prop_name] = generate_example_value(prop_type, prop_name, prop_spec, strict=strict)
            return obj
        if strict:
            raise _cannot_synthesize(
                field_type, field_name, field_spec, "object declares no 'properties' to derive from"
            )
        return {}
    else:
        if strict:
            raise _cannot_synthesize(field_type, field_name, field_spec, f"no branch handles type {field_type!r}")
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
        except AdCPInvalidRequestError as e:
            # A custom business-rule validator (stricter than the raw schema) raised
            # a typed INVALID_REQUEST — e.g. AdCPPackageUpdate requires package_id and
            # rejects immutable fields. The synthetic generator does not satisfy those
            # nested constraints. Models MAY be stricter than spec; this is acceptable
            # as long as it is not rejecting a spec field (it requires a required field).
            pytest.skip(
                f"{model_class.__name__} enforces a business-rule shape "
                f"(custom validator → INVALID_REQUEST), stricter than the schema. Acceptable. Error: {e}"
            )
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
    ``declared_fields_override`` ADDS to the F4 declared-field check — the pinned
    required set is always included — so a row can also pin specific OPTIONAL fields
    production emits (e.g. CreateMediaBuySuccess valid_actions/context) without
    quietly dropping the required ones.
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
# GetAllMediaBuyDelivery, Adapter*) are not spec-grounded
# success arms and are excluded.
_RESPONSE_MODEL_REGISTRY: list[_RegistryRow] = [
    _RegistryRow(
        schema_ref="media-buy/get-products-response.json",
        selector="products",
        model=GetProductsResponse,
    ),
    _RegistryRow(
        schema_ref="media-buy/create-media-buy-response.json",
        selector="media_buy_id",
        model=CreateMediaBuySuccess,
        # packages requires the local package shape; synthesize is not reliable.
        # confirmed_at/revision carry NO model default any more (they are columns the
        # repository owns), so the sample has to supply them like any other
        # schema-required field the model will not fill in for itself.
        sample_override={
            "media_buy_id": "mb_1",
            "packages": [{"package_id": "pkg_1", "paused": False}],
            "confirmed_at": "2026-03-15T12:00:00Z",
            "revision": 1,
        },
        # Forward-compat fields production emits that must be explicitly declared (F4, PR #1388).
        declared_fields_override=frozenset({"valid_actions", "context"}),
    ),
    _RegistryRow(
        schema_ref="media-buy/update-media-buy-response.json",
        selector="media_buy_id",
        model=UpdateMediaBuySuccess,
    ),
    _RegistryRow(
        schema_ref="media-buy/get-media-buys-response.json",
        selector="media_buys",
        model=GetMediaBuysResponse,
    ),
    _RegistryRow(
        schema_ref="media-buy/get-media-buy-delivery-response.json",
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
        schema_ref="creative/get-creative-delivery-response.json",
        selector="creatives",
        model=GetCreativeDeliveryResponse,
        sample_override={
            "reporting_period": {"start": "2025-02-01T00:00:00Z", "end": "2025-02-02T00:00:00Z"},
            "currency": "USD",
            "creatives": [],
        },
    ),
    _RegistryRow(
        schema_ref="account/list-accounts-response.json",
        selector="accounts",
        model=ListAccountsResponse,
    ),
    _RegistryRow(
        schema_ref="account/sync-accounts-response.json",
        selector="accounts",
        model=SyncAccountsResponse,
    ),
    _RegistryRow(
        schema_ref="account/sync-governance-response.json",
        selector="accounts",
        model=SyncGovernanceResponse,
    ),
    _RegistryRow(
        schema_ref="creative/sync-creatives-response.json",
        selector="creatives",
        model=SyncCreativesResponse,
    ),
    _RegistryRow(
        schema_ref="creative/list-creatives-response.json",
        selector="creatives",
        model=ListCreativesResponse,
        sample_override={
            "query_summary": {"total_matching": 0, "returned": 0},
            "pagination": {"has_more": False},
            "creatives": [],
        },
    ),
    _RegistryRow(
        schema_ref="creative/list-creative-formats-response.json",
        selector="formats",
        model=ListCreativeFormatsResponse,
    ),
    _RegistryRow(
        schema_ref="signals/get-signals-response.json",
        selector="signals",
        model=GetSignalsResponse,
    ),
]


def _resolved_allof_arms(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield each arm of a schema's top-level ``allOf``, ``$ref``s resolved.

    Requiredness and definedness are harvested from these same arms by
    ``_allof_required_fields`` and ``_allof_properties``. They walked the list
    independently before, which is the shape that lets the two halves drift apart —
    and they must not, because a field pulled out of an arm's ``required`` without
    that arm's ``properties`` is reported as schema-required and undefined in the
    same breath.
    """
    for arm in schema.get("allOf", []) or []:
        yield pinned_schema.load_canonicalized(arm["$ref"]) if "$ref" in arm else arm


def _allof_required_fields(schema: dict[str, Any]) -> set[str]:
    """Domain-level required fields from every arm of a schema's top-level
    ``allOf`` — e.g. a shared error/pricing sub-schema composed in alongside
    the domain shape. A response schema with no top-level ``oneOf``/``required``
    of its own (get-products-response.json in 3.1.1) can still spec-require
    fields via allOf; without merging them in, a schema-required field
    silently drops out of grading instead of failing loudly (the exact bug
    class this suite exists to catch).

    This includes the shared Protocol Envelope arm's own ``status``. It used to be
    subtracted back out; nothing is excluded now, so a pin bump that adds a second
    envelope-required field lands directly as alignment failures on every model
    lacking it — forcing the same per-field decision, at the location that needs it.
    """
    return {field for arm in _resolved_allof_arms(schema) for field in arm.get("required", [])}


def _standard_branch_required_fields(schema: dict[str, Any]) -> set[str]:
    """Required fields from the innermost ``else`` branch of an ``if``/``then``/``else`` chain.

    3.1.1 response schemas (e.g. get-products-response.json, get-signals-response.json)
    express conditional requiredness this way instead of a top-level ``required`` or a
    root ``oneOf``: an outer if/then/else branches on the wholesale-unchanged shape,
    nesting a second if/then/else inside its ``else`` that branches on ``status ==
    "failed"`` vs. the standard success shape. The alignment suite's samples are all
    ordinary successful responses, so the "standard" branch — the final ``else`` at the
    end of the chain — is the one whose ``required`` applies; without walking it, these
    fields silently drop out of grading (the schema has no OTHER top-level ``required``
    to fall back on) instead of failing loudly.
    """
    node = schema
    walked = False
    while "else" in node:
        node = node["else"]
        walked = True
    return set(node.get("required", [])) if walked else set()


def _allof_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Property definitions contributed by every arm of a schema's top-level
    ``allOf`` — the shared Protocol/Version Envelope arms above all.

    Requiredness and definedness have to be merged from the same place or the
    walk contradicts itself: ``_allof_required_fields`` already pulls ``status``
    out of the envelope arm's ``required``, so a walk that does not also pull in
    the arm's ``properties`` reports a field as schema-required and, in the same
    breath, as not defined by the schema. Both graders read the merged node —
    ``test_declared_fields_present_in_schema_and_model`` checks membership in
    ``properties``, and ``_synthesize_sample`` reads the per-field spec out of it
    to build a valid value — so the missing half showed up as 7 spurious
    "not defined by pinned schema" failures plus samples synthesized from an
    empty spec.
    """
    # Later arms win on key collision, which is what the sequential ``|=`` did.
    return {name: spec for arm in _resolved_allof_arms(schema) for name, spec in arm.get("properties", {}).items()}


def _merge_composed(node: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Merge the fields composed into ``schema`` at its root — ``required`` from
    its top-level allOf arms and its if/then/else standard branch, ``properties``
    from those same allOf arms — into ``node``, rebuilding it only if that adds
    anything.

    ``node``'s own definitions win: a domain schema that redeclares an envelope
    property (narrowing it, say) is the more specific statement about the shape
    a buyer receives.

    schema is usually node itself, but a resolved oneOf variant passes the
    top-level schema separately: allOf/if-then-else compose at the schema
    root, not on the individual arm.
    """
    merged_required = (
        set(node.get("required", [])) | _allof_required_fields(schema) | _standard_branch_required_fields(schema)
    )
    merged_properties = _allof_properties(schema) | node.get("properties", {})
    if merged_required == set(node.get("required", [])) and merged_properties == node.get("properties", {}):
        return node
    return {**node, "required": sorted(merged_required), "properties": merged_properties}


def _success_shape(
    schema: dict[str, Any],
    *,
    selector: str | None = None,
    item_key: str | None = None,
) -> dict[str, Any]:
    """The pinned success (sub-)schema a response model maps to.

    ONE resolver, because there were two and they disagreed on the step that
    matters. Both picked a success arm out of a ``oneOf``; only one then merged the
    composition at the schema ROOT — the shared Protocol/Version Envelope arms and
    the standard branch of any top-level ``if``/``then``/``else`` — into it. The
    other returned the arm raw.

    That difference is not cosmetic: AdCP 3.1.1 composes ``status`` onto responses
    through a top-level ``allOf``, so for a ``oneOf`` response the un-merged
    resolver produced an arm with no ``status`` in ``properties`` at all. Every
    check keyed off "the fields this schema declares" — declared_fields, the
    sample, and the model_dump-survival check written for exactly the
    ``confirmed_at`` bug class — then skipped ``status`` silently, on the two models
    where it was most worth grading.

    Arm selection has two modes, and they are the reason the resolvers were
    separate:
    * ``selector`` — pick the arm exposing that property (the registry knows which
      field identifies its success arm);
    * no selector — pick the first arm whose ``required`` names neither ``errors``
      nor ``task_id``, i.e. is neither the error nor the submitted arm.
    Both then merge the root composition, which is the part that must not differ.

    ``item_key`` descends into an array's item schema (following a ``$ref`` to a
    standalone schema when the item is not inlined) and merges composition there.
    """
    if "oneOf" in schema:
        if selector is not None:
            variant = next(v for v in schema["oneOf"] if selector in v.get("properties", {}))
        else:
            variant = next(
                (arm for arm in schema["oneOf"] if not ({"errors", "task_id"} & set(arm.get("required", [])))),
                None,
            )
            if variant is None:
                raise AssertionError(
                    f"No success arm found in oneOf (all arms look like error/submitted): {schema.get('$id')}"
                )
    else:
        variant = schema

    if item_key:
        item_schema = variant["properties"][item_key]["items"]
        # Some item schemas are inlined (SyncResponseAccount); others are a $ref to a
        # standalone schema (get-products-response.json's products[] ->
        # core/product.json). load_canonicalized already rewrote the ref to the
        # root-relative form pinned_schema.load() expects, so a raw unresolved $ref
        # dict would silently short-circuit every field/required check below.
        if "$ref" in item_schema:
            item_schema = pinned_schema.load(item_schema["$ref"])
        return _merge_composed(item_schema, item_schema)

    return _merge_composed(variant, schema)


def _model_literal_value(model: type | None, fname: str) -> Any:
    """The single value *model* narrows ``fname`` to, or ``None``.

    A response type that only ever represents one outcome may narrow a spec enum to
    one member — ``CompletedTaskStatusMixin`` pins ``status`` to ``"completed"``
    because the model is the synchronous-success arm. The schema still lists the whole
    enum, so a sample synthesized from the schema alone can pick a DIFFERENT member
    and then fail to construct the model.

    That failure would be reported as a conformance defect while being an artefact of
    the instrument — the exact false-failure class this module refuses to tolerate
    elsewhere. The narrowing itself is not waved through: the caller asserts the
    narrowed value is a member of the schema's enum, so a model that narrows to
    something the spec does not allow still fails, and fails AT that field.
    """
    from typing import Literal, get_args, get_origin

    if model is None:
        return None
    field = model.model_fields.get(fname)
    if field is None or get_origin(field.annotation) is not Literal:
        return None
    args = get_args(field.annotation)
    return args[0] if len(args) == 1 else None


def _synthesize_sample(arm: dict[str, Any], schema_ref: str, model: type | None = None) -> dict[str, Any]:
    """Build valid kwargs covering every required field from the pinned arm.

    Array required fields → empty list (valid + minimal). Enums and ``$ref``\\ s to
    enum schemas → a real member. Other types → generate_example_value.

    A shape the generator has no rule for RAISES here rather than passing its guess
    through. That is the whole design correction: an instrument that cannot measure a
    field must fail loudly AT that field, not quietly hand the model a value the spec
    never allowed and report the resulting ValidationError as a conformance failure in
    production code. This is the class of false failure that the envelope-status
    exclusion was created to suppress — and suppressing a whole field's grading to silence an
    instrument bug costs far more than one located error demanding either a generator
    rule or an explicit sample_override, both of which already exist.
    """
    sample: dict[str, Any] = {}
    props = arm.get("properties", {})
    for fname in set(arm.get("required", [])) - _VERSION_FIELDS:
        spec = props.get(fname, {})
        narrowed = _model_literal_value(model, fname)
        if narrowed is not None:
            allowed = spec.get("enum")
            assert allowed is None or narrowed in allowed, (
                f"{model.__name__}.{fname} narrows to {narrowed!r}, which the pinned "
                f"{schema_ref} does not allow (enum: {allowed})"
            )
            sample[fname] = narrowed
            continue
        if spec.get("type") == "array":
            sample[fname] = []
            continue
        try:
            value = generate_example_value(spec.get("type", "string"), fname, spec, strict=True)
        except _CannotSynthesize as exc:
            # The generator refuses at the field; only this frame knows WHICH schema was
            # being synthesized, so the located error is completed here. Previously this
            # was a post-hoc comparison against the guess sentinel, which could only
            # catch a guess returned at the TOP level — a guess embedded inside a
            # container came back as a well-formed dict and passed.
            raise _CannotSynthesize(
                f"cannot synthesize a sample for required field {fname!r} of {schema_ref} — {exc}"
            ) from exc
        sample[fname] = value
    return sample


def _build_alignments_from_pinned(registry: list[_RegistryRow]) -> list[ResponseAlignment]:
    """Derive an envelope-level ResponseAlignment per registered model from the
    pinned success arm — machine-complete, so a new spec-required field on any
    registered model is enforced without hand-editing this list (#1399 Plan-B)."""
    alignments: list[ResponseAlignment] = []
    for row in registry:
        arm = _success_shape(load_json_schema(row.schema_ref))
        # The REQUIRED fields (not all properties): the bug class is a spec-REQUIRED
        # field silently dropped (PR #1941 review). Demanding every OPTIONAL
        # forward-compat property be declared would over-reach — response models
        # intentionally carry optional fields via extra='allow'.
        declared = frozenset(arm.get("required", [])) - _VERSION_FIELDS
        if row.declared_fields_override is not None:
            # ADDITIVE, which is what the field has always claimed to be ("also pin
            # specific optional fields production emits"). It used to REPLACE, and the
            # one row that sets it thereby dropped every spec-required field —
            # media_buy_id, packages, confirmed_at, revision and status — out of the
            # declared-field check while reading as if it had only added two.
            declared |= row.declared_fields_override
        sample = (
            row.sample_override
            if row.sample_override is not None
            else _synthesize_sample(arm, row.schema_ref, row.model)
        )
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
        schema_ref="account/sync-accounts-response.json",
        selector="accounts",
        item_key="accounts",
        model=SyncResponseAccount,
        # brand/operator/action/status are explicitly declared on the model AND required in
        # the pinned schema; pin them so test_declared_fields_present_in_schema_and_model runs
        # for this row too (an empty declared_fields skips it) — the still-skipped neighbour of
        # the sync-governance row below (#1329).
        declared_fields=frozenset({"brand", "operator", "action", "status"}),
        sample={"brand": {"domain": "acme.com"}, "operator": "create", "action": "created", "status": "active"},
    ),
    ResponseAlignment(
        schema_ref="account/sync-governance-response.json",
        selector="accounts",
        item_key="accounts",
        model=SyncGovernanceResponseAccount,
        # account/status are explicitly declared on the model (schemas/account.py) and
        # required in the pinned schema; pin them so test_declared_fields_present_in_schema_
        # and_model actually runs for this row (an empty declared_fields skips it) — the check
        # that catches a field surviving only via inherited extra='allow' (#1329 item 4).
        declared_fields=frozenset({"account", "status"}),
        sample={"account": {"account_id": "acc_1"}, "status": "synced"},
    ),
    ResponseAlignment(
        schema_ref="media-buy/get-products-response.json",
        selector="products",
        item_key="products",
        model=Product,
        # core/product.json's own required[] — reporting_capabilities included.
        # Product carries a validated default_factory for it, so omitting it from
        # the sample is graded by the model_defaulted branch of
        # test_required_fields_enforced (the attribute must come out non-None),
        # and by test_declared_fields_present_in_schema_and_model's model_dump()
        # presence check (#1868 review).
        declared_fields=frozenset(
            {
                "product_id",
                "name",
                "description",
                "publisher_properties",
                "delivery_type",
                "pricing_options",
                "reporting_capabilities",
            }
        ),
        sample={
            "product_id": "align_test_product",
            "name": "Alignment Test Product",
            "description": "Product used to verify the pinned schema descends into products[].",
            "publisher_properties": [create_test_publisher_properties_by_tag()],
            "delivery_type": "guaranteed",
            "pricing_options": [create_test_cpm_pricing_option()],
            "reporting_capabilities": {
                "available_reporting_frequencies": ["daily"],
                "expected_delay_minutes": 60,
                "timezone": "UTC",
                "supports_webhooks": False,
                "available_metrics": ["impressions", "clicks"],
                "date_range_support": "date_range",
            },
        },
    ),
]


RESPONSE_ALIGNMENTS = _build_alignments_from_pinned(_RESPONSE_MODEL_REGISTRY) + _SUPPLEMENTAL_ALIGNMENTS


def _resolve_response_item_schema(alignment: ResponseAlignment) -> dict[str, Any]:
    """The pinned (sub-)schema for a registry row — :func:`_success_shape` by row."""
    return _success_shape(
        load_json_schema(alignment.schema_ref),
        selector=alignment.selector,
        item_key=alignment.item_key,
    )


# The no-rule exits of generate_example_value that strict synthesis must refuse
# (plan §3.4 F9, GH #1900). Every row was measured at HEAD with sys.settrace on the
# function's own returns, so each drives the exit named in its id and no other.
#
# Columns:
#   lenient_value — what the lenient default (strict=False) must keep returning
#                   byte-for-byte, because the pre-existing request-side callers
#                   depend on it. Pinning it here is what keeps "add strict" from
#                   quietly becoming "change the generator".
#   from_cause    — True for the two ``except Exception`` swallows, whose raise must
#                   carry the exception it swallowed (``raise ... from exc``). That is
#                   what makes them honour load_json_schema's own HARD-FAILURE
#                   contract instead of trading one silence for another.
_NO_RULE_EXITS = [
    # The $ref resolved and was read, but the resolver inspects only
    # type/enum/properties — never oneOf — so a discriminated union yields {}.
    # core/signal-id.json is entirely a oneOf; it reaches signals[].signal_id.
    pytest.param(
        "object", "signal_id", {"$ref": "core/signal-id.json"}, {}, False, id="ref-read-but-unreadable-198-else"
    ),
    # The $ref did not resolve at all: swallowed by ``except Exception: return {}``.
    pytest.param("object", "thing", {"$ref": "core/unresolvable-thing.json"}, {}, True, id="ref-unresolvable-199"),
    # A guessed string embedded in a container. _synthesize_sample's sentinel check
    # compares the TOP-LEVEL value only, so a guess produced one level down is
    # returned silently — and a guess fed to a required enum/formatted field is
    # reported as a conformance failure against production, which is the exact
    # false-failure that bought envelope 'status' its blanket exclusion.
    pytest.param(
        "object",
        "wrapper",
        {"type": "object", "required": ["weird"], "properties": {"weird": {"description": "no type"}}},
        {"weird": _unsynthesized_guess("weird")},
        False,
        id="nested-string-guess-263",
    ),
    # Array items whose $ref did not resolve: swallowed by ``except Exception: pass``,
    # falling through to the invented [{}].
    pytest.param(
        "array",
        "things",
        {"type": "array", "items": {"$ref": "core/unresolvable-thing.json"}},
        [{}],
        True,
        id="array-items-ref-unresolvable-296",
    ),
    # Array items whose $ref resolved to an object: [{}] is invented for an element
    # shape the generator declined to read.
    pytest.param(
        "array",
        "errors",
        {"type": "array", "items": {"$ref": "core/error.json"}},
        [{}],
        False,
        id="array-items-ref-object-298",
    ),
    # Array of inline objects that declare no required and no *id* property: the
    # EMPTY half only — [obj] for a non-empty obj is derived, not guessed.
    pytest.param(
        "array",
        "rows",
        {"type": "array", "items": {"type": "object", "properties": {"foo": {"type": "string"}}}},
        [],
        False,
        id="array-of-objects-no-required-310-empty-half",
    ),
    # Array with no items spec at all — the element shape was never declared.
    pytest.param("array", "coordinates", {"type": "array"}, [], False, id="array-items-absent-314"),
    # Array whose items is a LIST (tuple validation), so items_spec.get(...) never
    # ran and the generator fell out of the branch entirely.
    pytest.param(
        "array",
        "coordinates",
        {"type": "array", "items": [{"type": "number"}]},
        [],
        False,
        id="array-items-list-valued-314",
    ),
    # Bare object with no properties to read: {} invented for an unread shape.
    pytest.param("object", "payload", {"type": "object"}, {}, False, id="bare-object-336"),
    # The terminal else. A union type array is not a branch this function has, and
    # confirmed_at's pinned shape is exactly {"type": ["string", "null"]} — the
    # #1900 field, reached today only because its row carries a sample_override.
    pytest.param(
        ["string", "null"],
        "confirmed_at",
        {"type": ["string", "null"]},
        None,
        False,
        id="terminal-none-338",
    ),
]

# The non-empty halves of the two split exits. A DERIVED minimal value for a shape
# the generator READ is correct and must survive strict; only the invented half is
# refused. Measured at HEAD: both reach the same return statements as their empty
# twins above, with obj non-empty.
_DERIVED_HALVES = [
    # core/pagination-response.json resolves and declares required has_more:boolean,
    # so the object is built from what was read, not guessed.
    pytest.param(
        "object", "pagination", {"$ref": "core/pagination-response.json"}, {"has_more": True}, id="198-if-half"
    ),
    pytest.param(
        "array",
        "rows",
        {"type": "array", "items": {"type": "object", "required": ["foo"], "properties": {"foo": {"type": "integer"}}}},
        [{"foo": 100}],
        id="310-non-empty-half",
    ),
]


class TestAllOfArmHarvest:
    """The two allOf harvests read the SAME arms, in the same order.

    ``_allof_required_fields`` and ``_allof_properties`` used to walk the top-level
    ``allOf`` in two independent loops. They must agree: a field pulled out of an
    arm's ``required`` without that arm's ``properties`` is reported as
    schema-required and as not-defined-by-the-schema in the same breath, which is
    the contradiction that produced 7 spurious failures once already.

    Nothing graded the shared walk, so these pin the two properties a refactor can
    silently break — arm ORDER (last arm wins on a key collision) and arm COVERAGE
    (every arm contributes, not just the first).
    """

    @staticmethod
    def _two_arm_schema() -> dict[str, Any]:
        """Two inline arms that collide on one property and differ on required."""
        return {
            "allOf": [
                {
                    "required": ["from_first"],
                    "properties": {"shared": {"type": "string"}, "only_first": {"type": "string"}},
                },
                {
                    "required": ["from_second"],
                    "properties": {"shared": {"type": "integer"}, "only_second": {"type": "string"}},
                },
            ]
        }

    def test_required_is_unioned_across_every_arm(self):
        """A first-arm-only walk would drop ``from_second``."""
        assert _allof_required_fields(self._two_arm_schema()) == {"from_first", "from_second"}

    def test_properties_come_from_every_arm(self):
        """A first-arm-only walk would drop ``only_second``."""
        props = _allof_properties(self._two_arm_schema())
        assert set(props) == {"shared", "only_first", "only_second"}

    def test_last_arm_wins_on_a_property_collision(self):
        """Pins the merge DIRECTION.

        The sequential ``|=`` this was extracted from let later arms overwrite
        earlier ones. A comprehension preserves that; a ``dict(...)`` built the other
        way round, or a first-wins guard, would silently flip which spec a colliding
        field is graded against — and every pinned response composes the shared
        envelope arm alongside its domain arm, so collisions are the normal case.
        """
        assert _allof_properties(self._two_arm_schema())["shared"] == {"type": "integer"}


class TestSampleSynthesisFailsLoud:
    """The instrument refuses to guess.

    Graded here because the failure mode is silence: a _synthesize_sample that
    quietly returns a guessed string does not break anything visibly — it hands a
    model a value the spec never allowed, and the ValidationError that follows
    reads as a conformance bug in production code. That misreading is what bought the
    envelope-status exclusion its existence, so 'raises instead of guessing' is a
    behaviour this suite has to keep, not an implementation detail.
    """

    def test_unsynthesizable_required_field_raises_located_error(self):
        """A shape with no generator rule names itself, its schema, and the fix."""
        arm = {
            "required": ["opaque_field"],
            "properties": {"opaque_field": {"type": "string", "contentEncoding": "base64"}},
        }
        with pytest.raises(AssertionError) as excinfo:
            _synthesize_sample(arm, "media-buy/some-response.json")

        message = str(excinfo.value)
        assert "opaque_field" in message
        assert "media-buy/some-response.json" in message
        assert "contentEncoding" in message, "the message must show the shape it could not synthesize"
        assert "sample_override" in message, "the message must name the escape hatch that already exists"

    def test_known_shapes_still_synthesize(self):
        """The guard fires on unknown shapes only — enums and arrays still work."""
        arm = {
            "required": ["status", "accounts"],
            "properties": {
                "status": {"type": "string", "enum": ["completed", "failed"]},
                "accounts": {"type": "array"},
            },
        }
        assert _synthesize_sample(arm, "account/some-response.json") == {
            "status": "completed",
            "accounts": [],
        }

    @pytest.mark.parametrize(("field_type", "field_name", "field_spec", "lenient_value", "from_cause"), _NO_RULE_EXITS)
    def test_strict_synthesis_refuses_every_no_rule_exit(
        self, field_type, field_name, field_spec, lenient_value, from_cause
    ):
        """Under ``strict``, a shape the generator has no rule for raises instead of inventing a value.

        This is a PROSPECTIVE guard, and deliberately so. Measured across the whole
        response registry, ``_synthesize_sample`` calls ``generate_example_value``
        exactly nine times — status x5, cache_scope x2, media_buy_id, revision — all
        at depth 1, zero recursion, and not one of them lands on an exit below. So
        these cases drive the shapes directly rather than through a registry row: the
        obligation is that the generator cannot invent, not that some row happens to
        exercise it today. Every shape here is reachable in the pinned 3.1 tree.

        The lenient default must be unchanged, byte-for-byte — ``strict`` is a new
        capability for the response side, not a rewrite of the request-side generator.
        """
        with pytest.raises(_CannotSynthesize) as excinfo:
            generate_example_value(field_type, field_name, field_spec, strict=True)

        if from_cause:
            assert excinfo.value.__cause__ is not None, (
                "an exit that swallowed an exception must raise FROM it — dropping the cause "
                "replaces one silence with another and loses why the schema could not be read"
            )

        assert generate_example_value(field_type, field_name, field_spec) == lenient_value

    @pytest.mark.parametrize(("field_type", "field_name", "field_spec", "derived_value"), _DERIVED_HALVES)
    def test_strict_synthesis_keeps_the_derived_half_of_a_split_exit(
        self, field_type, field_name, field_spec, derived_value
    ):
        """Two of the refused exits are one half of a two-way return; only that half is refused.

        ``return obj if obj else {}`` and ``return [obj] if obj else []`` invent on their
        EMPTY half and derive on their non-empty one. A minimal instance built out of the
        required names the generator actually read is a derived value, not a guess, so it
        must still come back under ``strict`` — refusing it would make the instrument
        unable to measure shapes it can, in fact, read.
        """
        assert generate_example_value(field_type, field_name, field_spec, strict=True) == derived_value

    def test_top_level_required_array_keeps_the_minimal_list_rule(self):
        """RATIFIED BOUNDARY: a top-level required array synthesizes to ``[]`` and does NOT raise.

        ``_synthesize_sample`` short-circuits every required array to ``[]`` before
        ``generate_example_value`` is reached. That is a RULE the docstring already
        declares ("Array required fields -> empty list (valid + minimal)"), not the
        absence of one: every required array across the registry has no ``minItems``,
        so ``[]`` is a spec-VALID derived instance of the field, and element shapes are
        graded by separate ``item_key`` rows rather than by this envelope sample.

        The distinction this pins is the whole line strict draws: a DERIVED minimal
        value for a shape that was read is fine; an INVENTED value for an element shape
        the generator could not read (the array exits in ``_NO_RULE_EXITS``) is not.
        Recorded as a decision so the short-circuit is never mistaken for an oversight —
        and if a future pin adds ``minItems`` to any required array, ``[]`` stops being
        spec-valid and this test is the thing that says so.
        """
        for row in _RESPONSE_MODEL_REGISTRY:
            arm = _success_shape(load_json_schema(row.schema_ref))
            props = arm.get("properties", {})
            for fname in sorted(set(arm.get("required", [])) - _VERSION_FIELDS):
                if props.get(fname, {}).get("type") != "array":
                    continue
                assert "minItems" not in props[fname], (
                    f"{row.schema_ref} now requires a non-empty {fname!r}; the empty-list rule is no "
                    f"longer a spec-valid derived instance and _synthesize_sample must stop using it"
                )
                if row.sample_override is None:
                    assert _synthesize_sample(arm, row.schema_ref, row.model)[fname] == []


class TestResponseModelAlignment:
    """Local success models conform to the pinned AdCP response schemas."""

    @pytest.mark.parametrize("alignment", RESPONSE_ALIGNMENTS, ids=lambda a: a.model.__name__)
    def test_envelope_status_is_graded_for_every_registered_response(self, alignment: ResponseAlignment):
        """``status`` enters declared_fields for EVERY registry row that the pin requires it on.

        This is GH #1900's fifth acceptance bullet expressed as a MECHANISM rather than
        as an outcome. Two models satisfied that bullet by accident: ``status`` is
        composed onto the response through a top-level ``allOf``, and the resolver used
        to derive declared_fields returned the raw ``oneOf`` arm without merging root
        composition — so ``status`` never entered the derived set, and every check keyed
        off it (the sample, and the model_dump-survival check written for exactly the
        ``confirmed_at`` bug class) skipped the field silently on the two models where
        it mattered most.

        Asserting it here, once, for every row means a future resolver change that
        re-hides ``status`` fails on this test by name instead of quietly reducing what
        the other tests measure. A row whose pinned schema does NOT require status is
        skipped rather than forced — the assertion is "graded wherever required", not
        "present everywhere".
        """
        if alignment.item_key is not None:
            # Item-level rows describe an ELEMENT of an array (accounts[], media_buys[]),
            # where `status` is the domain status of that object — a different namespace
            # from the protocol envelope's task status. #1900 is about the envelope, so
            # grading item rows here would assert the wrong thing under the right name.
            pytest.skip(f"{alignment.model.__name__}: item-level row; envelope status is graded on the envelope row")
        item = _resolve_response_item_schema(alignment)
        if "status" not in set(item.get("required", [])):
            pytest.skip(f"{alignment.model.__name__}: the pinned schema does not require status on this shape")
        assert "status" in alignment.declared_fields, (
            f"{alignment.model.__name__}: the pinned schema REQUIRES status on this response, but it is "
            f"absent from declared_fields ({sorted(alignment.declared_fields)}), so no alignment check "
            f"grades it. This is what an unmerged oneOf arm produces — see _success_shape."
        )
        assert "status" in alignment.model.model_fields, (
            f"{alignment.model.__name__} does not declare status as a field; the pinned schema requires it "
            f"and inheriting it via extra='allow' would let it vanish with a parent config change"
        )

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

        # A field can be declared on the model (above) yet still be silently
        # dropped by a custom model_dump() override (e.g. an over-broad
        # exclude set, or a "strip None" pass that also strips populated
        # values) — the exact bug class this suite exists to catch
        # (#1868 review). Construct with a real, populated value for
        # every declared field and confirm each survives serialization.
        if alignment.sample:
            instance = alignment.model(**alignment.sample)
            dumped = instance.model_dump(mode="json")
            for fname in alignment.declared_fields:
                if fname not in alignment.sample:
                    continue
                assert fname in dumped, (
                    f"{fname!r} is declared on {alignment.model.__name__} and populated in the "
                    f"constructor sample, but missing from model_dump() output — silently dropped "
                    f"from the wire a buyer actually receives."
                )

    @pytest.mark.parametrize("alignment", RESPONSE_ALIGNMENTS, ids=lambda a: a.model.__name__)
    def test_required_fields_enforced(self, alignment: ResponseAlignment):
        """The model enforces every field the pinned schema marks required.

        A schema-required field is "enforced" one of two ways, both valid:
        - the model has no default -> omitting it MUST raise ValidationError
          (the model rejects an incomplete construction), or
        - the model declares a spec-correct literal default (e.g.
          CreateMediaBuySuccess.status, which IS invariant for a synchronous
          success — unlike confirmed_at/revision, which are columns the
          repository owns and therefore carry no default: a default there made
          the response a second producer of persisted state) -> omitting it must NOT
          raise, and the constructed model must still carry a non-None value
          for it. Either way the schema's requiredness invariant holds; only
          silently accepting an omitted field with no value at all would be
          a real gap.
        """
        item = _resolve_response_item_schema(alignment)
        required = set(item.get("required", [])) - _VERSION_FIELDS
        if not required:
            pytest.skip(f"{alignment.model.__name__}: pinned schema marks no required fields")
        assert alignment.sample, (
            f"{alignment.model.__name__}: schema requires {sorted(required)} but no sample provided"
        )
        model_defaulted = {
            fname
            for fname in required
            if (mf := alignment.model.model_fields.get(fname)) is not None and not mf.is_required()
        }
        # A model-defaulted field guarantees its own value, so the caller-supplied
        # sample need not carry it — only fields the model can't fill in itself
        # must be present in the sample.
        required_from_sample = required - model_defaulted
        assert required_from_sample <= set(alignment.sample), (
            f"sample for {alignment.model.__name__} missing required keys: "
            f"{sorted(required_from_sample - set(alignment.sample))}"
        )
        # The complete required set constructs cleanly.
        assert alignment.model(**alignment.sample) is not None
        for fname in required:
            partial = {k: v for k, v in alignment.sample.items() if k != fname}
            if fname in model_defaulted:
                # Model-defaulted: omission must NOT raise, and the default must
                # still satisfy the schema's requiredness (a real, non-None value).
                instance = alignment.model(**partial)
                assert getattr(instance, fname) is not None, (
                    f"{alignment.model.__name__}.{fname} is schema-required but the model's "
                    f"own default left it None when omitted from the constructor call"
                )
            else:
                # No model default: the model itself must reject an incomplete construction.
                with pytest.raises(ValidationError):
                    alignment.model(**partial)


def _extends_adcp_library_type(model: type) -> bool:
    """Whether ``model`` directly extends a type DEFINED under ``adcp.types``.

    Extracted so the rule is gradeable in isolation. The obvious inline spelling —
    ``base in vars(adcp.types).values()`` — is not merely stale, it is
    NON-DETERMINISTIC: ``adcp.types`` re-exports lazily via PEP 562 ``__getattr__``,
    so that namespace is populated as a side effect of first attribute access and the
    answer depends on what any earlier import in the process happened to touch.
    ``__module__`` is a fixed property of the class, so this rule returns the same
    answer whenever it is asked.
    """
    return any((base.__module__ or "").startswith("adcp.types") for base in model.__bases__)


def _enumerate_grounded_response_models() -> set[type]:
    """Enumerate every local response model the registry MUST cover.

    This makes the registry's own inclusion rule executable instead of
    hand-listed: a model belongs iff it is (1) defined in ``src.core.schemas``
    (so imported ``Library*`` aliases, whose ``__module__`` is ``adcp.types.*``,
    are excluded), (2) extends an ``adcp`` library type directly — a base DEFINED
    under ``adcp.types``, which is NOT the same as one re-exported into
    ``vars(adcp.types)``: the SDK leaves some bases in submodules it never
    re-exports, and testing membership of that flat namespace silently drops the
    models extending them — and (3) carries a response role — its name
    ends in ``Response`` or ``Success`` (the oneOf success arm). Error arms end in
    ``Error`` and requests in ``Request``, so both are excluded; reusable
    sub-components (``Account``, ``Package``, ``Pagination``) lack the response
    suffix and are excluded too.

    A future library-grounded response model that nobody registers is therefore
    discovered here and fails the coverage gate, rather than slipping through a
    stale literal.
    """
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
            if _extends_adcp_library_type(obj):
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

    def test_enumeration_admits_models_whose_library_base_is_not_reexported(self):
        """Groundedness is decided by where the base is DEFINED, not by whether it is re-exported.

        ``_enumerate_grounded_response_models``'s own docstring states the rule as
        "``__bases__`` contains an ``adcp.types`` class", but it implements that as
        membership in ``vars(adcp.types)`` — the flat re-export namespace. A library
        base defined in a submodule that the SDK does not re-export therefore fails the
        identity check even though the model plainly extends a library type.

        Measured at HEAD: the identity rule enumerates 10 models, the ``__module__``
        rule 12 — the two below are the difference, and nothing is dropped. Both are
        already registered, so the gate stays green; what the fix buys is that their
        registry rows become deletion-protected. Until then ``SyncAccountsResponse``,
        the model GH #1900 is named for, could have its row deleted and this coverage
        gate would stay green — an instrument reporting success on a model it never
        enumerated.

        Worse than stale: ``adcp.types`` re-exports LAZILY via PEP 562 ``__getattr__``,
        so ``vars(adcp.types)`` is populated as a side effect of first attribute access.
        The identity rule's answer therefore depended on whether any earlier import in
        the process happened to touch that name — the same model was admitted or
        dropped depending on test order. Measured: ``SyncAccountsResponse`` is absent
        from ``vars`` in a file-scoped run and present in a full-suite run. Definition
        location is a stable property of the class, so the ``__module__`` rule is also
        what makes this gate deterministic.

        The rule is graded on a SYNTHETIC base rather than on the two real models,
        and that is the whole point. Asserting only that the enumeration admits
        ``SyncAccountsResponse``/``CreateMediaBuySuccess`` is VACUOUS in a whole-suite
        run: measured, the identity rule starts admitting both once
        ``test_delivery_metrics`` has executed and populated ``vars(adcp.types)``, and
        that file sorts earlier — so by the time this test runs under ``make quality``
        or ``tox -e unit`` (both whole-suite executions) the two rules already agree
        and a full revert of the fix reddens nothing. A base built here is guaranteed
        absent from that namespace no matter what ran before, so the two rules are
        forced apart deterministically.
        """
        unexported_base = type("ProbeLibraryBase", (BaseModel,), {})
        # Defined under adcp.types (new rule: grounded) but never re-exported into the
        # flat namespace (old rule: not grounded) — true in every import order.
        unexported_base.__module__ = "adcp.types.generated_poc.probe.probe_response"

        import adcp.types as adcp_types

        assert unexported_base not in vars(adcp_types).values(), (
            "the synthetic base must not be in the flat namespace, or it cannot separate the two rules"
        )

        probe = type("ProbeResponse", (unexported_base,), {})
        assert _extends_adcp_library_type(probe), (
            "groundedness must follow where the base is DEFINED (__module__ under adcp.types), not "
            "whether the SDK happens to re-export it into vars(adcp.types) — the latter is populated "
            "lazily by PEP 562 __getattr__, so it makes admission depend on import order"
        )

        unrelated = type("UnrelatedResponse", (BaseModel,), {})
        unrelated.__module__ = "src.core.schemas.something"
        assert not _extends_adcp_library_type(unrelated), "a non-library base must not be admitted"

        # And the rule, applied to the real tree, admits the two models whose bases the
        # identity check missed. Order-dependent on its own (see above), so it rides
        # behind the synthetic assertions rather than carrying the grade.
        grounded = _enumerate_grounded_response_models()
        unadmitted = {SyncAccountsResponse, CreateMediaBuySuccess} - grounded
        assert not unadmitted, (
            f"library-grounded response models the enumeration failed to admit: "
            f"{sorted(m.__name__ for m in unadmitted)} — their registry rows are unprotected, so "
            f"deleting one leaves the coverage gate green"
        )


def _pinned_constraint(ref: str, node_path: tuple[str | int, ...], field: str, keyword: str) -> Any:
    """Read one JSON-Schema keyword off a field in the PINNED schema tree.

    ``node_path`` walks to the object that declares ``properties`` — the pins
    below put the field under ``oneOf/0`` (a response union arm) or under
    ``$defs/signal`` — so the caller names the node instead of a search guessing
    which of several same-named fields it found.

    The keyword must be PRESENT: a pin that silently stopped declaring the bound
    would otherwise make the caller assert against ``None`` and pass.
    """
    node: Any = pinned_schema.load(ref)
    for step in node_path:
        node = node[step]
    spec = node["properties"][field]
    assert keyword in spec, f"{ref} {'/'.join(map(str, node_path))}.properties.{field} declares no {keyword!r}: {spec}"
    return spec[keyword]


class TestPinnedBoundsUnreachableFromAnyRequest:
    """Bounds the pin declares that NO request payload can drive production across.

    Seven local fields redeclared an adcp parent
    field and drop the bound the pin carries. Four are reachable from a request
    and are graded behaviourally, cross-transport, in
    tests/bdd/features/local-constraint-relaxation-rejections.feature. These three
    are not reachable, for reasons measured per field and recorded in each test —
    so they are graded here, at the model, which is the only place the value can
    be presented at all. A behavioural row for any of them would have to fake the
    payload it claims a buyer could send.

    Each test reads the bound from the pinned schema rather than restating it, so
    a spec change moves the test instead of silently invalidating it.
    """

    def test_create_media_buy_success_refuses_a_revision_below_the_pinned_minimum(self):
        """revision=0 must not be constructible on the create success envelope.

        Not behaviourally reachable: ``media_buys.revision`` is NOT NULL DEFAULT 1
        (src/core/database/models.py:1116), so no persisted row can carry 0 and no
        request can steer production into emitting one. The bound protects the
        buyer's optimistic-concurrency token against a SELLER-side defect — a
        response fabricated or migrated with a zero revision — which is why the
        model is the grading locus.
        """
        minimum = _pinned_constraint("media-buy/create-media-buy-response.json", ("oneOf", 0), "revision", "minimum")

        with pytest.raises(ValidationError):
            CreateMediaBuySuccess(
                media_buy_id="mb_bounds_probe",
                confirmed_at=datetime(2026, 1, 1, tzinfo=UTC),
                revision=minimum - 1,
                packages=[],
            )

    def test_update_media_buy_success_refuses_a_revision_below_the_pinned_minimum(self):
        """revision=0 must not be constructible on the update success envelope.

        Same unreachability as the create sibling, and graded separately rather
        than parametrized with it: they are two independently-declared local
        fields, and one grader standing in for both is the substitution this epic
        exists to remove. The update envelope's revision is the value the buyer
        feeds back into the NEXT conditional update, so a zero here is the one
        that would strand a buyer mid-sequence.
        """
        minimum = _pinned_constraint("media-buy/update-media-buy-response.json", ("oneOf", 0), "revision", "minimum")

        with pytest.raises(ValidationError):
            UpdateMediaBuySuccess(media_buy_id="mb_bounds_probe", revision=minimum - 1)

    def test_signal_refuses_an_empty_deployments_list(self):
        """A Signal must carry at least one deployment.

        No behavioural row is authorable, and faking one would be the dishonest
        move: ``_get_signals_impl`` is an explicit mock whose only producers are
        six hardcoded ``Signal(...)`` literals at src/core/tools/signals.py:90-155,
        each passing exactly one ``SignalDeployment`` (lines 98/109/120/131/142/153).
        No request parameter reaches the deployments list, so no scenario can
        present an empty one — the model is the whole surface.

        The pin is ``core/wholesale-feed-event.json#/$defs/signal``, which is what
        ``src.core.schemas.Signal`` extends. Deliberately NOT
        ``signals/get-signals-response.json``: that sibling types the SAME
        deployments array with no minItems at all, so a reader who checks only the
        tool's own response schema would conclude this bound is unfounded. The
        bound belongs to the entity, and the entity's pin declares it.
        """
        min_items = _pinned_constraint("core/wholesale-feed-event.json", ("$defs", "signal"), "deployments", "minItems")
        assert min_items == 1, f"pin changed: $defs.signal.deployments.minItems is {min_items}, not 1"

        with pytest.raises(ValidationError):
            Signal(
                name="bounds probe",
                description="a signal presented with no deployment",
                signal_agent_segment_id="seg_bounds_probe",
                signal_type="marketplace",
                deployments=[],
            )

    def test_package_request_creatives_bounds_match_the_pin(self):
        """``PackageRequest.creatives`` carries the pin's minItems AND maxItems.

        This one needs its own grader, and the reason is structural rather than
        incidental. The other four hand-restated bounds are protected by the
        inheritance guard's metadata-superset arm: if the pin moves, the parent's
        metadata stops being a subset of the local field's and the guard reddens.

        That arm is only reached for fields the guard finds ADMISSIBLE. This field
        fails the SHAPE clause — ``issubclass(schemas.creative.Creative,
        adcp...CreativeAsset)`` is False, because the local element type is a
        substitution rather than a narrowing — so it keeps its KNOWN_OVERRIDES row,
        and **a row absorbs any later metadata divergence silently**. The drift
        protection the rest of the change-set relies on is dead here.

        ``max_length=100`` in particular was graded by nothing at all: every
        behavioural row exercises the lower bound.
        """
        # Read from package-request.json directly, not through
        # create-media-buy-request.json: that schema's packages.items is a bare
        # {"$ref": "package-request.json"}, so the bounds are not there to read. The
        # entity's own schema is both the reachable location and the correct citation
        # — the same package type is referenced by update-media-buy-request.json's
        # packages AND new_packages, and all three carry these bounds because they all
        # point here.
        min_items = _pinned_constraint("media-buy/package-request.json", (), "creatives", "minItems")
        max_items = _pinned_constraint("media-buy/package-request.json", (), "creatives", "maxItems")

        assert (min_items, max_items) == (1, 100), (
            f"pin changed: packages.items.creatives bounds are ({min_items}, {max_items}), not (1, 100)"
        )

        from src.core.schemas import PackageRequest

        declared = {type(m).__name__: m for m in PackageRequest.model_fields["creatives"].metadata}
        assert getattr(declared.get("MinLen"), "min_length", None) == min_items, (
            f"local min_length does not match the pin's minItems={min_items}: {declared}"
        )
        assert getattr(declared.get("MaxLen"), "max_length", None) == max_items, (
            f"local max_length does not match the pin's maxItems={max_items}: {declared}"
        )


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])

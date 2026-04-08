"""Comprehensive tests for deep_strip_to_schema.

Three-pass derived test suite:
- Pass 1: Postcondition scenarios (P1-P8)
- Pass 2: Boundary analysis and invariants (INV-1 through INV-3)
- Pass 3: Integration with real tool schemas

The function recursively strips unknown properties from dicts where the
JSON Schema declares additionalProperties: false, letting TypeAdapter
accept arguments that our Pydantic models (extra='ignore') would accept.
"""

from __future__ import annotations

import pytest

from src.core.request_compat import deep_strip_to_schema

# ---------------------------------------------------------------------------
# Shared schemas
# ---------------------------------------------------------------------------

FLAT_OBJECT_STRICT = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "additionalProperties": False,
}

FLAT_OBJECT_OPEN = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
    },
    "additionalProperties": True,
}

NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "account": {"$ref": "#/$defs/AccountRef"},
        "label": {"type": "string"},
    },
    "additionalProperties": False,
    "$defs": {
        "AccountRef": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

ARRAY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

UNION_SCHEMA = {
    "type": "object",
    "properties": {
        "ref": {
            "anyOf": [
                {
                    "$ref": "#/$defs/ById",
                },
                {
                    "$ref": "#/$defs/ByName",
                },
            ],
        },
    },
    "additionalProperties": False,
    "$defs": {
        "ById": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
        },
        "ByName": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "domain": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

DEEP_NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "level1": {
            "type": "object",
            "properties": {
                "level2": {
                    "type": "object",
                    "properties": {
                        "level3": {
                            "type": "object",
                            "properties": {
                                "keep": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


# ===========================================================================
# P1: Top-level unknown fields stripped (additionalProperties: false)
# ===========================================================================


class TestP1TopLevelStripping:
    """P1: Unknown top-level fields are removed when additionalProperties is false."""

    def test_unknown_field_stripped(self):
        result = deep_strip_to_schema(
            {"name": "Alice", "age": 30, "unknown_field": "remove me"},
            FLAT_OBJECT_STRICT,
        )
        assert result == {"name": "Alice", "age": 30}
        assert "unknown_field" not in result

    def test_multiple_unknown_fields_stripped(self):
        result = deep_strip_to_schema(
            {"name": "Alice", "extra1": 1, "extra2": 2, "extra3": 3},
            FLAT_OBJECT_STRICT,
        )
        assert result == {"name": "Alice"}

    def test_known_fields_preserved(self):
        result = deep_strip_to_schema(
            {"name": "Alice", "age": 30},
            FLAT_OBJECT_STRICT,
        )
        assert result == {"name": "Alice", "age": 30}


# ===========================================================================
# P2: Nested unknown fields stripped recursively
# ===========================================================================


class TestP2NestedStripping:
    """P2: Unknown fields inside nested objects are stripped recursively."""

    def test_nested_unknown_stripped_via_ref(self):
        result = deep_strip_to_schema(
            {"account": {"account_id": "acc-1", "new_spec_field": "strip me"}, "label": "test"},
            NESTED_SCHEMA,
        )
        assert result == {"account": {"account_id": "acc-1"}, "label": "test"}

    def test_both_levels_stripped(self):
        result = deep_strip_to_schema(
            {
                "account": {"account_id": "acc-1", "extra": True},
                "label": "test",
                "top_extra": "gone",
            },
            NESTED_SCHEMA,
        )
        assert result == {"account": {"account_id": "acc-1"}, "label": "test"}


# ===========================================================================
# P3: Array items stripped
# ===========================================================================


class TestP3ArrayStripping:
    """P3: Unknown fields in array items are stripped."""

    def test_array_items_stripped(self):
        result = deep_strip_to_schema(
            {
                "items": [
                    {"id": "a", "value": 1, "extra": "gone"},
                    {"id": "b", "value": 2, "future_field": True},
                ]
            },
            ARRAY_SCHEMA,
        )
        assert result == {
            "items": [
                {"id": "a", "value": 1},
                {"id": "b", "value": 2},
            ]
        }

    def test_empty_array_preserved(self):
        result = deep_strip_to_schema({"items": []}, ARRAY_SCHEMA)
        assert result == {"items": []}

    def test_array_item_with_only_unknowns(self):
        result = deep_strip_to_schema(
            {"items": [{"extra1": 1, "extra2": 2}]},
            ARRAY_SCHEMA,
        )
        assert result == {"items": [{}]}


# ===========================================================================
# P4: Known fields preserved at all levels
# ===========================================================================


class TestP4KnownFieldsPreserved:
    """P4: Known fields are never removed at any nesting level."""

    def test_all_known_fields_preserved_flat(self):
        value = {"name": "Alice", "age": 30}
        result = deep_strip_to_schema(value, FLAT_OBJECT_STRICT)
        assert result == value

    def test_all_known_fields_preserved_nested(self):
        value = {"account": {"account_id": "acc-1"}, "label": "test"}
        result = deep_strip_to_schema(value, NESTED_SCHEMA)
        assert result == value

    def test_all_known_fields_preserved_in_array(self):
        value = {"items": [{"id": "a", "value": 1}]}
        result = deep_strip_to_schema(value, ARRAY_SCHEMA)
        assert result == value


# ===========================================================================
# P5: Primitives pass through unchanged
# ===========================================================================


class TestP5PrimitivePassthrough:
    """P5: Primitive values (str, int, float, bool, None) are never modified."""

    @pytest.mark.parametrize("value", ["hello", 42, 3.14, True, False, None])
    def test_primitive_passthrough(self, value):
        schema = {"type": "string"}  # schema type doesn't matter for passthrough
        assert deep_strip_to_schema(value, schema) == value


# ===========================================================================
# P6: anyOf/oneOf union handling
# ===========================================================================


class TestP6UnionHandling:
    """P6: anyOf unions — strip against the matching variant."""

    def test_anyof_matches_first_variant(self):
        """ById variant: has 'id', strips unknown from ById schema."""
        result = deep_strip_to_schema(
            {"ref": {"id": "123", "extra": "gone"}},
            UNION_SCHEMA,
        )
        assert result == {"ref": {"id": "123"}}

    def test_anyof_matches_second_variant(self):
        """ByName variant: has 'name' + 'domain', strips unknown from ByName schema."""
        result = deep_strip_to_schema(
            {"ref": {"name": "acme", "domain": "acme.com", "extra": "gone"}},
            UNION_SCHEMA,
        )
        assert result == {"ref": {"name": "acme", "domain": "acme.com"}}

    def test_anyof_with_null_variant(self):
        """Optional field: anyOf includes {type: null}. Non-null value strips correctly."""
        schema = {
            "type": "object",
            "properties": {
                "opt": {
                    "anyOf": [
                        {"type": "object", "properties": {"x": {"type": "integer"}}, "additionalProperties": False},
                        {"type": "null"},
                    ],
                },
            },
            "additionalProperties": False,
        }
        result = deep_strip_to_schema({"opt": {"x": 1, "extra": "gone"}}, schema)
        assert result == {"opt": {"x": 1}}

    def test_anyof_null_value_preserved(self):
        """None value with anyOf [object, null] passes through."""
        schema = {
            "type": "object",
            "properties": {
                "opt": {
                    "anyOf": [
                        {"type": "object", "properties": {"x": {"type": "integer"}}, "additionalProperties": False},
                        {"type": "null"},
                    ],
                },
            },
            "additionalProperties": False,
        }
        result = deep_strip_to_schema({"opt": None}, schema)
        assert result == {"opt": None}


# ===========================================================================
# P7: $ref resolution
# ===========================================================================


class TestP7RefResolution:
    """P7: $ref pointers are resolved correctly."""

    def test_ref_resolved_and_stripped(self):
        result = deep_strip_to_schema(
            {"account": {"account_id": "acc-1", "new_field": "strip"}, "label": "ok"},
            NESTED_SCHEMA,
        )
        assert result["account"] == {"account_id": "acc-1"}

    def test_missing_ref_passes_through(self):
        """$ref to a non-existent def — value passes through unchanged."""
        schema = {
            "type": "object",
            "properties": {
                "data": {"$ref": "#/$defs/DoesNotExist"},
            },
            "additionalProperties": False,
        }
        result = deep_strip_to_schema(
            {"data": {"anything": "goes"}},
            schema,
        )
        # Falls back to the unresolved schema (which has no properties/additionalProperties)
        # so the dict passes through
        assert result == {"data": {"anything": "goes"}}


# ===========================================================================
# P8: additionalProperties: true preserves unknowns
# ===========================================================================


class TestP8AdditionalPropertiesTrue:
    """P8: When additionalProperties is true (or absent), unknowns are preserved."""

    def test_open_schema_preserves_unknowns(self):
        result = deep_strip_to_schema(
            {"name": "Alice", "whatever": "kept"},
            FLAT_OBJECT_OPEN,
        )
        assert result == {"name": "Alice", "whatever": "kept"}

    def test_no_additional_properties_key_defaults_to_true(self):
        """Schema without additionalProperties key defaults to allowing extras."""
        schema = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        }
        result = deep_strip_to_schema({"x": 1, "y": 2}, schema)
        assert result == {"x": 1, "y": 2}


# ===========================================================================
# Boundary: Edge cases
# ===========================================================================


class TestBoundaryEdgeCases:
    """Boundary analysis: edge cases at structural limits."""

    def test_empty_dict(self):
        result = deep_strip_to_schema({}, FLAT_OBJECT_STRICT)
        assert result == {}

    def test_dict_with_only_unknowns(self):
        result = deep_strip_to_schema(
            {"a": 1, "b": 2, "c": 3},
            FLAT_OBJECT_STRICT,
        )
        assert result == {}

    def test_three_levels_deep(self):
        result = deep_strip_to_schema(
            {"level1": {"level2": {"level3": {"keep": "yes", "strip": "no"}, "strip2": "no"}, "strip3": "no"}},
            DEEP_NESTED_SCHEMA,
        )
        assert result == {"level1": {"level2": {"level3": {"keep": "yes"}}}}

    def test_schema_with_no_defs(self):
        """Schema without $defs — direct properties only."""
        result = deep_strip_to_schema(
            {"name": "Alice", "extra": "gone"},
            FLAT_OBJECT_STRICT,
        )
        assert result == {"name": "Alice"}

    def test_schema_with_no_properties(self):
        """Schema without properties key — value passes through."""
        result = deep_strip_to_schema({"a": 1}, {"type": "object"})
        assert result == {"a": 1}

    def test_non_dict_with_object_schema(self):
        """Non-dict value where schema says object — pass through (let Pydantic reject)."""
        result = deep_strip_to_schema("not a dict", FLAT_OBJECT_STRICT)
        assert result == "not a dict"

    def test_nested_array_of_arrays(self):
        """Array of arrays — inner arrays pass through if no items schema."""
        schema = {
            "type": "object",
            "properties": {
                "matrix": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
            },
            "additionalProperties": False,
        }
        result = deep_strip_to_schema({"matrix": [[1, 2], [3, 4]]}, schema)
        assert result == {"matrix": [[1, 2], [3, 4]]}


# ===========================================================================
# INV-1: Known fields are NEVER removed
# ===========================================================================


class TestInv1KnownFieldsNeverRemoved:
    """INV-1: No known field is ever lost, regardless of nesting or siblings."""

    def test_known_survives_with_many_unknowns(self):
        result = deep_strip_to_schema(
            {"name": "keep", "a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
            FLAT_OBJECT_STRICT,
        )
        assert "name" in result

    def test_nested_known_survives_with_unknowns(self):
        result = deep_strip_to_schema(
            {"account": {"account_id": "keep", "x": 1, "y": 2, "z": 3}, "label": "keep"},
            NESTED_SCHEMA,
        )
        assert result["account"]["account_id"] == "keep"
        assert result["label"] == "keep"


# ===========================================================================
# INV-2: Idempotent — strip(strip(x)) == strip(x)
# ===========================================================================


class TestInv2Idempotent:
    """INV-2: Applying deep_strip twice produces the same result as once."""

    @pytest.mark.parametrize(
        "schema",
        [
            FLAT_OBJECT_STRICT,
            NESTED_SCHEMA,
            ARRAY_SCHEMA,
            UNION_SCHEMA,
            DEEP_NESTED_SCHEMA,
        ],
    )
    def test_idempotent(self, schema):
        value = {
            "name": "Alice",
            "age": 30,
            "extra": "gone",
            "account": {"account_id": "acc", "extra": True},
            "label": "test",
            "items": [{"id": "a", "value": 1, "extra": "x"}],
            "ref": {"id": "123", "extra": "y"},
            "level1": {"level2": {"level3": {"keep": "yes", "strip": "no"}}},
        }
        once = deep_strip_to_schema(value, schema)
        twice = deep_strip_to_schema(once, schema)
        assert once == twice


# ===========================================================================
# Integration: Real-world AdCP-like schemas
# ===========================================================================


class TestRealWorldSchemas:
    """Integration tests with schemas resembling real AdCP tool parameters."""

    def test_get_products_with_unknown_nested_account_field(self):
        """Buyer sends account with a future field — stripped to known fields."""
        schema = {
            "type": "object",
            "properties": {
                "brief": {"type": "string"},
                "brand": {
                    "anyOf": [
                        {"type": "object", "properties": {"domain": {"type": "string"}}, "additionalProperties": False},
                        {"type": "null"},
                    ],
                },
                "account": {
                    "anyOf": [
                        {"$ref": "#/$defs/AccountRef1"},
                        {"$ref": "#/$defs/AccountRef2"},
                        {"type": "null"},
                    ],
                },
            },
            "additionalProperties": False,
            "$defs": {
                "AccountRef1": {
                    "type": "object",
                    "properties": {"account_id": {"type": "string"}},
                    "additionalProperties": False,
                },
                "AccountRef2": {
                    "type": "object",
                    "properties": {
                        "brand": {
                            "type": "object",
                            "properties": {"domain": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        "operator": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        }
        result = deep_strip_to_schema(
            {
                "brief": "video ads",
                "brand": {"domain": "acme.com"},
                "account": {"account_id": "acc-123", "future_field": "v4.0"},
            },
            schema,
        )
        assert result == {
            "brief": "video ads",
            "brand": {"domain": "acme.com"},
            "account": {"account_id": "acc-123"},
        }

    def test_create_media_buy_packages_with_extra_targeting_fields(self):
        """Buyer sends packages with future targeting fields — stripped."""
        schema = {
            "type": "object",
            "properties": {
                "buyer_ref": {"type": "string"},
                "packages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "budget": {
                                "type": "object",
                                "properties": {"total": {"type": "number"}},
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        result = deep_strip_to_schema(
            {
                "buyer_ref": "ref-1",
                "packages": [
                    {
                        "product_id": "prod-1",
                        "budget": {"total": 5000, "new_currency_field": "BTC"},
                        "future_targeting": {"ai_segments": ["gen-z"]},
                    },
                ],
            },
            schema,
        )
        assert result == {
            "buyer_ref": "ref-1",
            "packages": [
                {
                    "product_id": "prod-1",
                    "budget": {"total": 5000},
                },
            ],
        }

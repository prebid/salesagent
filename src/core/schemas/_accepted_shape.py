"""Reduce a parameter bag to the fields this seller's schema declares.

THE POLICY. A field our models do not declare never reaches an implementation. In development
it is a hard rejection, so a spec field we have not implemented is loud rather than silent; in
production it is dropped, so a newer buyer is served instead of refused.

WHERE IT RUNS. A ``mode="before"`` validator on ``BuyerRequest`` (``_base.py``), so every
transport gets it by CONSTRUCTING the DTO and there is no call to forget. It ran in the MCP
middleware alone before, which is why the same bytes had three meanings: dropped on MCP,
rejected on A2A/REST inside an ``additionalProperties: false`` object, and KEPT and passed to
the implementation inside one that allows extras. The last of those also reached the
idempotency digest, so a retry carrying an unknown key inside ``ext`` was answered
IDEMPOTENCY_CONFLICT instead of being replayed. Its intermediate home was
``ToolSpec.validate``, which only A2A ever reached -- see the validator's own docstring.

WHY A SCHEMA WALK AND NOT ``model_config``. Two other approaches were built and thrown away.
Walking the DATA against the model tree meant reimplementing union resolution, ``RootModel``
unwrapping and ``dict[K, V]`` detection -- pydantic's job -- and it deleted every
``account.account_id`` and every creative asset on its first run. Setting ``extra`` on the 255
reachable SDK classes made pydantic's ``__eq__`` time-dependent (``__pydantic_extra__`` is
``{}`` under ``allow`` and ``None`` under ``ignore``), so a model built before the change
compared unequal to a field-identical one built after, and tests failed by import order. The
schema is the thing that actually defines the accepted shape, so walking it needs neither.

A FREE-FORM CONTAINER KEEPS ITS CONTENTS. An object that declares no properties -- ``ext``,
``context`` -- is the schema's shape for "arbitrary data lives here". There is nothing
undeclared to remove, only contents to lose.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def deep_strip_to_schema(
    value: Any,
    schema: dict[str, Any],
    defs: dict[str, Any] | None = None,
    rejected: list[str] | None = None,
) -> Any:
    """Recursively strip fields not declared in a JSON Schema.

    Walks the value alongside its JSON Schema and removes unknown properties
    at every nesting level where additionalProperties is false. This lets
    TypeAdapter accept the cleaned arguments, deferring real validation to
    our Pydantic models (which use extra='ignore' in production).

    Args:
        value: The argument value (dict, list, or primitive).
        schema: JSON Schema for this value (from tool.parameters or a nested property).
        defs: The $defs dict from the root schema (for resolving $ref).
        rejected: Optional list to collect the RFC 6901 pointer of every key removed.
            Pass one when the caller REFUSES on a strip rather than tolerating it: a
            rejection has to name what it rejected, and the walk is the only place that
            knows. Omit it and the walk reports nothing, which is what a production
            (tolerant) strip wants.

    Returns:
        Cleaned value with unknown properties removed at strict levels.
    """
    if defs is None:
        defs = schema.get("$defs", {})

    return _strip_node(value, schema, defs, "", rejected)


def _child(path: str, key: str) -> str:
    """The RFC 6901 pointer of ``key`` inside ``path``, escaping ``~`` and ``/`` per §3."""
    return f"{path}/{key.replace('~', '~0').replace('/', '~1')}"


def _resolve_ref(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a $ref pointer to its definition."""
    ref = schema.get("$ref", "")
    # Handle #/$defs/Name format
    parts = ref.rsplit("/", 1)
    if len(parts) == 2:
        def_name = parts[1]
        if def_name in defs:
            return defs[def_name]
    return schema


def _strip_node(
    value: Any,
    schema: dict[str, Any],
    defs: dict[str, Any],
    path: str = "",
    rejected: list[str] | None = None,
) -> Any:
    """Recursive worker for deep_strip_to_schema.

    ``path`` is the RFC 6901 pointer of ``value`` within the whole document, so a removal
    can be reported where it happened rather than as a bare key name.
    """
    # Follow $ref
    if "$ref" in schema:
        schema = _resolve_ref(schema, defs)

    # anyOf / oneOf: strip against each variant, pick best match
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            variants = schema[union_key]
            # Filter out null-type variants (e.g., {"type": "null"} in Optional fields)
            real_variants = [v for v in variants if v.get("type") != "null"]
            if not real_variants:
                return value
            # Strip against each variant, pick the one whose declared properties
            # match the most input keys. This avoids variants with
            # additionalProperties: true inflating the score via unknown fields.
            best_result = value
            best_score = -1
            best_rejected: list[str] = []
            for variant in real_variants:
                try:
                    resolved = variant
                    if "$ref" in resolved:
                        resolved = _resolve_ref(resolved, defs)
                    # Each candidate collects its OWN removals: only the winning variant's
                    # are the buyer's, and appending to the shared list would report keys
                    # dropped by variants this value was never matched against.
                    candidate_rejected: list[str] = []
                    candidate = _strip_node(value, variant, defs, path, candidate_rejected)
                    # Score by how many input keys match declared properties
                    declared = set(resolved.get("properties", {}).keys())
                    score = len(declared & value.keys()) if isinstance(value, dict) else 0
                    if score > best_score:
                        best_score = score
                        best_result = candidate
                        best_rejected = candidate_rejected
                except Exception:
                    logger.debug("Schema candidate matching failed", exc_info=True)
                    continue
            if rejected is not None:
                rejected.extend(best_rejected)
            return best_result

    # allOf: value must satisfy ALL schemas. Merge declared properties from
    # all members and strip against the union of known fields.
    if "allOf" in schema:
        merged_props: dict[str, Any] = {}
        allows_additional = True
        for member in schema["allOf"]:
            resolved = member
            if "$ref" in resolved:
                resolved = _resolve_ref(resolved, defs)
            merged_props.update(resolved.get("properties", {}))
            if resolved.get("additionalProperties") is False:
                allows_additional = False
        merged_schema = {
            "type": "object",
            "properties": merged_props,
            "additionalProperties": allows_additional,
        }
        return _strip_node(value, merged_schema, defs, path, rejected)

    # Object: strip unknown properties, recurse into known ones
    if isinstance(value, dict):
        props = schema.get("properties", {})
        allows_additional = schema.get("additionalProperties", True)
        result = {}
        for k, v in value.items():
            if k in props:
                result[k] = _strip_node(v, props[k], defs, _child(path, k), rejected)
            elif allows_additional and not props:
                # A free-form container: the schema declares no properties here, so there is
                # nothing undeclared to remove -- only contents to lose. This is `ext` and
                # `context`, the shape AdCP uses for "arbitrary data lives here".
                result[k] = v
            # else: the object declares a shape and this key is not part of it. Dropped,
            # whatever `additionalProperties` says: the spec decides what a buyer MAY SEND,
            # this seller decides what it PROCESSES.
            elif rejected is not None:
                rejected.append(_child(path, k))
        return result

    # Array: recurse into items
    if isinstance(value, list) and "items" in schema:
        items_schema = schema["items"]
        return [_strip_node(item, items_schema, defs, _child(path, str(i)), rejected) for i, item in enumerate(value)]

    # Primitives (str, int, float, bool, None): pass through
    return value

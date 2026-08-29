"""Grade a success-path wire body against the pinned AdCP schema.

The transport-blind half of a "the response should be schema-valid against
<file>" oracle: it owns the framing-vs-payload split ONCE, so a step (or an
integration test) that needs it does not re-derive the envelope allowlist, the
item-subschema slice, and the validator construction inline. Schema resolution
itself lives in :mod:`tests.helpers.pinned_schema` — the single locator for the
installed SDK's schema tree; this module adds only the wire-shape knowledge
that is not in the schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tests.helpers.pinned_schema import array_item_validator_for

# Transport envelope framing keys that wrap an AdCP response object on the wire
# and are legitimately not model fields: A2A merges message/success into the
# DataPart, and the protocol envelope may add status/task_id/context_id. Any
# OTHER top-level key the model does not declare is a wire regression, not
# framing. Lives here, not in a step module, because every transport's oracle
# needs the same answer — the sibling hand-rolled subsets already in the
# integration suite ({"task_id", "context_id"}; ("message", "success")) are
# what this exists to stop multiplying.
KNOWN_ENVELOPE_KEYS = frozenset({"message", "success", "status", "task_id", "context_id"})


def assert_wire_items_schema_valid(
    wire: dict[str, Any],
    *,
    schema_ref: str,
    item_key: str,
    model: type[BaseModel],
    known_missing_required: frozenset[str] = frozenset(),
) -> None:
    """Assert *wire*'s ``item_key`` array is schema-valid, item by item.

    Two arms, because a response's top level and its payload are graded by
    different authorities:

    - **Framing.** The pinned schema sets ``additionalProperties: true`` at the
      top level (it has to — the envelope adds keys), so a regression that ADDS
      a bogus top-level key would validate clean. Reject any top-level key that
      is neither a *model* field nor known envelope framing. ``model`` is used
      for SHAPE here, never as the validation authority.
    - **Payload.** Each item is graded against the pinned schema's own item
      definition via
      :func:`~tests.helpers.pinned_schema.array_item_validator_for`.

    ``known_missing_required`` names item-level required fields production is
    known not to emit; it is checked against the schema and raises if it has
    gone stale (see the helper). Every other violation still fails loud.
    """
    allowed_top_level = set(model.model_fields) | KNOWN_ENVELOPE_KEYS
    unexpected = set(wire) - allowed_top_level
    assert not unexpected, (
        f"unexpected top-level wire keys {sorted(unexpected)} — neither "
        f"{model.__name__} fields nor known envelope framing; a wire regression "
        "may have added a bogus field"
    )

    items = wire.get(item_key)
    assert isinstance(items, list), f"wire carries no {item_key} array — got {type(items).__name__}"

    validator = array_item_validator_for(schema_ref, item_key, ignore_required=known_missing_required)
    violations = [(idx, err) for idx, item in enumerate(items) for err in validator.iter_errors(item)]
    assert not violations, f"{schema_ref} schema violations on the wire:\n" + "\n".join(
        f"  {item_key}[{idx}] at /{'/'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for idx, err in violations
    )

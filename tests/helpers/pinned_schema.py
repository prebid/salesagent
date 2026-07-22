"""Validate data against vendored ("pinned") AdCP JSON schemas, fully offline.

Single source of truth for schema-shape assertions in tests (e.g. the BDD step
"the response should be schema-valid against <file>"). Reads the committed
fixtures under ``tests/fixtures/adcp_schemas_pinned/``, pinned at
adcontextprotocol/adcp@04f59d2d5 (tag ``v3.1-04f59d2d5``). This SHA predates the
``v3.1.1`` release tag but its schema CONTENT is verified equal to the released
``dist/schemas/3.1.1/...`` (the spec version the repo targets, adcp==6.6.0), so
validations here are against current-3.1.1 content — see the content note in
``tests/fixtures/adcp_schemas_pinned/_refresh.py``. It never fetches the network —
``/schemas/latest`` drifts and would make tests non-deterministic.

``$ref`` resolution (e.g. ``/schemas/core/format-id.json``) is wired through a
``referencing.Registry`` retrieve callback that loads each referenced schema from
the same pinned tree, so nested refs validate against the frozen closure. A
missing schema (the pin moved, or a ``$ref`` is outside the vendored closure) is
a HARD FAILURE, never a silent skip — mirroring ``load_json_schema`` in
``tests/unit/test_pydantic_schema_alignment.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import referencing
from jsonschema.validators import Draft7Validator
from referencing.jsonschema import DRAFT7

_PINNED_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adcp_schemas_pinned"


def _load_by_ref(schema_ref: str) -> dict[str, Any]:
    """Load a pinned schema by its ``$id``/``$ref`` namespace path (``/schemas/...``)."""
    rel = schema_ref.split("#", 1)[0]
    if not rel.startswith("/schemas/"):
        raise AssertionError(f"Unexpected schema ref (expected '/schemas/...'): {schema_ref!r}")
    path = _PINNED_SCHEMA_DIR / rel[len("/schemas/") :]
    if not path.exists():
        raise AssertionError(
            f"Pinned schema not vendored: {schema_ref} -> {path}. "
            "Re-run tests/fixtures/adcp_schemas_pinned/_refresh.py to vendor it."
        )
    return json.loads(path.read_text())


def _resolve_filename(filename: str) -> Path:
    """Resolve a bare schema filename (e.g. ``list-creatives-response.json``) to its pinned path."""
    matches = sorted(_PINNED_SCHEMA_DIR.rglob(filename))
    if not matches:
        raise AssertionError(
            f"Pinned schema {filename!r} not found under {_PINNED_SCHEMA_DIR}. "
            "Re-run tests/fixtures/adcp_schemas_pinned/_refresh.py to vendor it."
        )
    return matches[0]


def _retrieve(uri: str) -> referencing.Resource:
    """referencing retrieve callback: resolve a ``/schemas/...`` ref from the pinned tree."""
    return DRAFT7.create_resource(_load_by_ref(uri))


def load_pinned_schema(filename: str) -> dict[str, Any]:
    """Load a pinned AdCP schema dict by bare filename (offline, from the vendored tree).

    The read-only companion to ``validate_against_pinned_schema`` — lets a test read the
    schema's own field metadata (e.g. which properties are marked "only present in webhook
    deliveries") so a hand-maintained constant can be grounded against the spec rather than
    re-typed. A missing schema is a HARD FAILURE (see ``_resolve_filename``), never a skip.
    """
    return json.loads(_resolve_filename(filename).read_text())


def validate_against_pinned_schema(filename: str, data: Any) -> None:
    """Assert *data* is schema-valid against the pinned AdCP schema *filename*.

    Raises ``AssertionError`` listing every JSON-path violation on failure.
    """
    schema = load_pinned_schema(filename)
    registry: referencing.Registry = referencing.Registry(retrieve=_retrieve)
    root_id = schema.get("$id")
    if root_id:
        registry = registry.with_resource(root_id, DRAFT7.create_resource(schema))
    validator = Draft7Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "\n".join(
            f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors
        )
        raise AssertionError(f"Response is not schema-valid against {filename}:\n{details}")

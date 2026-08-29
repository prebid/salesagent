"""Validate/load AdCP JSON schemas from the installed adcp SDK's pinned tree,
fully offline.

Single source of truth for schema-shape assertions in tests (e.g. the BDD
step "the response should be schema-valid against <file>") AND for the
Pydantic-model alignment suite's schema walking
(tests/unit/test_pydantic_schema_alignment.py). Reads the SDK's own "plain"
schema tree (``adcp/_schemas/<major.minor>/``, sibling of the SDK's
``bundled/`` tree) — never the network, and never an independently vendored
snapshot: the SDK's own installed version IS the pin (moves with
pyproject.toml's ``adcp`` version), so there is exactly one upstream pin for
every consumer that reads through this module (this module previously read
a separately vendored, independently pinned fixture tree that had already
drifted a full spec-minor behind).

The plain tree (not ``bundled/``) is deliberately the source: ``bundled/``
only physically ships 8 of the SDK's 16 top-level schema categories (no
``account/``, ``enums/``, ``governance/``, etc.) — it is a strict subset of
the plain tree, not a superset, despite being individually self-contained
per file. The plain tree's schemas use relative ``$ref``s (``../core/x.json``,
resolved against the referring file's own directory) instead of bundled's
pre-inlined local anchors; this module resolves those relative refs by
stamping every loaded schema with its own ``file://`` URI (``path.as_uri()``)
before handing it to ``jsonschema``/``referencing``. ``file://`` is a real
scheme that ``referencing``'s ``urljoin``-based resolution handles natively,
and it maps back to a path with no invented naming convention in between.

The pure resolution primitives (``schema_root``, ``normalize_ref``, ``load``,
``PinnedSchemaError``, and their private helpers) live in
``tests/helpers/adcp_pinned_schema.py`` — a stdlib-only module test code
(``src/core/version_compat.py``) also imports, so there is exactly ONE
resolution implementation shared by prod and tests, never a test-only copy
duplicated into src/. This module re-exports every one of those names and
adds only the jsonschema-validation-specific pieces on top:

- ``validator_for(ref)`` — a ready-to-use ``Draft7Validator`` with full
  ``$ref`` resolution wired, for validating a payload against a schema
  (``validate_against_pinned_schema`` is the convenience wrapper most
  callers want).
- ``array_item_validator_for(ref, item_key)`` — the same, narrowed to ONE
  array property's items, for callers that grade a response's payload
  without its transport/protocol envelope.
- ``load(ref)`` — re-exported from ``tests/helpers/adcp_pinned_schema`` — a
  single schema's raw dict, ``$ref``s left as-is, for callers that WALK the
  schema tree themselves (the alignment suite's synthetic-example
  generator) rather than validating a concrete payload. Callers that want
  to follow the refs they find should use ``load_canonicalized``, which
  rewrites them into the root-relative form ``load`` itself accepts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import referencing
from jsonschema.validators import Draft7Validator
from referencing.jsonschema import DRAFT7

from tests.helpers.adcp_pinned_schema import (
    PinnedSchemaError,
    _contained_path,
    _load_with_id,
    _resolve_and_load,
    load,
    normalize_ref,
    schema_root,
)
from tests.helpers.adcp_pinned_schema import (
    # Re-exported for tests.unit.test_pinned_schema_single_source, which reads
    # this module's own private resolution helper directly. Not used in this
    # file's body — the `as` self-alias is the standard re-export idiom that
    # tells the linter this import is intentional, not dead.
    _resolve_filename as _resolve_filename,
)

__all__ = [
    "PinnedSchemaError",
    "array_item_validator_for",
    "load",
    "load_canonicalized",
    "normalize_ref",
    "schema_root",
    "validate_against_pinned_schema",
    "validator_for",
]


def _canonicalize_refs(node: Any, *, file_dir: Path, root: Path) -> Any:
    """Recursively rewrite every "$ref" string in *node* from a path relative
    to file_dir (the schema file's own directory — the plain tree's ``$ref``
    convention) to a path relative to root (this module's root-relative
    convention, understood by ``load``/``_resolve_filename``/bare filenames)."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                target_part, _, fragment = value.partition("#")
                target = _contained_path(file_dir / target_part, what=f"$ref {value!r} (from {file_dir})")
                rel = str(target.relative_to(root)).replace("\\", "/")
                out[key] = rel + (f"#{fragment}" if fragment else "")
            else:
                out[key] = _canonicalize_refs(value, file_dir=file_dir, root=root)
        return out
    if isinstance(node, list):
        return [_canonicalize_refs(item, file_dir=file_dir, root=root) for item in node]
    return node


def load_canonicalized(ref: str) -> dict[str, Any]:
    """Load one schema's raw dict with every ``$ref`` inside it rewritten to
    be root-relative (e.g. ``"../core/duration.json"`` found in
    ``media-buy/get-products-request.json`` becomes ``"core/duration.json"``).

    For callers that walk the schema tree themselves (the alignment suite's
    synthetic-example generator) and recursively re-call ``load_canonicalized``
    on every ``$ref`` they encounter — this makes every ref they see
    resolvable the same way regardless of how deep the schema that
    contained it was nested, without threading a "current file" context
    through the walk.
    """
    path, schema = _resolve_and_load(ref)
    return _canonicalize_refs(schema, file_dir=path.parent, root=schema_root())


def _retrieve(uri: str) -> referencing.Resource:
    path = _contained_path(Path(url2pathname(urlparse(uri).path)), what=f"Schema URI {uri!r}")
    if not path.exists():
        raise PinnedSchemaError(f"Pinned schema not found: {uri} -> {path}")
    return DRAFT7.create_resource(_load_with_id(path))


def validator_for(ref: str) -> Draft7Validator:
    """A Draft7Validator for *ref* with full (relative) $ref resolution wired."""
    _, schema = _resolve_and_load(ref)
    registry: referencing.Registry = referencing.Registry(retrieve=_retrieve)
    registry = registry.with_resource(schema["$id"], DRAFT7.create_resource(schema))
    return Draft7Validator(schema, registry=registry)


def array_item_validator_for(
    ref: str,
    item_key: str,
    *,
    ignore_required: frozenset[str] = frozenset(),
) -> Draft7Validator:
    """A Draft7Validator for the ITEMS of *ref*'s ``item_key`` array property.

    For payload-level grading: a response schema's top level composes the
    protocol envelope (``core/protocol-envelope.json``, which REQUIRES
    ``status``), so validating a bare AdCP payload against the whole document
    fails on framing the payload legitimately does not carry. This narrows the
    validator to the array the caller's scenario is actually about, while
    keeping the parent document's ``$ref`` resolution: ``evolve`` carries the
    parent validator's resolver — rooted at the parent's ``file://`` ``$id`` —
    onto the narrowed schema, so the item's relative refs
    (``../core/account.json``) resolve exactly as they do in place. That
    inheritance is what makes the narrowing safe, so it is pinned by a test
    (tests/unit/test_wire_schema_oracle.py::test_cross_file_ref_resolves)
    rather than assumed.

    ``ignore_required`` drops names from the item's ``required`` list, for a
    KNOWN production gap the caller has named and accepted (expressed against
    the SCHEMA rather than by pattern-matching jsonschema's error message,
    which is presentation, not contract). Every other required field, and every
    non-required constraint, still grades. A name that is NOT required by the
    pinned schema raises: a tolerance that has silently become a no-op — the
    spec dropped the field, or it was misspelled — must fail loud so it gets
    deleted, never linger as dead permission.
    """
    schema = load(ref)
    prop = schema.get("properties", {}).get(item_key)
    if not isinstance(prop, dict) or not isinstance(prop.get("items"), dict):
        raise PinnedSchemaError(f"{ref} has no array property {item_key!r} with an object ``items`` schema")

    item_schema = dict(prop["items"])
    required = list(item_schema.get("required", []))
    if ignore_required:
        stale = sorted(set(ignore_required) - set(required))
        if stale:
            raise PinnedSchemaError(
                f"ignore_required names {stale} which {ref} does not mark required on "
                f"{item_key} items — the tolerance is a no-op; delete it."
            )
        item_schema["required"] = [name for name in required if name not in ignore_required]

    return validator_for(ref).evolve(schema=item_schema)


def validate_against_pinned_schema(filename: str, data: Any) -> None:
    """Assert *data* is schema-valid against the pinned AdCP schema *filename*.

    Raises ``AssertionError`` listing every JSON-path violation on failure.
    """
    validator = validator_for(filename)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "\n".join(
            f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors
        )
        raise AssertionError(f"Response is not schema-valid against {filename}:\n{details}")

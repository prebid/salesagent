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
adds on top the jsonschema-validation surfaces plus the pinned-enum readers
that the test-side oracles grade against:

- ``validator_for(ref)`` — a ready-to-use ``Draft7Validator`` with full
  ``$ref`` resolution wired, for validating a payload against a schema
  (``validate_against_pinned_schema`` is the convenience wrapper most
  callers want).
- ``load(ref)`` — re-exported from ``tests/helpers/adcp_pinned_schema`` — a
  single schema's raw dict, ``$ref``s left as-is, for callers that WALK the
  schema tree themselves (the alignment suite's synthetic-example
  generator) rather than validating a concrete payload. Callers that want
  to follow the refs they find should use ``load_canonicalized``, which
  rewrites them into the root-relative form ``load`` itself accepts.
- ``recovery_by_code()`` — the normative ``error-code.json`` ``enumMetadata``
  ``{code: recovery}`` map, the ONE test-side reader of that block (see its
  own docstring for why it lives here rather than in each consumer).
- ``auth_scheme_values()`` — the pinned ``enums/auth-scheme.json`` ``enum``,
  the ONE test-side reader of that enum, for the same reason.
"""

from __future__ import annotations

from functools import cache
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
    "auth_scheme_values",
    "load",
    "load_canonicalized",
    "normalize_ref",
    "recovery_by_code",
    "schema_root",
    "validate_against_pinned_schema",
    "validator_for",
]


@cache
def recovery_by_code() -> dict[str, str]:
    """``{error_code: recovery}`` from the pinned ``error-code.json`` enumMetadata.

    The ONE test-side reader of that block. The block is normative — its own
    ``$comment`` says "SDKs MUST consume this block ... the recovery
    classification embedded in that prose is normative and MUST match the value
    here" — so it is the expectation every test-side recovery oracle grades
    against, and more than one of them needs it (the recovery-conformance
    oracle, and ``envelope_assertions.assert_envelope_shape``, which refuses to
    grade a (code, recovery) pair the pin contradicts). Two independent copies
    of the same load is the copy-paste shape DRY forbids here, and a second copy
    can silently drift to a different key filter.

    Reads through this module's own ``load()``, so it stays independent of
    ``src.core.exceptions.RECOVERY_BY_WIRE_CODE``: a test-side oracle that
    imported src's table would agree with the thing it grades instead of
    grading it.

    Cached: the map is a pure function of the installed SDK's pinned tree, and
    callers hit it once per assertion. Callers share the one dict — read it,
    never mutate it.
    """
    meta = load("error-code.json")["enumMetadata"]
    return {code: entry["recovery"] for code, entry in meta.items() if isinstance(entry, dict) and "recovery" in entry}


@cache
def auth_scheme_values() -> frozenset[str]:
    """The pinned ``enums/auth-scheme.json`` ``enum`` — the wire spellings a
    webhook ``authentication.schemes`` entry may legally carry.

    The ONE test-side reader of that enum, for the same reason
    ``recovery_by_code`` is the one reader of ``enumMetadata``: the value under
    test is ``adcp.types.AuthenticationScheme``, and a test that read the
    spelling off the SDK would agree with the thing it grades instead of
    grading it. This module reads the SDK's pinned SCHEMA tree, which is
    generated from the spec rather than hand-maintained alongside the Python
    enum, so the two can disagree — and that disagreement is exactly what the
    conformance test in ``tests/unit/test_auth_scheme_pin_conformance.py``
    exists to catch.

    Cached: a pure function of the installed SDK's pinned tree.
    """
    return frozenset(load("enums/auth-scheme.json")["enum"])


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


def error_code_suggestion(code: str) -> str:
    """Pinned ``enumMetadata`` suggestion for *code* (spec pin, not production ClassVar).

    Use this in wire-oracle asserts so a ClassVar edit cannot lockstep with the
    test. Raises ``KeyError`` if the pin has no suggestion for *code*.

    The ``not entry.get("suggestion")`` arm is forward-insurance: today's pin has
    no dict entry with a falsy suggestion, but a future enum row must still fail
    loud rather than return ``None``/``""`` into buyer-facing asserts.
    """
    meta = load("error-code.json")["enumMetadata"]
    entry = meta.get(code)
    if not isinstance(entry, dict) or not entry.get("suggestion"):
        raise KeyError(f"pinned error-code.json has no suggestion for {code!r}")
    return str(entry["suggestion"])

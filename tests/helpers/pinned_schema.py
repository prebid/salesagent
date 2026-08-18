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

Two surfaces, matching the two distinct things callers need:

- ``validator_for(ref)`` — a ready-to-use ``Draft7Validator`` with full
  ``$ref`` resolution wired, for validating a payload against a schema
  (``validate_against_pinned_schema`` is the convenience wrapper most
  callers want).
- ``load(ref)`` — a single schema's raw dict, ``$ref``s left as-is, for
  callers that WALK the schema tree themselves (the alignment suite's
  synthetic-example generator) rather than validating a concrete payload.
  Callers that want to follow the refs they find should use
  ``load_canonicalized``, which rewrites them into the root-relative form
  ``load`` itself accepts.

A missing schema (the SDK layout changed, or a ``$ref`` is outside the
resolvable tree) is a HARD FAILURE — ``PinnedSchemaError``, never a silent
skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import referencing
from jsonschema.validators import Draft7Validator
from referencing.jsonschema import DRAFT7

# The SDK's bundled/ subtree pre-inlines a partial (8-of-16-category) mirror
# of the plain tree under the same filenames — searching it too would make
# every mirrored bare filename ambiguous (e.g. "list-creatives-response.json"
# exists at both creative/list-creatives-response.json and
# bundled/creative/list-creatives-response.json). Bare-filename lookups are
# scoped to the plain tree only; bundled/ is never read by this module.
_EXCLUDED_TOP_LEVEL_DIR = "bundled"


class PinnedSchemaError(Exception):
    """A pinned schema could not be resolved or loaded.

    One type for every "the instrument is broken or the ref is unresolvable"
    failure in this module, so callers can catch resolution failure without
    also catching an assertion (which in a test process means "a payload
    violated the contract" — a completely different outcome).
    """


def schema_root() -> Path:
    """The installed adcp SDK's schema tree for the pinned spec version.

    The SDK stores schemas under ``adcp/_schemas/<major.minor>/`` (e.g. the
    3.1.1 spec lives in ``_schemas/3.1/``; its ``index.json`` carries the full
    ``adcp_version``).
    """
    import adcp

    spec_version = adcp.get_adcp_spec_version()
    major_minor = ".".join(spec_version.split(".")[:2])
    root = Path(adcp.__file__).parent / "_schemas" / major_minor
    if not root.is_dir():
        raise PinnedSchemaError(
            f"Installed adcp SDK (spec {spec_version}) has no schema tree at {root} — "
            "the SDK layout changed; update schema_root()."
        )
    return root


def normalize_ref(ref: str) -> str:
    """The one place a caller-supplied schema ref becomes a resolvable one.

    The single accepted form is the one the SDK index itself uses: a
    category-qualified path relative to the version root
    (``media-buy/get-products-request.json``), optionally with a ``#fragment``,
    which is stripped. Everything else — an absolute URL, a site-rooted
    ``/schemas/…`` path, a traversal — is a ``PinnedSchemaError``.

    Two implementations of this used to exist with DIFFERENT rules, and fed
    the version-free form the repo uses internally they disagreed: one ate the
    category segment as if it were a version and survived only because the
    fallback rglob happened to find the file anyway. Rejecting rather than
    rewriting is the point — a ref naming a version or a live registry means
    the caller believes it is validating against something other than the pin,
    and quietly redirecting it hides that the version it named was ignored.
    """
    stripped = ref.split("#", 1)[0]
    if not stripped or "://" in stripped or stripped.startswith(("/", "..")):
        raise PinnedSchemaError(f"Cannot resolve schema reference {ref!r} against the pinned SDK schema tree")
    return stripped


def _contained_path(candidate: Path, *, what: str) -> Path:
    """Resolve *candidate* and assert it stays inside the pinned schema tree.

    The single containment check for this module. A ref can embed traversal
    (``"media-buy/../../../../etc/hosts"``), and probing an UNRESOLVED path
    lets the OS follow the ``..`` segments — so resolution and the check have
    to happen together, here, once, rather than at each of the call sites that
    used to spell it out in three different shapes with three different
    messages.
    """
    resolved = candidate.resolve()
    if not resolved.is_relative_to(schema_root().resolve()):
        raise PinnedSchemaError(f"{what} escapes the pinned SDK schema tree: {resolved}")
    return resolved


def _resolve_filename(filename: str) -> Path:
    """Resolve a bare or category-qualified schema filename to its path.

    A bare filename (``"list-creatives-response.json"``) is searched across
    the plain schema tree (``bundled/`` excluded — see module docstring). If
    that search is still ambiguous (a true same-basename collision within
    the plain tree itself, e.g. ``core/error.json`` vs
    ``trusted-match/error.json``), this raises rather than silently picking
    one — pass a category-qualified ref (``"core/error.json"``) instead.
    """
    root = schema_root()
    if "/" in filename:
        path = _contained_path(root / filename, what=f"Schema ref {filename!r}")
        if not path.exists():
            raise PinnedSchemaError(f"Pinned schema not found: {filename} -> {path}")
        return path

    matches = sorted(p for p in root.rglob(filename) if _EXCLUDED_TOP_LEVEL_DIR not in p.relative_to(root).parts)
    if not matches:
        raise PinnedSchemaError(f"Pinned schema {filename!r} not found under {root}.")
    if len(matches) > 1:
        rels = [str(m.relative_to(root)) for m in matches]
        raise PinnedSchemaError(
            f"Pinned schema filename {filename!r} is ambiguous ({rels}) — pass a "
            f"category-qualified ref (e.g. {rels[0]!r}) instead of a bare filename."
        )
    return matches[0]


def _load_with_id(path: Path) -> dict[str, Any]:
    """Load a schema file, stamping its own ``file://`` URI as ``$id`` so its
    relative ``$ref``s (and any ``$ref``s INTO it from a sibling schema)
    resolve deterministically."""
    schema = json.loads(path.read_text())
    return {**schema, "$id": path.as_uri()}


def _resolve_and_load(ref: str) -> tuple[Path, dict[str, Any]]:
    """Resolve ref to its path and load it (with its ``$id``) in one call."""
    path = _resolve_filename(ref)
    return path, _load_with_id(path)


def load(ref: str) -> dict[str, Any]:
    """Load one schema's raw dict (bare or category-qualified filename).

    $refs inside the returned dict are left as-is (relative, e.g.
    ``"../core/duration.json"``) — this is for callers that walk the schema
    tree themselves. Use ``load_canonicalized`` if you intend to follow them.
    """
    _, schema = _resolve_and_load(ref)
    return schema


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

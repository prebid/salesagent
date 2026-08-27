"""Validate/load AdCP JSON schemas from the pinned trees, fully offline.

Single source of truth for schema-shape assertions in tests (e.g. the BDD
step "the response should be schema-valid against <file>") AND for the
Pydantic-model alignment suite's schema walking
(tests/unit/test_pydantic_schema_alignment.py).

TWO pinned sources, selected by the ref itself
----------------------------------------------
1. **The installed SDK tree** (``adcp/_schemas/<major.minor>/``) — the default,
   selected by every ref that does NOT name a version
   (``media-buy/get-products-request.json``). It carries SCHEMA SHAPE for the
   whole protocol, and the SDK's own installed version IS the pin (moves with
   pyproject.toml's ``adcp`` version), so there is exactly one upstream pin for
   every shape consumer that reads through this module (this module previously
   read a separately vendored, independently pinned fixture tree that had
   already drifted a full spec-minor behind).

2. **The vendored, version-namespaced tree**
   (``tests/fixtures/adcp_schemas_pinned/<major.minor.patch>/``) — selected ONLY
   by a ref that explicitly names that version (``3.1.1/adagents.json``). These
   are the upstream spec repo's own documents, fetched VERBATIM at git tag
   ``v3.1.1`` from ``dist/schemas`` (see the fixture tree's ``_refresh.py``,
   root set 2). They are NOT interchangeable with (1)'s copies: measured over
   the 73 vendored files, 0 are byte-identical to the SDK's, 44 differ
   cosmetically (``$ref`` path form) and **29 differ STRUCTURALLY** — including
   ``adagents.json``, ``brand.json`` and ``core/authorized-agent-base.json``,
   the trust-root documents where ``signing_keys[]`` lives.

   CLAUDE.md's spec-grounding gate is why the vendored copies win for those
   documents: the authority for protocol behavior is the spec repo at the
   pinned tag; the installed SDK — whose schema tree is a generated artifact —
   is a CROSS-CHECK, not the authority. A producer graded against the SDK's
   derived copy would be graded against something the spec never said.

The two sources use two DIFFERENT internal ``$ref`` conventions (the SDK's
plain tree uses file-relative refs; the tag tree uses site-rooted
``/schemas/<version>/…`` refs), which is exactly what ``_PinnedSource``
parameterizes — resolution, containment and canonicalization each have ONE
implementation, taking the source as an argument. Two implementations of ref
resolution with different rules used to exist here and silently disagreed (see
``normalize_ref``); do not reintroduce that.

For source (1), the plain tree (not ``bundled/``) is deliberately the source: ``bundled/``
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

A missing schema (the SDK layout changed, a version-prefixed ref names a tree
that was never vendored, or a ``$ref`` is outside the resolvable tree) is a
HARD FAILURE — ``PinnedSchemaError``, never a silent skip and never a fallback
onto the other source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple
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

# Root of the vendored, version-namespaced trees (source 2 in the module
# docstring). Each immediate child named <major>.<minor>.<patch> is one tag's
# verbatim document set.
_VENDORED_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adcp_schemas_pinned"

# The ONLY prefix that selects a vendored tree: an explicit spec version.
_VERSION_PREFIX = re.compile(r"^(\d+\.\d+\.\d+)/(.+)$")

# The namespace the vendored documents' own ``$id``/``$ref`` values live in
# (``/schemas/3.1.1/core/agent-signing-key.json``).
_SITE_ROOT_PREFIX = "/schemas/"


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


class _PinnedSource(NamedTuple):
    """One pinned schema tree, and everything that differs between the two.

    Every resolution/containment/canonicalization step below takes one of
    these instead of hardcoding a root, so each rule has exactly ONE
    implementation rather than a per-source copy.

    - ``label`` — names the tree in error messages.
    - ``root`` — the containment boundary; a ref's remainder is joined onto it.
    - ``ref_base`` — what a ``load()``-able (root-relative) ref is expressed
      against. Identical to ``root`` for the SDK tree; the PARENT of the
      version directory for a vendored tree, so a canonicalized ref keeps the
      ``3.1.1/`` prefix that selects that tree back.
    - ``uri_prefix`` — ``None`` means "stamp the file's own ``file://`` URI as
      ``$id``" (the SDK plain tree's file-relative ``$ref``s resolve by urljoin
      against it). A string means the tree's documents carry site-rooted
      ``$id``/``$ref``s in that namespace, which are then the registry keys
      verbatim — the vendored tag tree's case.
    """

    label: str
    root: Path
    ref_base: Path
    uri_prefix: str | None

    def uri_for(self, path: Path) -> str:
        """The ``$id`` / registry key for *path* under this source's convention."""
        if self.uri_prefix is None:
            return path.as_uri()
        return self.uri_prefix + path.resolve().relative_to(self.ref_base.resolve()).as_posix()


def _sdk_source() -> _PinnedSource:
    root = schema_root()
    return _PinnedSource(label="SDK", root=root, ref_base=root, uri_prefix=None)


def _vendored_source(version: str) -> _PinnedSource:
    """The vendored tree for *version*, or a hard failure naming what is missing.

    Never falls back to the SDK tree: a ref that named a version and silently
    got the SDK's (structurally different — see module docstring) copy instead
    would be the exact "validating against something other than the pin"
    failure this module exists to prevent.
    """
    root = _VENDORED_DIR / version
    if not root.is_dir():
        raise PinnedSchemaError(
            f"No vendored AdCP schema tree for spec version {version!r}: {root} does not exist. "
            "Vendor it with `uv run python -m tests.fixtures.adcp_schemas_pinned._refresh`, or drop the version "
            "prefix to resolve the ref against the installed SDK's tree."
        )
    return _PinnedSource(label=f"vendored {version}", root=root, ref_base=_VENDORED_DIR, uri_prefix=_SITE_ROOT_PREFIX)


def _select_source(ref: str) -> tuple[_PinnedSource, str]:
    """Pick the pinned tree a ref names, plus the path within that tree.

    An explicit ``<major>.<minor>.<patch>/`` prefix — and ONLY that — selects
    the vendored tree for that version; every other ref resolves against the
    installed SDK's tree.
    """
    match = _VERSION_PREFIX.match(ref)
    if match:
        return _vendored_source(match.group(1)), match.group(2)
    return _sdk_source(), ref


def normalize_ref(ref: str) -> str:
    """The one place a caller-supplied schema ref becomes a resolvable one.

    Two accepted forms, one per pinned source (see the module docstring): the
    form the SDK index itself uses — a category-qualified path relative to the
    version root (``media-buy/get-products-request.json``) — and that same form
    prefixed with an explicit vendored spec version
    (``3.1.1/core/authorized-agent-base.json``), which selects the vendored
    tag-verbatim tree for that version and nothing else. A ``#fragment`` is
    stripped. Everything else — an absolute URL, a site-rooted ``/schemas/…``
    path, a traversal — is a ``PinnedSchemaError``.

    Two implementations of this used to exist with DIFFERENT rules, and fed
    the version-free form the repo uses internally they disagreed: one ate the
    category segment as if it were a version and survived only because the
    fallback rglob happened to find the file anyway. Rejecting rather than
    rewriting is still the point: a ref naming a version in any OTHER shape (a
    site-rooted ``/schemas/3.1.1/…``, a live registry URL) means the caller
    believes it is validating against something other than either pin, and
    quietly redirecting it would hide that the version it named was ignored.
    The explicit prefix is legal precisely because it is NOT redirected — it
    resolves against that version's vendored tree, or it fails.
    """
    stripped = ref.split("#", 1)[0]
    if not stripped or "://" in stripped or stripped.startswith(("/", "..")):
        raise PinnedSchemaError(f"Cannot resolve schema reference {ref!r} against the pinned schema trees")
    return stripped


def _contained_path(candidate: Path, *, source: _PinnedSource, what: str) -> Path:
    """Resolve *candidate* and assert it stays inside *source*'s tree.

    The single containment check for this module, always against whichever
    tree the ref itself selected. A ref can embed traversal
    (``"media-buy/../../../../etc/hosts"``), and probing an UNRESOLVED path
    lets the OS follow the ``..`` segments — so resolution and the check have
    to happen together, here, once, rather than at each of the call sites that
    used to spell it out in three different shapes with three different
    messages.
    """
    resolved = candidate.resolve()
    if not resolved.is_relative_to(source.root.resolve()):
        raise PinnedSchemaError(f"{what} escapes the pinned {source.label} schema tree: {resolved}")
    return resolved


def _resolve_ref(ref: str) -> tuple[_PinnedSource, Path]:
    """Resolve *ref* to the pinned source it selects and the file within it.

    A bare filename (``"list-creatives-response.json"``, or the remainder left
    by a version prefix, ``"3.1.1/brand.json"`` -> ``"brand.json"``) is searched
    across the selected tree (``bundled/`` excluded — see module docstring). If
    that search is ambiguous (a true same-basename collision within the tree
    itself, e.g. ``core/error.json`` vs ``trusted-match/error.json``), this
    raises rather than silently picking one — pass a category-qualified ref
    (``"core/error.json"``) instead.
    """
    source, rel = _select_source(ref)
    root = source.root
    if "/" in rel:
        path = _contained_path(root / rel, source=source, what=f"Schema ref {ref!r}")
        if not path.exists():
            raise PinnedSchemaError(f"Pinned schema not found: {ref} -> {path}")
        return source, path

    matches = sorted(p for p in root.rglob(rel) if _EXCLUDED_TOP_LEVEL_DIR not in p.relative_to(root).parts)
    if not matches:
        raise PinnedSchemaError(f"Pinned schema {ref!r} not found under {root}.")
    if len(matches) > 1:
        rels = [str(m.relative_to(root)) for m in matches]
        raise PinnedSchemaError(
            f"Pinned schema filename {ref!r} is ambiguous ({rels}) — pass a "
            f"category-qualified ref (e.g. {rels[0]!r}) instead of a bare filename."
        )
    return source, matches[0]


def _resolve_filename(filename: str) -> Path:
    """The path *filename* resolves to, in whichever pinned tree it selects."""
    return _resolve_ref(filename)[1]


def _load_with_id(path: Path, source: _PinnedSource) -> dict[str, Any]:
    """Load a schema file, stamping the ``$id`` its own tree's convention gives
    it (``source.uri_for``) so its ``$ref``s — and any ``$ref``s INTO it from a
    sibling schema — resolve deterministically."""
    schema = json.loads(path.read_text())
    return {**schema, "$id": source.uri_for(path)}


def _resolve_and_load(ref: str) -> tuple[_PinnedSource, Path, dict[str, Any]]:
    """Resolve ref to its source and path, and load it (with its ``$id``), in one call."""
    source, path = _resolve_ref(ref)
    return source, path, _load_with_id(path, source)


def load(ref: str) -> dict[str, Any]:
    """Load one schema's raw dict (bare, category-qualified, or version-prefixed).

    $refs inside the returned dict are left as-is (relative, e.g.
    ``"../core/duration.json"``, or site-rooted for the vendored tree) — this
    is for callers that walk the schema tree themselves. Use
    ``load_canonicalized`` if you intend to follow them.
    """
    _, _, schema = _resolve_and_load(ref)
    return schema


def _ref_target(target_part: str, *, file_dir: Path, source: _PinnedSource, what: str) -> tuple[_PinnedSource, Path]:
    """The pinned source and file a non-fragment ``$ref`` points at, under either convention.

    Site-rooted (``/schemas/<version>/…``, the vendored tag tree's own form)
    resolves against that version's vendored tree — the same explicit-version
    selection ``_select_source`` makes, never a fallback. Anything else is a
    path relative to the referring file's own directory (the SDK plain tree's
    form) and stays inside *source*.
    """
    if target_part.startswith(_SITE_ROOT_PREFIX):
        return _site_rooted_target(target_part, what=what)
    return source, _contained_path(file_dir / target_part, source=source, what=what)


def _site_rooted_target(uri_path: str, *, what: str) -> tuple[_PinnedSource, Path]:
    """Map a site-rooted ``/schemas/…`` ref onto a vendored tree.

    Only the version-namespaced form is resolvable: the version segment is what
    names a pinned tree. A version-free ``/schemas/core/x.json`` is upstream's
    OTHER (SHA-pinned) namespace, which this module deliberately does not read
    — resolving it would silently grade against a different pin.
    """
    match = _VERSION_PREFIX.match(uri_path[len(_SITE_ROOT_PREFIX) :])
    if not match:
        raise PinnedSchemaError(
            f"{what}: a site-rooted ref is resolvable only when it names a vendored spec version "
            f"({_SITE_ROOT_PREFIX}<major>.<minor>.<patch>/...); got {uri_path!r}"
        )
    source = _vendored_source(match.group(1))
    return source, _contained_path(source.root / match.group(2), source=source, what=what)


def _canonicalize_refs(node: Any, *, file_dir: Path, source: _PinnedSource) -> Any:
    """Recursively rewrite every "$ref" string in *node* from its tree's own
    convention (a path relative to file_dir, the schema file's own directory,
    for the SDK plain tree; a site-rooted ``/schemas/<version>/…`` path for the
    vendored tree) to a path relative to ``source.ref_base`` — this module's
    root-relative convention, understood by ``load``/``_resolve_ref``/bare
    filenames, and carrying the version prefix that selects the vendored tree
    back."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                target_part, _, fragment = value.partition("#")
                target_source, target = _ref_target(
                    target_part, file_dir=file_dir, source=source, what=f"$ref {value!r} (from {file_dir})"
                )
                rel = target.relative_to(target_source.ref_base.resolve()).as_posix()
                out[key] = rel + (f"#{fragment}" if fragment else "")
            else:
                out[key] = _canonicalize_refs(value, file_dir=file_dir, source=source)
        return out
    if isinstance(node, list):
        return [_canonicalize_refs(item, file_dir=file_dir, source=source) for item in node]
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
    source, path, schema = _resolve_and_load(ref)
    return _canonicalize_refs(schema, file_dir=path.parent, source=source)


def _retrieve(uri: str) -> referencing.Resource:
    """``referencing``'s retrieve callback — one URI convention per pinned source.

    SDK tree: file-relative ``$ref``s, resolved by urljoin against the
    ``file://`` URI ``_load_with_id`` stamps, so the URI names a real
    filesystem path. Vendored tree: upstream's own site-rooted
    ``/schemas/<version>/…`` refs, which ARE the registry keys verbatim (that
    is why the vendored documents keep their own ``$id`` — see
    ``_PinnedSource.uri_prefix``).
    """
    if uri.startswith(_SITE_ROOT_PREFIX):
        source, path = _site_rooted_target(uri, what=f"Schema URI {uri!r}")
    else:
        source = _sdk_source()
        path = _contained_path(Path(url2pathname(urlparse(uri).path)), source=source, what=f"Schema URI {uri!r}")
    if not path.exists():
        raise PinnedSchemaError(f"Pinned schema not found: {uri} -> {path}")
    return DRAFT7.create_resource(_load_with_id(path, source))


def validator_for(ref: str) -> Draft7Validator:
    """A Draft7Validator for *ref* with full $ref resolution wired (relative for
    the SDK tree, site-rooted for a vendored one)."""
    _, _, schema = _resolve_and_load(ref)
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

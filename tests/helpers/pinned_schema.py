"""Validate/load AdCP JSON schemas from the pinned trees, fully offline.

Single source of truth for schema-shape assertions in tests (e.g. the BDD
step "the response should be schema-valid against <file>") AND for the
callers that WALK the schema tree themselves rather than validating a payload
(``tests/unit/test_adcp_contract.py``, via ``load_canonicalized``). Never the
network.

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

The version segment in a version-prefixed ref is never written literally at a
call site: callers spell ``f"{EXPECTED_SPEC_VERSION}/brand.json"``, reading the
one pin constant in ``tests/helpers/adcp_pin.py``, so this module adds no
second place the spec version is recorded.

The two sources use two DIFFERENT internal ``$ref`` conventions (the SDK's
plain tree uses file-relative refs; the tag tree uses site-rooted
``/schemas/<version>/…`` refs), which is exactly what ``_PinnedSource``
parameterizes — containment, ``$id`` stamping and canonicalization each have
ONE implementation, taking the source as an argument. Two implementations of
ref resolution with different rules used to exist here and silently disagreed
(see ``normalize_ref``); do not reintroduce that.

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

Where the pieces live
---------------------
The pure, stdlib-only resolution primitives for source (1) — ``schema_root``,
``normalize_ref``, ``PinnedSchemaError``, and the SDK tree's file search and
file read — live in ``tests/helpers/adcp_pinned_schema.py``, so a caller that
only needs to LOCATE a pinned schema does not pull in the
``jsonschema``/``referencing`` dependency stack, and so there is exactly ONE
SDK-tree resolution implementation and never a second copy. This module
re-exports those names and adds, on top of them, the jsonschema-validation
pieces, the source (2) layer, and the pinned-enum readers the test-side
oracles grade against:

- ``validator_for(ref)`` — a ready-to-use ``Draft7Validator`` with full
  ``$ref`` resolution wired, for validating a payload against a schema
  (``validate_against_pinned_schema`` is the convenience wrapper most
  callers want).
- ``load(ref)`` — a single schema's raw dict, ``$ref``s left as-is, for
  callers that WALK the schema tree themselves (``test_adcp_contract``, the
  UC002 enum step) rather than validating a concrete payload.
  Callers that want to follow the refs they find should use
  ``load_canonicalized``, which rewrites them into the root-relative form
  ``load`` itself accepts. This module's ``load`` is the two-source one; the
  extracted module's same-named function reads source (1) only.
- ``recovery_by_code()`` — the normative ``error-code.json`` ``enumMetadata``
  ``{code: recovery}`` map, the ONE test-side reader of that block (see its
  own docstring for why it lives here rather than in each consumer).
- ``auth_scheme_values()`` — the pinned ``enums/auth-scheme.json`` ``enum``,
  the ONE test-side reader of that enum, for the same reason.

Both enum readers name no version, so they resolve against source (1), the
installed SDK's tree — the shape/enum pin — exactly as they did before the
vendored source existed.

Resolution of a source (1) ref is DELEGATED to the extracted module
(``_resolve_in_sdk_tree``), never copied, so the SDK search rule the
single-source guard grades (``tests/unit/test_pinned_schema_single_source.py``)
has exactly one implementation. The vendored trees need that same search
against a different root, and the extracted resolver hardcodes the SDK root and
takes no root argument — ``_resolve_in_tree`` below is that rule expressed once
for any root, and it is the natural home for the SDK branch too if the
extracted module ever grows a root parameter.

A missing schema (the SDK layout changed, a version-prefixed ref names a tree
that was never vendored, or a ``$ref`` is outside the resolvable tree) is a
HARD FAILURE — ``PinnedSchemaError``, never a silent skip and never a fallback
onto the other source.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse
from urllib.request import url2pathname

import referencing
from jsonschema.validators import Draft7Validator
from referencing.jsonschema import DRAFT7

from tests.helpers.adcp_pinned_schema import (
    _EXCLUDED_TOP_LEVEL_DIRS,
    PinnedSchemaError,
    normalize_ref,
    schema_root,
)
from tests.helpers.adcp_pinned_schema import (
    # The ONE file read + ``$id`` stamp, reused for both trees (see
    # ``_load_with_id``), and the ONE SDK-tree file search (see
    # ``_resolve_ref``). Aliased rather than shadowed so the two-source
    # wrappers below can keep the unprefixed names.
    _load_with_id as _load_with_file_uri_id,
)
from tests.helpers.adcp_pinned_schema import (
    _resolve_filename as _resolve_in_sdk_tree,
)
from tests.helpers.adcp_pinned_schema import (
    installed_spec_version as _installed_spec_version,
)

__all__ = [
    "PinnedSchemaError",
    "auth_scheme_values",
    "load",
    "load_canonicalized",
    "normalize_ref",
    "recovery_by_code",
    "schema_root",
    "tree_ref_for_index_ref",
    "validate_against_pinned_schema",
    "validator_for",
]

# Root of the vendored, version-namespaced trees (source 2 in the module
# docstring). Each immediate child named <major>.<minor>.<patch> is one tag's
# verbatim document set.
_VENDORED_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adcp_schemas_pinned"

# The ONLY prefix that selects a vendored tree: an explicit spec version.
_VERSION_PREFIX = re.compile(r"^(\d+\.\d+\.\d+)/(.+)$")

# The namespace the vendored documents' own ``$id``/``$ref`` values live in
# (``/schemas/3.1.1/core/agent-signing-key.json``).
_SITE_ROOT_PREFIX = "/schemas/"

#: The origin upstream stamps into ``$id`` and ``$ref`` from AdCP 3.2 on. Before 3.2 it
#: wrote the bare site-rooted path; from 3.2 it writes the same path behind this origin,
#: which is why both spellings have to resolve to the same vendored file.
_SITE_ORIGIN = "https://adcontextprotocol.org"


def _site_rooted_path(uri: str) -> str | None:
    """*uri*'s site-rooted ``/schemas/…`` path, or ``None`` if it has none.

    THE ROUTING DECISION, in one place, made on the PARSED PATH rather than on the raw
    string. AdCP 3.2 writes ``$id`` and ``$ref`` as absolute URLs where 3.1.1 wrote the
    bare path, so ``uri.startswith("/schemas/")`` -- what both call sites used to ask --
    is False for every 3.2 ref. Those refs then fell through to the SDK branch, where
    ``Path(url2pathname(urlparse(uri).path))`` is the ABSOLUTE ``/schemas/3.2.1/...`` and
    :meth:`_PinnedSource.contained` correctly refused it as outside the SDK tree. The
    guard was right; the routing that reached it was not.

    THE ORIGIN IS CHECKED, not ignored. Routing on the path alone would make
    ``https://evil.example/schemas/3.2.1/core/error.json`` resolve into the vendored tree
    -- a foreign document naming our pinned namespace and being served our bytes. Only
    upstream's own origin, or no origin at all, is site-rooted here; anything else keeps
    falling through to the SDK branch and is refused there as it always was.

    Not fixed by normalizing at vendor time instead: that would edit upstream's own bytes,
    which is exactly what the SHA-256 pin guard on ``enums/error-code.json`` exists to
    detect, and the vendoring is deliberately byte-faithful.
    """
    if uri.startswith(_SITE_ROOT_PREFIX):
        return uri
    parsed = urlparse(uri)
    if not parsed.scheme and not parsed.netloc:
        return None
    if f"{parsed.scheme}://{parsed.netloc}" != _SITE_ORIGIN:
        return None
    return parsed.path if parsed.path.startswith(_SITE_ROOT_PREFIX) else None


def tree_ref_for_index_ref(ref: str) -> str:
    """Upstream's ``index.json`` ``$ref``, as a ref this module's resolvers accept.

    The index changed spelling at the pin boundary, and nothing was taught to read
    it. Measured on the installed trees:

    ====  ============  ==========  ===============================================
    pin   ``$ref``s     absolute    the ``sync-creatives`` response entry
    ====  ============  ==========  ===============================================
    3.1   437           0  (0%)     ``creative/sync-creatives-response.json``
    3.2   633           633 (100%)  ``https://adcontextprotocol.org/schemas/3.2.1/creative/sync-creatives-response.json``
    ====  ============  ==========  ===============================================

    This is the same move :func:`_resolve_site_rooted` already records for the
    generated tree's own 1314 internal ``$ref``s — 3.2 put them behind upstream's
    origin, naming the spec version. It happened to the index too.

    A tree-relative ref (the 3.1 form, and the app tree's) is returned unchanged, so
    this is identity for every ref that already resolved.

    THE VERSION IS VERIFIED, NOT STRIPPED, and that is the whole point of doing this
    here rather than relaxing :func:`normalize_ref`. That function refuses a ref
    carrying a version precisely because "quietly redirecting it hides that the
    version it named was ignored"; keeping the refusal and narrowing it to a
    MISMATCH preserves the guarantee while accepting upstream's new spelling. The
    comparison is against the installed SDK's spec version (``3.2.1``), NOT the
    schema directory it lives in (``3.2``) — a check against the directory would
    pass today and go quiet at the next patch release, which is the failure this
    docstring exists to prevent.

    The origin is not re-checked here: :func:`_site_rooted_path` owns that rule, so
    ``https://evil.example/schemas/3.2.1/core/error.json`` returns ``None`` there and
    falls through unchanged, to be refused downstream. One origin rule, one place.

    Measured, because "falls through" is only safe if something catches it — and WHICH
    thing catches it depends on the caller, so both were checked:

    - through :class:`~tests.helpers.adcp_schema_validator.AdCPSchemaValidator`, which
      runs this and then :func:`normalize_ref`, the ``"://"`` refusal fires:
      ``Cannot resolve schema reference 'https://evil.example/…'``;
    - through :func:`load` directly, which skips ``normalize_ref``, it reaches
      :func:`_resolve_filename` and dies as ``not found under any of: …``.

    Refused on both paths. The second message names containment rather than origin,
    which is worth knowing before debugging one.
    """
    path = _site_rooted_path(ref)
    if path is None:
        return ref

    match = _VERSION_PREFIX.match(path[len(_SITE_ROOT_PREFIX) :])
    if not match:
        raise PinnedSchemaError(
            f"Index ref {ref!r} is site-rooted but names no spec version "
            f"({_SITE_ROOT_PREFIX}<major>.<minor>.<patch>/...), so it selects no pinned tree"
        )

    named, remainder = match.group(1), match.group(2)
    pinned = _installed_spec_version()
    if named != pinned:
        raise PinnedSchemaError(
            f"Index ref {ref!r} names spec version {named!r} but the installed pin is "
            f"{pinned!r}. Resolving it would grade against a version the ref did not name."
        )
    return remainder


class _PinnedSource(NamedTuple):
    """One pinned schema tree, and everything that differs between the two.

    Every containment/``$id``/canonicalization step below takes one of these
    instead of hardcoding a root, so each rule has exactly ONE implementation
    rather than a per-source copy.

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

    def resolved_file(self, candidate: Path, *, what: str) -> Path:
        """Resolve *candidate*, assert it stays inside this source's tree, and
        assert the file is THERE.

        The single resolution check for this module, always against whichever
        tree the ref itself selected. A ref can embed traversal
        (``"media-buy/../../../../etc/hosts"``), and probing an UNRESOLVED path
        lets the OS follow the ``..`` segments — so resolution and the check
        have to happen together, here, once, rather than at each of the call
        sites that used to spell it out in three different shapes with three
        different messages.

        EXISTENCE IS THE SECOND HALF, and leaving it out made a whole class of
        defect silent. This was ``contained()``, which checked containment
        alone; two of its four callers then checked ``exists()`` by hand and two
        did not. The two that did not are the pair ``load_canonicalized`` walks,
        so a ref that resolved INSIDE the tree to a file that was not there came
        back as a successful resolution, and
        ``load_canonicalized("media-buy/get-products-response.json")`` returned
        a document whose ``$ref``s named files that do not exist. Nothing failed
        until some later reader followed one, at which point the error named the
        reader rather than the ref. A resolution that cannot be loaded is not a
        resolution, so it fails here — and the name says both rules, because a
        method called ``contained`` that also asserts existence is two rules
        wearing one name.
        """
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise PinnedSchemaError(f"{what} escapes the pinned {self.label} schema tree: {resolved}")
        if not resolved.is_file():
            raise PinnedSchemaError(f"{what}: pinned schema not found in the {self.label} tree -> {resolved}")
        return resolved


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


def _resolve_in_tree(source: _PinnedSource, rel: str, ref: str) -> Path:
    """The file *rel* names inside *source*'s tree, or a hard failure.

    The same rule the extracted module applies to the SDK tree, expressed once
    for an arbitrary root: a category-qualified path is joined and must exist;
    a bare filename is searched (``bundled/`` excluded — see module docstring),
    and a true same-basename collision raises rather than silently picking one.

    Only the vendored trees route through here — the SDK branch of
    ``_resolve_ref`` delegates to ``_resolve_in_sdk_tree`` so that rule is not
    duplicated — because the extracted, stdlib-only resolver hardcodes the SDK
    root and takes no root argument.
    """
    root = source.root
    if "/" in rel:
        return source.resolved_file(root / rel, what=f"Schema ref {ref!r}")

    matches = sorted(p for p in root.rglob(rel) if not (_EXCLUDED_TOP_LEVEL_DIRS & set(p.relative_to(root).parts)))
    if not matches:
        raise PinnedSchemaError(f"Pinned schema {ref!r} not found under {root}.")
    if len(matches) > 1:
        rels = [str(m.relative_to(root)) for m in matches]
        raise PinnedSchemaError(
            f"Pinned schema filename {ref!r} is ambiguous ({rels}) — pass a "
            f"category-qualified ref (e.g. {rels[0]!r}) instead of a bare filename."
        )
    return matches[0]


def _resolve_ref(ref: str) -> tuple[_PinnedSource, Path]:
    """Resolve *ref* to the pinned source it selects and the file within it.

    An explicit ``<major>.<minor>.<patch>/`` prefix — and ONLY that — selects
    the vendored tree for that version (``"3.1.1/brand.json"`` -> the vendored
    3.1.1 tree's ``brand.json``); every other ref resolves against the installed
    SDK's tree, through the extracted module's own resolver.
    """
    match = _VERSION_PREFIX.match(ref)
    if match:
        source = _vendored_source(match.group(1))
        return source, _resolve_in_tree(source, match.group(2), ref)
    return _sdk_source(), _resolve_in_sdk_tree(ref)


def _resolve_filename(filename: str) -> Path:
    """The path *filename* resolves to, in whichever pinned tree it selects."""
    return _resolve_ref(filename)[1]


def _load_with_id(path: Path, source: _PinnedSource) -> dict[str, Any]:
    """Load a schema file, stamping the ``$id`` its own tree's convention gives
    it (``source.uri_for``) so its ``$ref``s — and any ``$ref``s INTO it from a
    sibling schema — resolve deterministically.

    The read itself is the extracted module's — the ONE place a pinned schema
    file is opened. That loader stamps the ``file://`` form, which IS
    ``uri_for`` for the SDK source; re-stamping is what gives a vendored
    document its site-rooted ``$id`` instead.
    """
    return {**_load_with_file_uri_id(path), "$id": source.uri_for(path)}


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
    ``src.core.errors.codes.CODE_TABLE``: a test-side oracle that
    imported src's table would agree with the thing it grades instead of
    grading it. The version-free ref resolves against source (1), the installed
    SDK's tree.

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


def _ref_target(target_part: str, *, file_dir: Path, source: _PinnedSource, what: str) -> tuple[_PinnedSource, Path]:
    """The pinned source and file a non-fragment ``$ref`` points at, under either convention.

    Site-rooted (``/schemas/<version>/…``) resolves in the tree that holds the
    REFERRING document — see :func:`_site_rooted_target`. Anything else is a
    path relative to the referring file's own directory (the form the SDK plain
    tree used before 3.2) and stays inside *source*.
    """
    site_rooted = _site_rooted_path(target_part)
    if site_rooted is not None:
        return _site_rooted_target(site_rooted, source=source, what=what)
    return source, source.resolved_file(file_dir / target_part, what=what)


def _site_rooted_target(uri_path: str, *, source: _PinnedSource, what: str) -> tuple[_PinnedSource, Path]:
    """Map a site-rooted ``/schemas/<version>/…`` ref onto a tree, choosing the
    tree by the REFERRING document's *source*.

    THE REFERRER DECIDES, because from AdCP 3.2 the URI cannot. Until 3.2 the
    two pinned trees spelled their refs differently — the vendored tag tree
    site-rooted, the SDK plain tree relative to the referring file — so the ref
    alone said which tree it meant. 3.2 moved the SDK's generated tree onto the
    SAME absolute site-rooted form, in upstream's own origin, naming the spec
    version: all 1314 of its internal ``$ref``s, with no ``$id`` of its own. The
    two namespaces collapsed into one, and a rule that reads only the URI then
    sends every SDK-internal ref to the vendored tree — a deliberately partial
    closure of upstream's documents — where all but 112 of them are not present.

    Resolving in the referrer's own tree is not a guess. Measured at this pin,
    BOTH trees are CLOSED under self-reference and NEITHER has a single ref the
    other would have to satisfy: the SDK tree's 1314 absolute targets all exist
    inside it, the vendored tree's 110 all exist inside it, cross-tree refs are
    zero in both directions. So this costs no reachable document, and it leaves
    ``_vendored_source``'s rule — never serve the SDK's structurally different
    copy for a ref that named a version — enforced BY CONSTRUCTION rather than by
    a check a later reader can relax.

    Not fixed by rewriting the refs at load time instead: that falsifies
    :func:`load`'s own contract that ``$ref``s come back as upstream wrote them,
    and it is a softer form of the vendor-time normalization this module already
    refuses, the SHA-256 pin on ``enums/error-code.json`` existing precisely to
    detect edits to upstream's bytes.

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
    version, rest = match.group(1), match.group(2)
    if source.uri_prefix is None:
        # The SDK tree: its site-rooted refs are SELF-references, so the version
        # they name must be the version THIS tree is. The tree is installed as
        # <major>.<minor> and its refs carry the full <major>.<minor>.<patch>, so
        # the comparison is on the maintenance line. A 3.1.1 ref found inside a
        # 3.2 tree is a real anomaly and is refused rather than silently served.
        if version.rsplit(".", 1)[0] != source.root.name:
            raise PinnedSchemaError(
                f"{what}: the {source.label} tree is pinned at {source.root.name} but this ref names "
                f"spec version {version}. A tree's own refs are self-references; a ref naming another "
                "version is not resolvable here."
            )
        return source, source.resolved_file(source.root / rest, what=what)
    target = _vendored_source(version)
    return target, target.resolved_file(target.root / rest, what=what)


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

    For callers that walk the schema tree themselves (``test_adcp_contract``,
    the UC002 enum step) and recursively re-call ``load_canonicalized``
    on every ``$ref`` they encounter — this makes every ref they see
    resolvable the same way regardless of how deep the schema that
    contained it was nested, without threading a "current file" context
    through the walk.
    """
    source, path, schema = _resolve_and_load(ref)
    return _canonicalize_refs(schema, file_dir=path.parent, source=source)


def _retrieve_in(source: _PinnedSource) -> Callable[[str], referencing.Resource]:
    """``referencing``'s retrieve callback, BOUND to the tree that is asking.

    ``referencing``'s callback signature is fixed at ``(uri) -> Resource``, so
    the referring document's source cannot be threaded through the call — and
    from AdCP 3.2 the URI alone no longer says which tree a site-rooted ref
    means (see :func:`_site_rooted_target`). A single registry over both trees
    therefore has one key namespace that both write into and no way to tell them
    apart. So each source gets its OWN registry, built here: the source is
    captured at :func:`validator_for` time, where it is already resolved, and
    every ref inside that document resolves in that document's tree.

    This is why the two trees cannot bleed into one another at all, rather than
    being kept apart by a test somewhere. A vendored document physically cannot
    reach the SDK's structurally different copy of the same filename, which is
    the failure ``_vendored_source``'s docstring is written against.

    Within a tree there are two spellings and both arrive here. A site-rooted
    URI — upstream's own ``/schemas/<version>/…``, in either the bare-path form
    3.1.1 wrote or the absolute form 3.2 adopted — routes through
    :func:`_site_rooted_path`. Anything else is a ``file://`` URI, urljoined
    against the one ``_load_with_id`` stamps, naming a real filesystem path.
    """

    def retrieve(uri: str) -> referencing.Resource:
        what = f"Schema URI {uri!r}"
        site_rooted = _site_rooted_path(uri)
        if site_rooted is not None:
            target, path = _site_rooted_target(site_rooted, source=source, what=what)
        else:
            target = source
            path = source.resolved_file(Path(url2pathname(urlparse(uri).path)), what=what)
        return DRAFT7.create_resource(_load_with_id(path, target))

    return retrieve


def validator_for(ref: str) -> Draft7Validator:
    """A Draft7Validator for *ref* with full $ref resolution wired (relative for
    the SDK tree, site-rooted for a vendored one).

    *ref* may carry a JSON-pointer fragment naming a subschema —
    ``"media-buy/create-media-buy-response.json#/oneOf/0"`` validates against
    that ONE branch of a branching response. The fragment is split off before
    the ref selects its source, so a version-prefixed ref takes a pointer the
    same way (``"3.1.1/adagents.json#/oneOf/1"``). The
    registry is still built from the whole file and keyed on its ``$id``, so
    the branch's own ``$ref``s resolve exactly as they do when the whole
    document is validated.
    """
    file_ref, _, pointer = ref.partition("#")
    source, _, schema = _resolve_and_load(file_ref)
    # The registry is per-SOURCE, not shared: `_resolve_and_load` has already
    # decided which tree this document came from, and binding it here is what
    # lets `_retrieve_in` resolve the document's refs in that same tree. The
    # source used to be discarded on this line.
    registry: referencing.Registry = referencing.Registry(retrieve=_retrieve_in(source))
    registry = registry.with_resource(schema["$id"], DRAFT7.create_resource(schema))

    target = schema
    if pointer:
        for token in (t for t in pointer.split("/") if t):
            key = int(token) if token.isdigit() else token.replace("~1", "/").replace("~0", "~")
            try:
                target = target[key]  # type: ignore[index]
            except (KeyError, IndexError, TypeError) as exc:
                raise PinnedSchemaError(f"Schema ref {ref!r}: no such subschema at {pointer!r}") from exc
        # The subschema inherits the document's identity so its relative refs
        # resolve; without this the branch is an anonymous fragment and every
        # "../core/*.json" inside it is unresolvable.
        target = {"$id": schema["$id"], **target}

    return Draft7Validator(target, registry=registry)


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

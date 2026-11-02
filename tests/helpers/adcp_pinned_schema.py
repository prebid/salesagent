"""Pure, stdlib-only resolution primitives for the AdCP pinned JSON schema tree
shipped inside the installed ``adcp`` SDK.

This module is the DEPENDENCY-FREE half of what used to be a single module
(``tests/helpers/pinned_schema.py``). It carries only path/file resolution — no
``jsonschema``/``referencing`` import — so a caller that needs to locate and read
a pinned schema does not pull in the validation-only dependency stack.

It lives under ``tests/helpers/`` and has no consumer under ``src/``. No module
under ``src/`` imports anything from ``tests/``, and one that did would break the
layering this repository enforces. The split is real and useful, but it is a
dependency split, not a production/test split.

``tests/helpers/pinned_schema.py`` re-exports every name here and adds the
jsonschema-validation-specific pieces (``validator_for``,
``validate_against_pinned_schema``, ``load_canonicalized``) on top — so there
remains exactly ONE physical resolution implementation, this one.

The SDK stores schemas under ``adcp/_schemas/<major.minor>/`` (e.g. the
3.1.1 spec lives in ``_schemas/3.1/``; its ``index.json`` carries the full
``adcp_version``) — never the network, and never an independently vendored
snapshot: the SDK's own installed version IS the pin (moves with
pyproject.toml's ``adcp`` version).

A missing schema (the SDK layout changed, or a ``$ref`` is outside the
resolvable tree) is a HARD FAILURE — ``PinnedSchemaError``, never a silent
skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def app_schema_root() -> Path:
    """The repo's own schema tree, for tools the pinned spec does not describe.

    A seller may expose more tools than the standard. ``complete_task`` is the
    live case: registered, dispatchable on every transport, and absent from the
    pinned 3.1 tree entirely. Its schemas live in ``schemas/`` at the repo root,
    written to the same conventions as the SDK's, so nothing downstream needs to
    know which root answered.

    See ``schemas/README.md`` for the resolution order and what happens when the
    spec catches up.
    """
    return Path(__file__).resolve().parents[2] / "schemas"


def schema_roots() -> tuple[Path, ...]:
    """Every root a schema may come from, PINNED FIRST.

    Order is the precedence: the spec's own definition of a contract always wins
    over ours. A name present in both is not resolved by preference but by
    raising — see :func:`_resolve_filename`.
    """
    roots = [schema_root()]
    app = app_schema_root()
    if app.is_dir():
        roots.append(app)
    return tuple(roots)


def _contained_path(candidate: Path, *, what: str) -> Path:
    """Resolve *candidate* and assert it stays inside one of the schema roots.

    The single containment check for this module. A ref can embed traversal
    (``"media-buy/../../../../etc/hosts"``), and probing an UNRESOLVED path
    lets the OS follow the ``..`` segments — so resolution and the check have
    to happen together, here, once, rather than at each of the call sites that
    used to spell it out in three different shapes with three different
    messages.
    """
    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in schema_roots()):
        raise PinnedSchemaError(f"{what} escapes the schema trees: {resolved}")
    return resolved


def _resolve_filename(filename: str) -> Path:
    """Resolve a bare or category-qualified schema filename to its path.

    A bare filename (``"list-creatives-response.json"``) is searched across
    the plain schema tree (``bundled/`` excluded — see module docstring). If
    that search is still ambiguous (a true same-basename collision within
    the plain tree itself, e.g. ``core/error.json`` vs
    ``trusted-match/error.json``), this raises rather than silently picking
    one — pass a category-qualified ref (``"core/error.json"``) instead.

    TWO ROOTS, PINNED FIRST. A name found in both is not resolved by
    precedence — it raises, because two live definitions of one contract is the
    condition the app tree exists to avoid, and the day the spec ships a schema
    we wrote ourselves the right move is to delete ours, not to shadow it. See
    ``schemas/README.md``.
    """
    hits: list[Path] = []
    for root in schema_roots():
        if "/" in filename:
            candidate = _contained_path(root / filename, what=f"Schema ref {filename!r}")
            if candidate.exists():
                hits.append(candidate)
            continue

        matches = sorted(p for p in root.rglob(filename) if _EXCLUDED_TOP_LEVEL_DIR not in p.relative_to(root).parts)
        if len(matches) > 1:
            rels = [str(m.relative_to(root)) for m in matches]
            raise PinnedSchemaError(
                f"Schema filename {filename!r} is ambiguous ({rels}) — pass a "
                f"category-qualified ref (e.g. {rels[0]!r}) instead of a bare filename."
            )
        hits.extend(matches)

    if not hits:
        roots = ", ".join(str(r) for r in schema_roots())
        raise PinnedSchemaError(f"Schema {filename!r} not found under any of: {roots}")
    if len(hits) > 1:
        where = ", ".join(str(h) for h in hits)
        raise PinnedSchemaError(
            f"Schema {filename!r} exists in the pinned tree AND in the app tree ({where}). "
            f"The spec now describes this contract — delete the app-owned copy in schemas/ "
            f"rather than keeping two live definitions of it."
        )
    return hits[0]


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
    tree themselves. ``tests/helpers/pinned_schema.py``'s
    ``load_canonicalized`` is for callers that intend to follow them.
    """
    _, schema = _resolve_and_load(ref)
    return schema

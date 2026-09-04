"""Read field facts from the pinned AdCP schema tree that ships with the SDK.

The always-include set is a fact the pin states: a field is kept on the wire when
null exactly when the schema lists it in ``required`` AND types it nullable. Every
hand-declared copy of that fact is correct on the day it is written and unverified
afterwards — two of the three adopters had drifted from the pin by the time this
was added, and both emitted schema-invalid nulls to buyers.

Reads the SDK's own installed tree, so the fact moves with the ``adcp`` pin in
``pyproject.toml`` rather than with anyone's memory.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any


class UnderivablePinnedSchema(Exception):
    """The pin composes *ref* in a way no complete ``required`` list can be read from.

    Raised rather than returning an empty set, so a caller can never mistake "I could
    not read the list" for "the pin declares none". The two are indistinguishable by
    return value, which is what let the fail-open survive.
    """


@cache
def _schema_root() -> Path:
    import adcp

    major_minor = ".".join(adcp.get_adcp_spec_version().split(".")[:2])
    return Path(adcp.__file__).parent / "_schemas" / major_minor


def _resolve(node: Any, base: Path) -> tuple[Any, Path, tuple[str, str] | None]:
    """Follow ``$ref`` chains to their target.

    Returns the resolved node, the file it came from, and its *identity* — the
    ``(file, pointer)`` pair naming the subschema, or ``None`` for a node that was
    already inline. The base moves with the target, so a relative ``$ref`` inside a
    resolved file resolves against *that* file's directory rather than the entry
    point's.

    Identity and cycle detection both live here, at the single place ``$ref``
    following actually happens. Keeping them in the caller meant every spelling this
    function supports had to be re-taught to the caller's guard, and each round of
    review found one it had not been taught.
    """
    identity: tuple[str, str] | None = None
    seen: set[tuple[str, str]] = set()
    while isinstance(node, dict) and "$ref" in node:
        path_part, _, pointer = str(node["$ref"]).partition("#")
        if path_part:
            base = (base.parent / path_part).resolve()
        # A pointer-only ``$ref`` names a subschema of the file it appears in. The
        # file still has to be re-read: walking the pointer against the ``$ref`` dict
        # itself finds nothing.
        node = json.loads(base.read_text())
        identity = (str(base), pointer)
        if identity in seen:
            raise UnderivablePinnedSchema(
                f"{identity[0]}#{identity[1]} refers to itself through a $ref cycle; "
                f"no complete required list is derivable"
            )
        seen.add(identity)
        node = _walk_pointer(node, pointer)
    return node, base, identity


def _walk_pointer(node: Any, pointer: str) -> Any:
    """Walk a JSON Pointer, honouring RFC 6901 array-index tokens.

    ``schema[token]`` alone is wrong the moment a pointer crosses a list: every
    token arrives as ``str``, so ``#/oneOf/0`` raises ``TypeError`` on the array
    rather than selecting the arm. A pinned root that composes through ``oneOf``
    is exactly the shape a caller needs a pointer for -- the bare ref is
    underivable by design -- so refusing array tokens made the disjunctive case
    unreachable through the one mechanism provided for it.
    """
    for token in (t for t in pointer.split("/") if t):
        if isinstance(node, list):
            node = node[int(token)]
        else:
            node = node[token]
    return node


def _is_nullable(prop: dict[str, Any]) -> bool:
    """Whether the pin types *prop* nullable, in any of the three spellings it uses.

    Reads the property inline and deliberately does not follow a property-level
    ``$ref``: measured against the pinned tree, no required field is nullable only
    through one, so following it would change no value.
    """
    if "null" in (prop.get("type") or []):
        return True
    return any("null" in (arm.get("type") or []) for key in ("anyOf", "oneOf") for arm in (prop.get(key) or []))


def _merge_all_of(schema: dict[str, Any], base: Path) -> tuple[set[str], dict[str, Any], bool]:
    """Transitive ``allOf`` closure: merged ``required``, merged ``properties``, and
    whether any arm carries an unmergeable composition keyword.

    Only each arm's OWN top-level ``required`` is merged — an ``if``/``then`` arm
    states a conditional, not a requirement, and reading its ``if.required`` as
    unconditional would invent requiredness the pin does not declare.
    """
    required: set[str] = set()
    properties: dict[str, Any] = {}
    unmergeable = False
    # Each arm travels with the file it came from, so a nested arm reached through an
    # external ``$ref`` resolves its own relative refs against that file's directory.
    stack: list[tuple[Any, Path]] = [(arm, base) for arm in schema.get("allOf") or []]
    # Visited subschemas, by the identity ``_resolve`` reports. This exists only to
    # terminate a cyclic ``allOf`` graph — merging the same arm twice is harmless,
    # since both a set union and a dict update are idempotent. Deduplicating on
    # anything coarser than a subschema (a file, or a ``$ref`` spelling) silently
    # drops a distinct arm and returns an incomplete ``required`` list.
    merged: set[tuple[str, str]] = set()
    while stack:
        arm, arm_base = stack.pop()
        resolved, resolved_base, identity = _resolve(arm, arm_base)
        if not isinstance(resolved, dict):
            continue
        if identity is not None:
            if identity in merged:
                continue
            merged.add(identity)
        required |= set(resolved.get("required") or [])
        # allOf is conjunctive, so an arm cannot contradict an earlier one about a
        # property: merge order is immaterial.
        properties.update(resolved.get("properties") or {})
        if any(key in resolved for key in ("anyOf", "oneOf")):
            unmergeable = True
        stack.extend((nested, resolved_base) for nested in resolved.get("allOf") or [])
    return required, properties, unmergeable


@cache
def required_nullable_fields(ref: str) -> frozenset[str]:
    """Fields the pinned schema at *ref* marks both required and nullable.

    *ref* is a category-qualified path, optionally with a JSON pointer for a
    nested subschema: ``core/account.json`` or
    ``media-buy/get-media-buys-response.json#/properties/media_buys/items``.

    A ref that cannot be resolved is a hard failure. Returning an empty set on a
    missing file would silently drop every field from the wire — the exact
    omission class this derivation exists to prevent.

    The same reasoning extends to composition. ``allOf`` is conjunctive, so walking
    it yields a complete ``required`` list and an empty result genuinely means the
    pin declares none. ``anyOf``/``oneOf`` are disjunctive: no complete list exists,
    whatever else was found, so a root carrying one raises
    :class:`UnderivablePinnedSchema` rather than reporting the half it could read.
    ``account/sync-accounts-response.json`` is why — its ``allOf`` arms yield the
    protocol envelope's ``status`` while the payload's requiredness lives in
    ``oneOf`` arms, so a walk gated on emptiness returns a partial read dressed as a
    complete one.
    """
    path_part, _, pointer = ref.partition("#")
    base = (_schema_root() / path_part).resolve()
    schema = _walk_pointer(json.loads(base.read_text()), pointer)
    # Resolve the root itself. Some pinned roots are bare ``$ref`` aliases —
    # ``core/signal-pricing-option.json`` is one line of metadata pointing at
    # ``vendor-pricing-option.json`` — and without this an alias declares no
    # ``required``, composes with nothing, trips no unmergeable flag, and returns a
    # silent empty set while its target correctly raises. The fail-open survives one
    # indirection otherwise.
    schema, base, _ = _resolve(schema, base)

    local_required = schema.get("required")
    properties = dict(schema.get("properties") or {})

    if local_required is None:
        merged_required, merged_properties, arm_unmergeable = _merge_all_of(schema, base)
        if arm_unmergeable or any(key in schema for key in ("anyOf", "oneOf")):
            raise UnderivablePinnedSchema(
                f"{ref!r} composes through anyOf/oneOf, so no complete required list is "
                f"derivable; refusing to report a partial read as a complete one"
            )
        local_required = sorted(merged_required)
        properties = {**merged_properties, **properties}

    return frozenset(field for field in local_required if _is_nullable(properties.get(field) or {}))


@cache
def revision_minimum() -> int:
    """The lower bound the pin puts on ``media_buys[].revision``.

    Subscripted, never ``.get()``: if the pin drops the key, a ``KeyError`` here is a
    loud failure rather than a silent substitute bound.
    """
    schema = json.loads((_schema_root() / "media-buy" / "get-media-buys-response.json").read_text())
    return schema["properties"]["media_buys"]["items"]["properties"]["revision"]["minimum"]


@cache
def update_media_buy_revision_schema() -> dict[str, Any]:
    """The published fragment the pin puts on ``update_media_buy``'s ``revision`` token.

    One value feeds every place that must state the same buyer-facing bound: the
    ``RawRevision`` published schema, the MCP wrapper annotation, the ``Field(ge=...)``
    runtime gate, and the rejection message. A bound written a second time is a bound
    that can drift from the pin (see ``_base.py`` on redeclared bounds).

    Subscripted, never ``.get()``: a pin that drops a key fails loudly here rather than
    substituting a silent bound.
    """
    schema = json.loads((_schema_root() / "media-buy" / "update-media-buy-request.json").read_text())
    rev = schema["properties"]["revision"]
    return {"type": rev["type"], "minimum": rev["minimum"]}


@cache
def update_media_buy_revision_minimum() -> int:
    """The lower bound the pin puts on ``update_media_buy``'s ``revision`` token."""
    return update_media_buy_revision_schema()["minimum"]

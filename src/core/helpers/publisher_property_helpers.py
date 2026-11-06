"""Helpers for normalizing publisher_properties to AdCP discriminated union format.

AdCP 2.13.0+ requires PublisherPropertySelector dicts to have a selection_type
discriminator ("all", "by_id", or "by_tag"). Legacy data and inventory profiles
created via the admin UI "full JSON" mode may lack this field.

This module provides ensure_selection_type() to normalize on read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

_PROPERTY_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
_PROPERTY_TAG_PATTERN = re.compile(r"^[a-z0-9_]+$")


def ensure_selection_type(properties: list[dict]) -> list[dict] | None:
    """Ensure each publisher_properties dict has a selection_type discriminator.

    Non-destructive: adds selection_type when missing, keeps all other fields intact.
    Only filters property_ids/property_tags to valid values (^[a-z0-9_]+$).

    For each dict in the list:
    - Already has selection_type → passthrough unchanged
    - Has valid property_ids → adds selection_type "by_id", replaces property_ids with valid subset
    - Has valid property_tags → adds selection_type "by_tag", replaces property_tags with valid subset
    - Neither → adds selection_type "all"

    Non-dict entries are skipped. Returns None if result is empty.
    """
    converted = []
    for prop in properties:
        if not isinstance(prop, dict):
            continue

        if "selection_type" in prop:
            converted.append(prop)
            continue

        # Work on a copy — don't mutate the original
        result = dict(prop)
        result.setdefault("publisher_domain", "unknown")

        prop_ids = prop.get("property_ids", [])
        prop_tags = prop.get("property_tags", [])

        valid_ids = [pid for pid in prop_ids if _PROPERTY_ID_PATTERN.match(str(pid))]
        valid_tags = [tag for tag in prop_tags if _PROPERTY_TAG_PATTERN.match(str(tag))]

        if valid_ids:
            result["property_ids"] = valid_ids
            result["selection_type"] = "by_id"
        elif valid_tags:
            result["property_tags"] = valid_tags
            result["selection_type"] = "by_tag"
        else:
            result["selection_type"] = "all"

        converted.append(result)

    return converted if converted else None


# ---------------------------------------------------------------------------
# Selectors resolved against the seller's authorized properties
# ---------------------------------------------------------------------------

#: The seller's default property tag. Its stored description is "Default tag that applies to
#: all properties". It is the SELLER's tag, not one a publisher declares: AdCP 3.1.1
#: ``core/publisher-property-selector.json`` "Selects properties from a publisher's
#: adagents.json", so a buyer resolves a ``by_tag`` selector against the publisher's own file,
#: where ``all_inventory`` names nothing. Selecting it selects each publisher whole.
ALL_INVENTORY_TAG = "all_inventory"


class SelectableProperty(Protocol):
    """The three facts of an authorized property that a selector is built from."""

    @property
    def property_id(self) -> str: ...

    @property
    def publisher_domain(self) -> str: ...

    @property
    def tags(self) -> Sequence[str] | None: ...


@dataclass(frozen=True)
class AuthorizedPropertyRef:
    """An authorized property as a value, so it outlives the session that read it.

    ``get_products`` converts dynamic variants after its unit of work has closed, where an
    ORM row would raise on its first attribute read.
    """

    property_id: str
    publisher_domain: str
    tags: tuple[str, ...]


def _by_publisher(properties: Iterable[SelectableProperty]) -> dict[str, list[SelectableProperty]]:
    """*properties* grouped by publisher domain, domains in first-seen order."""
    grouped: dict[str, list[SelectableProperty]] = {}
    for prop in properties:
        grouped.setdefault(prop.publisher_domain, []).append(prop)
    return grouped


def all_selectors(properties: Iterable[SelectableProperty]) -> list[dict]:
    """One ``all`` selector per publisher of *properties*: each publisher offered whole."""
    return [{"publisher_domain": domain, "selection_type": "all"} for domain in _by_publisher(properties)]


def by_id_selectors(properties: Iterable[SelectableProperty]) -> list[dict]:
    """One ``by_id`` selector per publisher, holding the IDs of that publisher's properties.

    AdCP 3.1.1 ``core/publisher-property-selector.json``: by_id is "Single-publisher only —
    property IDs are publisher-scoped".
    """
    return [
        {"publisher_domain": domain, "property_ids": [p.property_id for p in props], "selection_type": "by_id"}
        for domain, props in _by_publisher(properties).items()
    ]


def by_tag_selectors_per_publisher(tags_by_publisher: dict[str, list[str]]) -> list[dict]:
    """One ``by_tag`` selector per publisher domain, holding the tags chosen for it."""
    return [
        {"publisher_domain": domain, "property_tags": tags, "selection_type": "by_tag"}
        for domain, tags in tags_by_publisher.items()
    ]


def by_tag_selectors(tags: Sequence[str], properties: Iterable[SelectableProperty]) -> list[dict]:
    """One selector per publisher whose properties carry any of *tags*.

    Each ``by_tag`` selector lists the requested tags that publisher's properties carry, in
    the order requested. A publisher carrying none of them is not named. ``all_inventory``
    selects every publisher whole (:data:`ALL_INVENTORY_TAG`).
    """
    if ALL_INVENTORY_TAG in tags:
        return all_selectors(properties)
    tags_by_publisher = {}
    for domain, props in _by_publisher(properties).items():
        carried = set().union(*(p.tags or () for p in props))
        matched = [tag for tag in tags if tag in carried]
        if matched:
            tags_by_publisher[domain] = matched
    return by_tag_selectors_per_publisher(tags_by_publisher)


def legacy_selectors(
    property_ids: Sequence[str] | None,
    property_tags: Sequence[str] | None,
    properties: Sequence[SelectableProperty],
) -> list[dict]:
    """The ``publisher_properties`` of a product selecting by the legacy columns.

    Resolved against the seller's *properties*, one selector per publisher (AdCP 3.1.1
    ``core/product.json`` admits only the singular ``publisher_domain`` form on a product).
    A product that selects nothing offers every authorized publisher whole. Returns ``[]``
    when no authorized property backs the selection: there is then no publisher to name.
    """
    if property_ids:
        wanted = set(property_ids)
        return by_id_selectors(p for p in properties if p.property_id in wanted)
    if property_tags:
        return by_tag_selectors(property_tags, properties)
    return all_selectors(properties)


def authorized_selectors(selectors: list[dict] | None, properties: Iterable[SelectableProperty]) -> list[dict]:
    """Stored selectors that name a publisher of *properties*; the rest are dropped.

    An inventory profile or a product's own ``properties`` already name their publishers,
    and a buyer verifies each one at ``https://<publisher_domain>/.well-known/adagents.json``
    (AdCP 3.1.1 ``governance/property/authorized-properties.mdx``). So a selector is kept
    only for a publisher the seller holds a verified property of -- a row the old profile
    form stored with the seller's own host, or a publisher whose property was never
    verified, is not offered (#1845).
    """
    domains = {p.publisher_domain for p in properties}
    return [
        selector
        for selector in ensure_selection_type(selectors or []) or []
        if selector.get("publisher_domain") in domains
    ]


def refuse_property_tag(tag: str) -> str | None:
    """Why *tag* cannot be stored, or ``None``: AdCP's ``core/property-tag.json`` pattern."""
    if _PROPERTY_TAG_PATTERN.match(tag):
        return None
    return f"Invalid tag '{tag}': use only lowercase letters, numbers, and underscores"


def tag_selection(tags: Sequence[str], authorized: Sequence[SelectableProperty]) -> tuple[list[dict], str | None]:
    """An admin form's tag choice as selectors over the seller's *authorized* properties, or why not.

    *authorized* are the VERIFIED properties (``AuthorizedPropertyRepository.list_refs``),
    which is what ``get_products`` sells, so a tag only a pending property carries is
    refused rather than stored and then silently not offered. Every requested tag must be
    carried; ``all_inventory`` is the seller's own and is carried whenever any property is.

    Returns ``(selectors, None)``, or ``([], refusal)``.
    """
    if not tags:
        return [], "At least one valid property tag is required"
    refusal = next(filter(None, map(refuse_property_tag, tags)), None)
    if refusal:
        return [], refusal
    carried = set().union(*(p.tags or () for p in authorized), {ALL_INVENTORY_TAG} if authorized else ())
    missing = [tag for tag in tags if tag not in carried]
    if missing:
        return [], f"No verified authorized property carries the tags: {', '.join(missing)}"
    return by_tag_selectors(tags, authorized), None


def publisher_tag_selection(
    selections: Sequence[str], authorized: Sequence[SelectableProperty]
) -> tuple[list[dict], str | None]:
    """The product form's ``domain:tag`` choices as selectors, each publisher's tags checked by :func:`tag_selection`.

    Returns ``(selectors, None)``, or ``([], refusal)`` at the first unusable choice.
    """
    tags_by_domain: dict[str, list[str]] = {}
    for selection in selections:
        domain, separator, tag = selection.partition(":")
        if not separator:
            return [], f"Invalid tag selection format: {selection}"
        tags = tags_by_domain.setdefault(domain, [])
        if tag not in tags:
            tags.append(tag)
    if not tags_by_domain:
        return [], "At least one valid property tag is required"
    selectors: list[dict] = []
    for domain, tags in tags_by_domain.items():
        chosen, refusal = tag_selection(tags, [p for p in authorized if p.publisher_domain == domain])
        if refusal:
            return [], f"{domain}: {refusal}"
        selectors.extend(chosen)
    return selectors, None


def id_selection(
    property_ids: Sequence[str], authorized: Sequence[SelectableProperty]
) -> tuple[list[dict], str | None]:
    """An admin form's property choice as ``by_id`` selectors over the seller's *authorized* properties, or why not.

    *authorized* are the VERIFIED properties, as for :func:`tag_selection`: an ID that names
    no verified property of this seller (unknown, another tenant's, or pending) is refused.

    Returns ``(selectors, None)``, or ``([], refusal)``.
    """
    if not property_ids:
        return [], "At least one property must be selected"
    wanted = set(property_ids)
    chosen = [p for p in authorized if p.property_id in wanted]
    unknown = wanted - {p.property_id for p in chosen}
    if unknown:
        return [], f"Not verified authorized properties: {', '.join(sorted(unknown))}"
    return by_id_selectors(chosen), None

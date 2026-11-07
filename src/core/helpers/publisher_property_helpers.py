"""Helpers for normalizing publisher_properties to AdCP discriminated union format.

AdCP 2.13.0+ requires PublisherPropertySelector dicts to have a selection_type
discriminator ("all", "by_id", or "by_tag"). Legacy data and inventory profiles
created via the admin UI "full JSON" mode may lack this field.

This module provides ensure_selection_type() to normalize on read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import PublisherDomain
from pydantic import ValidationError

from src.core.http_utils import canonical_publisher_domain

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
#: all properties", and the admin forms put it on every property row, so a row carrying it
#: says nothing about the publisher's own adagents.json: a publisher following
#: ``docs/deployment/vm.md`` declares it there, and another need not. AdCP 3.1.1
#: ``core/publisher-property-selector.json`` "Selects properties from a publisher's
#: adagents.json", so ``by_tag`` would name nothing at a publisher that does not declare it.
#: A tag selection holding it therefore selects each publisher whole, a form's choice and a
#: stored ``by_tag`` selector alike (:func:`publisher_tag_selector`). ``all`` claims "all
#: properties from each addressed publisher": under ``property_tags`` or ``property_ids``
#: authorization that includes properties outside the authorized scope, and the spec does not
#: say how a buyer treats them.
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
    ORM row would raise on its first attribute read. ``name`` and ``property_type`` are what
    the admin forms show of it.
    """

    property_id: str
    publisher_domain: str
    tags: tuple[str, ...]
    name: str
    property_type: str


def publisher_domain_on_wire(stored: str) -> str | None:
    """The host *stored* names, or ``None`` when AdCP's ``publisher_domain`` pattern refuses it.

    An admin path stores a domain as typed. AdCP 3.1.1 ``core/product.json`` admits only the
    pattern, which a port, an empty domain or an underscore cannot meet.
    """
    domain = canonical_publisher_domain(stored)
    try:
        PublisherDomain(root=domain)
    except ValidationError:
        return None
    return domain


def _by_publisher(properties: Iterable[SelectableProperty]) -> dict[str, list[SelectableProperty]]:
    """*properties* grouped by publisher domain, domains in first-seen order."""
    grouped: dict[str, list[SelectableProperty]] = {}
    for prop in properties:
        grouped.setdefault(prop.publisher_domain, []).append(prop)
    return grouped


def by_id_selectors(properties: Iterable[SelectableProperty]) -> list[dict]:
    """One ``by_id`` selector per publisher, holding the IDs of that publisher's properties.

    AdCP 3.1.1 ``core/publisher-property-selector.json``: by_id is "Single-publisher only —
    property IDs are publisher-scoped".
    """
    return [
        {"publisher_domain": domain, "property_ids": [p.property_id for p in props], "selection_type": "by_id"}
        for domain, props in _by_publisher(properties).items()
    ]


def carried_tags(properties: Iterable[SelectableProperty]) -> set[str]:
    """The tags *properties* carry, and :data:`ALL_INVENTORY_TAG`, the seller's own, whenever any property is."""
    props = list(properties)
    return set().union(*(p.tags or () for p in props), {ALL_INVENTORY_TAG} if props else ())


def publisher_tag_selector(domain: str, tags: Sequence[str]) -> dict:
    """The selector naming *tags* of publisher *domain*: ``by_tag``, or ``all`` when *tags* hold :data:`ALL_INVENTORY_TAG`.

    The one place the seller's default tag becomes ``all``, for a form's tag choice and a
    stored ``by_tag`` selector alike.
    """
    if ALL_INVENTORY_TAG in tags:
        return {"publisher_domain": domain, "selection_type": "all"}
    return {"publisher_domain": domain, "property_tags": list(tags), "selection_type": "by_tag"}


def by_tag_selectors(tags: Sequence[str], properties: Iterable[SelectableProperty]) -> list[dict]:
    """One selector per publisher whose properties carry any of *tags* (:func:`publisher_tag_selector`).

    Each lists the requested tags that publisher's properties carry, in the order requested.
    A publisher carrying none of them is not named.
    """
    selectors = []
    for domain, props in _by_publisher(properties).items():
        carried = carried_tags(props)
        matched = [tag for tag in tags if tag in carried]
        if matched:
            selectors.append(publisher_tag_selector(domain, matched))
    return selectors


def authorized_selectors(selectors: list[dict] | None, properties: Iterable[SelectableProperty]) -> list[dict]:
    """Stored selectors narrowed to the verified *properties* they name; the rest are dropped.

    An inventory profile or a product's own ``properties`` already name their publishers,
    and a buyer verifies each one at ``https://<publisher_domain>/.well-known/adagents.json``
    (AdCP 3.1.1 ``governance/property/authorized-properties.mdx``). The seller's verified
    row is its proof of authorization for that one property, so the check is per property,
    as :func:`id_selection` and :func:`tag_selection` check a form's choice: a ``by_id``
    selector keeps the ids of its publisher's verified properties, a ``by_tag`` selector
    keeps the tags a verified property of its publisher carries, and an ``all`` selector
    stays while its publisher has a verified property. A selector left naming nothing is
    dropped -- a row the old profile form stored with the seller's own host, or a publisher
    or property never verified, is not offered (#1845).

    A kept selector names its publisher canonically, as *properties* do, and a ``by_tag`` one
    is built by :func:`publisher_tag_selector`, as a form's tag choice is.
    """
    verified = _by_publisher(properties)
    kept = []
    for selector in ensure_selection_type(selectors or []) or []:
        domain = canonical_publisher_domain(str(selector.get("publisher_domain") or ""))
        if domain not in verified:
            continue
        if selector["selection_type"] == "by_tag":
            carried = carried_tags(verified[domain])
            tags = [tag for tag in selector.get("property_tags") or () if tag in carried]
            if tags:
                kept.append(publisher_tag_selector(domain, tags))
        elif selector["selection_type"] == "by_id":
            ids = {p.property_id for p in verified[domain]}
            property_ids = [i for i in selector.get("property_ids") or () if i in ids]
            if property_ids:
                kept.append({**selector, "publisher_domain": domain, "property_ids": property_ids})
        else:
            kept.append({**selector, "publisher_domain": domain})
    return kept


@dataclass(frozen=True)
class PreselectedChoices:
    """The boxes a product edit page pre-checks: ``domain:tag`` values and property ids."""

    tags: frozenset[str]
    property_ids: frozenset[str]


def preselected_choices(selectors: Iterable[dict], authorized: Iterable[SelectableProperty]) -> PreselectedChoices:
    """The boxes the product edit page checks for *selectors*, among those it offers.

    It offers the *authorized* (verified) properties, and the boxes cannot express every
    stored selection: a pending publisher's selector, ``all``, a mix of modes. Those are shown
    disabled and kept (:func:`kept_choices`).
    """
    authorized = list(authorized)
    tags, property_ids = set(), set()
    for selector in selectors:
        offered = [p for p in authorized if p.publisher_domain == selector["publisher_domain"]]
        carried = {tag for p in offered for tag in p.tags or ()}
        tags |= {
            f"{selector['publisher_domain']}:{tag}" for tag in selector.get("property_tags") or () if tag in carried
        }
        property_ids |= {p.property_id for p in offered if p.property_id in (selector.get("property_ids") or ())}
    return PreselectedChoices(frozenset(tags), frozenset(property_ids))


#: The product edit page's sections, by the property mode that submits them, and the kind of
#: stored choice each offers a box for. The full (legacy) mode offers property ids too.
_SECTION_KIND = {"tags": "tag", "property_ids": "id", "full": "id"}


@dataclass(frozen=True)
class KeptChoice:
    """A stored tag, property id or whole publisher a product edit page section has no box for.

    The section renders it disabled with its *status*, and a save that changes the section's
    boxes keeps it (:func:`kept_selectors`): the operator cannot see it there to drop it.
    """

    publisher_domain: str
    kind: str  # "tag", "id" or "all"
    value: str
    status: str


def kept_choices(
    stored: list[dict] | None, verified: Sequence[SelectableProperty], known: Sequence[SelectableProperty], mode: str
) -> list[KeptChoice]:
    """The choices of the *stored* selectors the product edit page's *mode* section has no box for.

    The page offers a box per tag a *verified* property carries (tags section) and per verified
    property id (the ids and full sections). A stored choice with no box in the section the
    save comes from -- a pending property, one the other mode selects, an ``all`` selector --
    is kept. *known* are all the seller's properties, pending ones included, for the status.
    """
    boxes = {
        "tag": {(p.publisher_domain, tag) for p in verified for tag in p.tags or ()},
        "id": {(p.publisher_domain, p.property_id) for p in verified},
    }
    pending = {(p.publisher_domain, tag) for p in known for tag in p.tags or ()} | {
        (p.publisher_domain, p.property_id) for p in known
    }
    section = _SECTION_KIND.get(mode)
    kept = []
    for selector in ensure_selection_type(stored or []) or []:
        domain = canonical_publisher_domain(str(selector.get("publisher_domain") or ""))
        kind = {"by_tag": "tag", "by_id": "id"}.get(selector["selection_type"])
        if kind is None:
            kept.append(KeptChoice(domain, "all", "", "every property of the publisher"))
            continue
        for value in selector.get("property_tags" if kind == "tag" else "property_ids") or ():
            if kind == section and (domain, value) in boxes[kind]:
                continue
            if (domain, value) in boxes[kind]:
                status = "selected in the other selection mode"
            elif (domain, value) in pending:
                status = "awaiting verification"
            else:
                status = "not an authorized property"
            kept.append(KeptChoice(domain, kind, value, status))
    return kept


def kept_selectors(chosen: list[dict], kept: Iterable[KeptChoice]) -> list[dict]:
    """The *chosen* selectors with the *kept* choices merged in, one selector per publisher and kind."""
    merged = [dict(selector) for selector in chosen]
    for choice in kept:
        if choice.kind == "all":
            if not any(
                s["publisher_domain"] == choice.publisher_domain and s["selection_type"] == "all" for s in merged
            ):
                merged.append({"publisher_domain": choice.publisher_domain, "selection_type": "all"})
            continue
        field_name, selection_type = ("property_tags", "by_tag") if choice.kind == "tag" else ("property_ids", "by_id")
        target = next(
            (
                s
                for s in merged
                if s["publisher_domain"] == choice.publisher_domain and s["selection_type"] == selection_type
            ),
            None,
        )
        if target is None:
            target = {"publisher_domain": choice.publisher_domain, field_name: [], "selection_type": selection_type}
            merged.append(target)
        if choice.value not in target[field_name]:
            target[field_name] = [*target[field_name], choice.value]
    return merged


def product_form_fields(selectors: list[dict], authorized: Iterable[SelectableProperty]) -> dict[str, list[str]]:
    """The selection fields the product edit page renders for *selectors*: its mode and checked boxes.

    *selectors* are what the product sells (``Product.resolve_publisher_properties``). The
    first one picks the mode; the full (legacy) mode checks no box.
    """
    choices = preselected_choices(selectors, authorized)
    first = selectors[0]["selection_type"] if selectors else "by_tag"
    return {
        "property_mode": [{"by_tag": "tags", "by_id": "property_ids"}.get(first, "full")],
        "selected_property_tags": sorted(choices.tags),
        "selected_property_ids": sorted(choices.property_ids),
        "full_property_ids": [],
    }


@dataclass(frozen=True)
class RenderedSelection:
    """The selection fields an edit page rendered, and a digest of the stored columns they show.

    The page embeds it (:meth:`token`) and its save is judged against it
    (:func:`judge_selection_save`), never against a selection recomputed when the save
    arrives: a property verified in between would change that recomputation, and a save
    that touched nothing would then rewrite what the product or profile sells.
    """

    fields: dict[str, list[str]]
    digest: str

    def token(self) -> str:
        return json.dumps({"fields": self.fields, "digest": self.digest}, sort_keys=True)


#: The refusal of a save whose page shows a selection that changed since it rendered.
STALE_SELECTION_MESSAGE = (
    "The publishers of this {subject} changed since this page was opened. Reload the page and save your changes again."
)


def selection_digest(*columns: object) -> str:
    """A digest of the stored selection *columns*, to tell whether they changed under an open page."""
    return hashlib.sha256(json.dumps(columns, sort_keys=True, default=str).encode()).hexdigest()


def judge_selection_save(token: str | None, posted: Callable[[str], list[str]], digest: str) -> str:
    """``"unchanged"``, ``"stale"`` or ``"changed"``: what a save does to the stored selection.

    *token* is the page's :meth:`RenderedSelection.token`, *posted* reads a field's posted
    values, *digest* is :func:`selection_digest` of the columns as stored now. ``"stale"``:
    the columns changed since the page rendered, so the page must not overwrite them.
    ``"unchanged"``: every selection field posts what the page rendered. A save carrying no
    token (a client other than the page) is ``"changed"``.
    """
    try:
        rendered = json.loads(token or "")
    except ValueError:
        return "changed"
    if rendered.get("digest") != digest:
        return "stale"
    for name, values in rendered.get("fields", {}).items():
        if sorted(map(_form_value, posted(name))) != sorted(map(_form_value, values)):
            return "changed"
    return "unchanged"


def _form_value(value: str) -> str:
    """*value* as a comparison sees it: a browser submits a textarea's newlines as CRLF."""
    return value.replace("\r\n", "\n").strip()


@dataclass(frozen=True)
class ProfileFormPrefill:
    """What the inventory-profile edit page renders for a profile's stored selectors."""

    property_mode: str
    property_tags: str = ""
    property_ids: frozenset[str] = field(default_factory=frozenset)
    publisher_properties: str = ""

    def fields(self) -> dict[str, list[str]]:
        """The selection fields the page renders from this pre-fill, as :class:`RenderedSelection` holds them."""
        return {
            "property_mode": [self.property_mode],
            "property_tags": [self.property_tags],
            "selected_property_ids": sorted(self.property_ids),
            "publisher_properties": [self.publisher_properties],
        }


def profile_form_prefill(selectors: list[dict] | None, authorized: Sequence[SelectableProperty]) -> ProfileFormPrefill:
    """The mode and values that save *selectors* back exactly as they are stored.

    A tags or property-ids pre-fill is used only when submitting it regenerates *selectors*.
    Anything else (a pending publisher's selector, a mix of modes, ``all``, a hand-written entry) is
    shown whole as JSON, the one mode that can hold it, so saving the page cannot lose it.
    """
    if not selectors:
        return ProfileFormPrefill("tags")
    # The JSON mode stores whatever parses, so an entry need not be a selector.
    well_formed = [s for s in selectors if isinstance(s, dict)]
    tags = list(dict.fromkeys(tag for s in well_formed for tag in s.get("property_tags") or ()))
    property_ids = [property_id for s in well_formed for property_id in s.get("property_ids") or ()]
    if tag_selection(tags, authorized) == (selectors, None):
        return ProfileFormPrefill("tags", property_tags=", ".join(tags))
    if id_selection(property_ids, authorized) == (selectors, None):
        return ProfileFormPrefill("property_ids", property_ids=frozenset(property_ids))
    return ProfileFormPrefill("full", publisher_properties=json.dumps(selectors, indent=2))


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
    carried = carried_tags(authorized)
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


def selector_selection(
    selectors: object, authorized: Sequence[SelectableProperty], stored: Sequence[object] = ()
) -> tuple[list[dict], str | None]:
    """An admin form's JSON selectors, accepted only when each is sold as written, or why not.

    *authorized* are the VERIFIED properties, and each selector is resolved by
    :func:`authorized_selectors`, the rule ``get_products`` applies to a stored selector. One
    it would drop, or narrow by an id or a tag, names something no verified property backs,
    so it is refused rather than stored and then silently not sold. A selector already
    *stored* is kept as it is: an edit form carries over what it did not change.

    Returns ``(selectors, None)``, or ``([], refusal)``.
    """
    if not isinstance(selectors, list) or not selectors:
        return [], "Publisher properties are required"
    refused = [
        str(selector.get("publisher_domain")) if isinstance(selector, dict) else json.dumps(selector)
        for selector in selectors
        if selector not in stored and not _sold_as_written(selector, authorized)
    ]
    if refused:
        return [], f"Not verified authorized publisher selections: {', '.join(refused)}"
    return selectors, None


def _sold_as_written(selector: object, authorized: Sequence[SelectableProperty]) -> bool:
    """Whether :func:`authorized_selectors` keeps *selector* with every id and tag it lists."""
    if not isinstance(selector, dict):
        return False
    kept = authorized_selectors([selector], authorized)
    if not kept:
        return False
    written = (ensure_selection_type([selector]) or [{}])[0]
    if kept[0]["selection_type"] != written["selection_type"]:
        return True  # a tag selection of all_inventory, sold whole (publisher_tag_selector)
    return all(kept[0].get(field) == written.get(field) for field in ("property_ids", "property_tags"))


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

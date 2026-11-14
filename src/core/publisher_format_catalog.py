"""What a seller's publishers authorize, and the format catalogs they publish.

Each publisher states in its own ``https://{domain}/.well-known/adagents.json`` whether it
authorizes this agent, for which properties, and which format options its inventory
accepts. ``src/services/publisher_ingest.py`` stores that file and derives the properties
and the ``formats[]`` catalog from it; this module is how a reader sees them.

A product references a publisher's format option by its key alone, ``{publisher_domain,
format_option_id}`` (``core/product-format-declaration.json``: "Publisher-scoped canonical
declarations are addressable only by the complete {publisher_domain, format_option_id}
identity pair"; ``core/product.json`` ``format_options``: "Match publisher-declared options by
{publisher_domain, format_option_id}"). It is served as the publisher's CURRENT entry
(:func:`resolve_declarations`), and ``core/product.json`` makes "an unresolved publisher
placement or format-option reference MUST fail closed": :func:`format_option_refusal` is why
such a product is not offered.

The host this agent serves its own adagents.json at is not a catalog: that file carries no
``formats[]`` (``src/core/signing/trust_root.py``), so an option naming it resolves nowhere
and is refused like any other unresolved key.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from src.core.http_utils import canonical_publisher_domain

if TYPE_CHECKING:
    from src.core.database.models import Publisher, PublisherProperty
    from src.core.helpers.publisher_property_helpers import AuthorizedPropertyRef, SelectableProperty

logger = logging.getLogger(__name__)

#: A publisher-backed format option's identity: canonical publisher domain, format_option_id.
OptionKey = tuple[str, str]


@functools.cache
def format_declarations_type() -> TypeAdapter:
    """The pinned type of a list of ``core/product-format-declaration.json`` documents.

    Read off the DTO that serves a product's ``format_options`` rather than restated, so an
    authoring surface refuses exactly what ``convert_product_model_to_schema`` would refuse
    later.
    """
    from src.core.schemas.product import Product as ProductSchema

    return TypeAdapter(ProductSchema.model_fields["format_options"].annotation)


def option_key(declaration: Mapping[str, Any]) -> OptionKey | None:
    """The key a declaration references a publisher's option by, or ``None`` for a product-local one."""
    domain = declaration.get("publisher_domain")
    if not domain:
        return None
    return canonical_publisher_domain(str(domain)), str(declaration.get("format_option_id") or "")


def names_a_publisher(declarations: Iterable[Mapping[str, Any]] | None) -> bool:
    """Whether any of *declarations* references a publisher's option."""
    return any(option_key(declaration) for declaration in declarations or ())


def stored_declaration(declaration: Mapping[str, Any]) -> Mapping[str, Any]:
    """*declaration* as a product stores it: a publisher-backed one as its key alone, a product-local one whole.

    A product does not narrow a publisher's option (the spec defines no narrowing), so the
    key is all it keeps; the publisher's catalog supplies the rest on every read.
    """
    key = option_key(declaration)
    if key is None:
        return declaration
    return {"publisher_domain": key[0], "format_option_id": key[1]}


def publisher_option_keys(declarations: Iterable[Mapping[str, Any]] | None) -> list[OptionKey]:
    """The keys *declarations* reference, in order, without repeats."""
    return list(dict.fromkeys(key for key in map(option_key, declarations or ()) if key is not None))


@dataclass(frozen=True)
class PublisherOption:
    """One entry of a publisher's ``formats[]`` catalog, as a value."""

    publisher_domain: str
    format_option_id: str
    declaration: Mapping[str, Any]
    applies_to_property_ids: frozenset[str] = field(default_factory=frozenset)
    applies_to_property_tags: frozenset[str] = field(default_factory=frozenset)

    def served(self) -> dict[str, Any]:
        """The declaration a product referencing this option is served: the publisher's entry under its key."""
        return {
            **self.declaration,
            "publisher_domain": self.publisher_domain,
            "format_option_id": self.format_option_id,
        }

    def covers(self, properties: Iterable[SelectableProperty]) -> bool:
        """Whether this option applies to any of *properties*.

        adagents.json ``formats[]``: an entry "without scope applies to all properties in the
        file; with scope, only to the named subset", and the scope is the union of
        ``applies_to_property_ids`` and ``applies_to_property_tags``.
        """
        if not self.applies_to_property_ids and not self.applies_to_property_tags:
            return True
        return any(
            prop.property_id in self.applies_to_property_ids or self.applies_to_property_tags & set(prop.tags or ())
            for prop in properties
        )


def options_by_key(publishers: Iterable[Publisher]) -> dict[OptionKey, PublisherOption]:
    """Every format option *publishers* publish, by key."""
    return {
        (row.publisher_domain, row.format_option_id): PublisherOption(
            publisher_domain=row.publisher_domain,
            format_option_id=row.format_option_id,
            declaration=row.declaration,
            applies_to_property_ids=frozenset(row.applies_to_property_ids or ()),
            applies_to_property_tags=frozenset(row.applies_to_property_tags or ()),
        )
        for publisher in publishers
        for row in publisher.format_options
    }


def resolve_declarations(
    declarations: Iterable[Mapping[str, Any]] | None, options: Mapping[OptionKey, PublisherOption]
) -> list[Mapping[str, Any]]:
    """*declarations* as served: a product-local one as authored, a publisher key as the publisher's entry.

    A key no option of *options* answers is left out: it cannot be served, and the product
    carrying it is not offered (:func:`format_option_refusal`).
    """
    resolved: list[Mapping[str, Any]] = []
    for declaration in declarations or ():
        key = option_key(declaration)
        if key is None:
            resolved.append(declaration)
        elif key in options:
            resolved.append(options[key].served())
    return resolved


def authorized_properties(publisher: Publisher, now: datetime) -> list[PublisherProperty]:
    """The properties *publisher*'s stored adagents.json authorizes this agent for at *now*.

    THE rule, in one place: none while the document is past its freshness window
    (``Publisher.is_fresh`` -- fail closed), else the derived properties whose authorizing
    entry's ``effective_from`` / ``effective_until`` window holds *now*.
    """
    if not publisher.is_fresh(now):
        return []
    return [prop for prop in publisher.properties if prop.in_effect(now)]


@dataclass(frozen=True)
class PublisherCatalog:
    """What the seller's publishers authorize now, read once per request.

    *refs* are the properties the publishers authorize this agent for now
    (:func:`authorized_properties`); *options* the format options their stored files publish.
    An option of a publisher whose document is past its freshness window is never served: the
    publisher authorizes nothing, so no product sells it (:func:`format_option_refusal` (b)).
    """

    refs: tuple[AuthorizedPropertyRef, ...] = ()
    options: Mapping[OptionKey, PublisherOption] = field(default_factory=dict)

    @classmethod
    def of(cls, publishers: Sequence[Publisher], now: datetime) -> PublisherCatalog:
        """The catalog of *publishers* at *now*.

        A property is named by the host its publisher row names (``publisher_domain_on_wire``).
        A publisher no such name exists for (a port) is logged and left out, so it costs that
        publisher and not every read of the catalog.
        """
        from src.core.helpers.publisher_property_helpers import AuthorizedPropertyRef, publisher_domain_on_wire

        refs: set[AuthorizedPropertyRef] = set()
        for publisher in publishers:
            domain = publisher_domain_on_wire(publisher.domain)
            if domain is None:
                logger.warning("Publisher %r is not offered: it is not a publisher domain", publisher.domain)
                continue
            refs.update(
                AuthorizedPropertyRef(
                    property_id=prop.property_id,
                    publisher_domain=domain,
                    tags=tuple(prop.tags or ()),
                    name=prop.name,
                    property_type=prop.property_type,
                )
                for prop in authorized_properties(publisher, now)
            )
        return cls(
            refs=tuple(sorted(refs, key=lambda ref: (ref.publisher_domain, ref.property_id or "", ref.name))),
            options=options_by_key(publishers),
        )


def format_option_refusal(
    declarations: Iterable[Mapping[str, Any]] | None,
    publisher_properties: Sequence[Mapping[str, Any]],
    catalog: PublisherCatalog,
) -> str | None:
    """Why *declarations* cannot be served for a product selling *publisher_properties*, or ``None``.

    Each publisher key must (a) name an option a publisher's stored file publishes, (b) belong to a
    publisher the product sells, and (c) apply to at least one property the product sells of
    that publisher. *publisher_properties* are the product's selectors narrowed to the
    authorized properties (``Product.resolve_publisher_properties``).
    """
    from src.core.helpers.publisher_property_helpers import selected_properties

    refusals = []
    for domain, option_id in publisher_option_keys(declarations):
        option = catalog.options.get((domain, option_id))
        sold = [selector for selector in publisher_properties if selector.get("publisher_domain") == domain]
        if option is None:
            refusals.append(
                f"format option {option_id!r} of {domain} is not in the format catalog of a publisher whose "
                f"adagents.json this seller holds"
            )
        elif not sold:
            refusals.append(f"format option {option_id!r} names {domain}, a publisher this product does not sell")
        elif not option.covers(selected_properties(sold, catalog.refs)):
            refusals.append(
                f"format option {option_id!r} of {domain} applies to none of the properties this product sells"
            )
    return "; ".join(refusals) or None

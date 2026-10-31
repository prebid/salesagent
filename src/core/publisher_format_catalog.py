"""The reusable publisher format options a tenant publishes, and the references that name them.

RFC S3 (``docs/rfcs/002-creative-format-model.md``). The pinned obligation is
``docs/creative/canonical-formats-migration.mdx`` § Seller migration, step 3: "Publish reusable
publisher options in ``/.well-known/adagents.json`` and reference them with
``publisher_domain`` plus ``format_option_id``."

**Who publishes.** ``adagents.json#/properties/formats`` is the "Publisher-authoritative format
catalog", and ``canonical-formats.mdx`` § Publisher catalog resolution says not to "synthesize a
publisher declaration from one sales agent's products". So the catalog is AUTHORED publisher
data, never derived from product declarations: a product entry is "Seller-specific narrowing"
(``canonical-formats.mdx`` § Where to declare a format), and two products may narrow one publisher
option two ways. This seller is the publisher for the host it is served at (it already serves
``/.well-known/adagents.json`` there), so the tenant row carries the catalog for that host and
:func:`src.core.signing.trust_root.build_adagents_json` publishes it as ``formats[]``.

**What a reference must resolve to.** ``adagents.json#/properties/formats``: "Products selling
this publisher's inventory SHOULD reference these declarations by ``format_option_id`` ... via
inline ``format_options`` whose ``format_option_id`` matches an entry here". This seller can
answer that only for the namespace it owns -- a declaration whose ``publisher_domain`` is its own
publisher domain. Another publisher's catalog is resolved by the buyer, which the pin makes the
fail-closed party (``core/product.json`` ``format_options``; ``core/placement.json``
``format_options``: "buyers MUST also resolve ..."). An own-namespace reference that the catalog
does not declare is refused where it is AUTHORED (the product and inventory-profile forms), where
a product's SERVED contract changes (the product forms check ``Product.format_contract`` after the
profile link is set, so linking a profile whose options the catalog has since withdrawn, or
unlinking one so the product's own stored arm is served again, is refused), and where the catalog
is EDITED (withdrawing an option a product is served). So a stored product cannot announce a
publisher option its own publisher file does not carry.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.database.models import Tenant


@functools.cache
def format_declarations_type() -> TypeAdapter:
    """The pinned type of a list of ``core/product-format-declaration.json`` documents.

    Read off the DTO that serves a product's ``format_options`` rather than restated, so an
    authoring surface refuses exactly what ``convert_product_model_to_schema`` would refuse
    later. ``adagents.json`` ``formats[]`` items are that same declaration (plus optional
    property scoping), so the publisher catalog is validated by the same type.
    """
    from src.core.schemas.product import Product as ProductSchema

    return TypeAdapter(ProductSchema.model_fields["format_options"].annotation)


def validated_catalog(document: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """*document* as the catalog to store, or ``ValueError`` naming why it is not one.

    An empty catalog is stored as ``None``: ``formats[]`` has ``minItems: 1``, so an empty list
    would publish a declaration that is not there.

    Every entry carries a ``format_option_id``, unique within the catalog. The pin does not
    require the id on a ``formats[]`` item, but an entry without one cannot be referenced by
    ``{publisher_domain, format_option_id}`` -- the only thing a reusable option is for -- and
    two entries under one id make a reference ambiguous.
    """
    if not document:
        return None
    # pydantic's ValidationError is a ValueError, so the caller's one except clause covers it.
    format_declarations_type().validate_python(document)
    option_ids = [str(entry.get("format_option_id") or "") for entry in document]
    if not all(option_ids):
        raise ValueError("Every publisher format option needs a format_option_id: it is how a product references it")
    duplicates = sorted({option_id for option_id in option_ids if option_ids.count(option_id) > 1})
    if duplicates:
        raise ValueError(f"Publisher format option ids must be unique; repeated: {', '.join(duplicates)}")
    return document


@dataclass(frozen=True)
class PublisherCatalog:
    """The tenant's own publisher namespace: its domain and the options its file declares.

    A value, read off the tenant by the caller that already holds it: resolving a reference
    must not re-read the tenant inside a caller's session.
    """

    publisher_domain: str
    options: tuple[Mapping[str, Any], ...]

    @classmethod
    def of(cls, tenant: Tenant) -> PublisherCatalog:
        # ``primary_domain`` is the domain ``Product.publisher_domain`` announces, so it is the
        # namespace a product's own-publisher declaration is written under.
        return cls(publisher_domain=tenant.primary_domain, options=tuple(tenant.publisher_format_options or ()))

    def unresolved(self, declarations: Iterable[Mapping[str, Any]] | None) -> list[str]:
        """The ``format_option_id`` of each own-namespace declaration the catalog does not declare.

        Matched by ``format_option_id`` (formats.mdx § Matching: "An exact publisher claim must
        match both ``publisher_domain`` and ``format_option_id``") and by ``format_kind``: a
        product declaration narrows the publisher option it names, and a narrowing never changes
        the canonical the option is anchored to.
        """
        published = {(option.get("format_option_id"), option.get("format_kind")) for option in self.options}
        return [
            str(declaration.get("format_option_id"))
            for declaration in declarations or ()
            if declaration.get("publisher_domain") == self.publisher_domain
            and (declaration.get("format_option_id"), declaration.get("format_kind")) not in published
        ]

    def refuse_unresolved(self, declarations: Iterable[Mapping[str, Any]] | None) -> None:
        """``ValueError`` when a declaration names an option this publisher does not publish."""
        unresolved = self.unresolved(declarations)
        if unresolved:
            raise ValueError(
                f"format_options name publisher options {', '.join(sorted(set(unresolved)))} under "
                f"{self.publisher_domain}, which this seller's publisher format catalog does not "
                f"declare with that format_kind. Publish them in the catalog first, or declare them "
                f"product-local by omitting publisher_domain."
            )

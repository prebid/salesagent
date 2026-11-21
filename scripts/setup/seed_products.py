"""The one way a seeder puts a product in a tenant's catalogue.

A product is never one row. It is a ``Product`` plus the ``PricingOption`` that makes it
quotable, and a catalogue missing the second half is a catalogue that answers
``get_products`` with prices nobody can buy at. Three seeders each spelled that pair out
in full -- the CI tenant, the CI isolation tenant and the storyboard tenant -- so a field
added to either row had to be found in three places, and the day one copy was missed the
symptom surfaced storyboards later as a conformance gap rather than as a seeding error.

The spec dict each seeder already writes is the input, unchanged; what moved here is the
operation.

A product names only publishers whose adagents.json authorizes this agent (#1845), so the
publisher backing a seeded catalogue is seeded here too: :func:`seed_verified_website`
stores a document for a publisher the seeder controls, through the ingest service's apply
half, without fetching it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from src.core.database.models import PricingOption, Product, ProductPublisherSelection
from src.core.database.repositories.publisher import PublisherRepository

#: The canonical creative agent, which the pin names as the namespace for IAB-standard
#: ``v1_format_ref`` entries (core/product-format-declaration.json, "AAO-hosted convention").
AAO_CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org"


def canonical_declaration(format_kind: str, v1_format_id: str, **params: Any) -> dict[str, Any]:
    """One canonical format declaration that asserts its legacy projection.

    Seeders author products canonically (core/product.json: "New products MUST author
    canonical format_options[] ... Do not author a new product from format_ids alone"). The
    ``v1_format_ref`` is the seller's assertion of the named format this declaration is, so
    a legacy buyer is served ``format_ids`` derived from it.
    """
    return {
        "format_kind": format_kind,
        "params": params,
        "v1_format_ref": [{"agent_url": AAO_CREATIVE_AGENT_URL, "id": v1_format_id}],
    }


#: A product spec as the seeders write it: the Product's own fields plus a ``pricing``
#: block. Kept a plain dict because that is what the seeders' literals already are -- and
#: what ``seed_storyboard_tenant`` derives from the pinned bundle's test kit.
ProductSpec = dict[str, Any]


def seed_product(session, tenant_id: str, spec: ProductSpec) -> bool:
    """Create *spec* in *tenant_id*'s catalogue, unless it is already there.

    Returns True when it created the product, False when one with that id existed. Does
    NOT commit: the caller owns the transaction, because each seeder batches its own.

    Every JSONB column the model does not default is set to None explicitly -- the table's
    constraints distinguish SQL NULL from an absent key, and a seeder that omits one gets
    an IntegrityError that names the constraint rather than the field.
    """
    exists = session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=spec["product_id"])).first()
    if exists:
        return False

    session.add(
        Product(
            tenant_id=tenant_id,
            product_id=spec["product_id"],
            name=spec["name"],
            description=spec["description"],
            format_options=spec["format_options"],
            targeting_template=spec["targeting_template"],
            delivery_type=spec["delivery_type"],
            measurement=None,
            creative_policy=None,
            price_guidance=None,
            countries=None,
            implementation_config=None,
            publisher_selections=whole_publisher_selections(session, tenant_id),
        )
    )
    pricing = spec["pricing"]
    session.add(
        PricingOption.create(
            tenant_id=tenant_id,
            product_id=spec["product_id"],
            pricing_model=pricing["model"],
            rate=pricing["rate"],
            currency=pricing.get("currency", "USD"),
            is_fixed=pricing["is_fixed"],
            price_guidance=None,  # Not used for fixed-price products
        )
    )
    return True


def publishers_whole(session, tenant_id: str) -> list[dict]:
    """One ``all`` selector per publisher *tenant_id* holds.

    What a seeded product sells: every publisher whole, each named explicitly (#1845). Seed
    the publishers first; a tenant with none gets ``[]``, a product buyers are not offered.
    """
    session.flush()  # the session may not autoflush, and the caller has just added the publishers
    return [
        {"publisher_domain": publisher.domain, "selection_type": "all"}
        for publisher in PublisherRepository(session, tenant_id).list_all()
    ]


def whole_publisher_selections(session, tenant_id: str) -> list[ProductPublisherSelection]:
    """The selection rows of a product selling every publisher of *tenant_id* whole (:func:`publishers_whole`)."""
    return ProductPublisherSelection.from_selectors(tenant_id, publishers_whole(session, tenant_id))


def seed_verified_website(session, tenant_id: str, domain: str, name: str | None = None) -> bool:
    """Give *tenant_id* the publisher *domain*, whose stored adagents.json authorizes it for one website.

    The document a seeder stores for a publisher it controls (``publisher_ingest.website_document``),
    its ``property_id`` the domain spelled to the property-id pattern, stored without a fetch
    (``publisher_ingest.seed_document``). Returns True when it created the publisher. Does
    NOT commit, for the same reason as :func:`seed_product`.

    The document is fresh for 24 hours from now, like a fetched one; past that the
    scheduled refresh must fetch the publisher's real file for the tenant to keep selling.
    """
    from src.core.database.repositories.tenant_config import TenantConfigRepository
    from src.services.publisher_ingest import seed_document, website_document, website_property_id

    session.flush()  # a tenant the seeder has just added is read back below
    tenant = TenantConfigRepository(session, tenant_id).get_tenant()
    if tenant is None:
        return False
    document = website_document(
        tenant.agent_url, domain=domain, property_id=website_property_id(domain), name=name or domain
    )
    return seed_document(session, tenant_id, domain, document, display_name=name)

"""The one way a seeder puts a product in a tenant's catalogue.

A product is never one row. It is a ``Product`` plus the ``PricingOption`` that makes it
quotable, and a catalogue missing the second half is a catalogue that answers
``get_products`` with prices nobody can buy at. Three seeders each spelled that pair out
in full -- the CI tenant, the CI isolation tenant and the storyboard tenant -- so a field
added to either row had to be found in three places, and the day one copy was missed the
symptom surfaced storyboards later as a conformance gap rather than as a seeding error.

The spec dict each seeder already writes is the input, unchanged; what moved here is the
operation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from src.core.database.models import PricingOption, Product

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
            format_ids=spec["formats"],
            targeting_template=spec["targeting_template"],
            delivery_type=spec["delivery_type"],
            property_tags=["all_inventory"],  # Required per AdCP spec
            measurement=None,
            creative_policy=None,
            price_guidance=None,
            countries=None,
            implementation_config=None,
            properties=None,  # property_tags instead
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

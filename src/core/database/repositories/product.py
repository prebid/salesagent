"""Product repository — tenant-scoped data access for products.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.core.database.models import PricingOption, Product
from src.core.format_satisfaction import refuse_outside_common_intersection
from src.core.publisher_format_catalog import agent_host_refusal

if TYPE_CHECKING:
    from src.core.helpers.publisher_property_helpers import SelectableProperty

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OfferedProduct:
    """A product buyers are offered, with the ``publisher_properties`` it names (#1845)."""

    product: Product
    publisher_properties: list[dict]


def offered_products(
    products: Iterable[Product], authorized_properties: Sequence[SelectableProperty]
) -> list[OfferedProduct]:
    """The *products* buyers are offered: each naming a publisher of the *authorized_properties*.

    *authorized_properties* are the tenant's VERIFIED properties
    (``AuthorizedPropertyRepository.list_refs``). AdCP 3.2.7 ``core/product.json`` requires
    ``publisher_properties`` with ``minItems: 1``, and each entry names the publisher whose
    adagents.json a buyer verifies the seller against
    (``governance/property/authorized-properties.mdx``), so a product resolving to none has no
    entry it can truthfully make. It is a seller-side configuration gap: logged, marked "Not
    offered to buyers" on the admin products page, and left out here, so every reader that
    goes through this function -- ``get_products``, the catalog, ``create_media_buy`` -- treats
    it as unknown.
    """
    offered = []
    for product in products:
        publisher_properties = product.resolve_publisher_properties(authorized_properties)
        refusal = offer_refusal(product, publisher_properties)
        if refusal is None:
            offered.append(OfferedProduct(product, publisher_properties))
        else:
            logger.warning("Product %s is not offered: %s", product.product_id, refusal)
    return offered


def offer_refusal(product: Product, publisher_properties: list[dict]) -> str | None:
    """Why buyers are not offered *product*, or ``None`` when they are.

    *publisher_properties* is what :meth:`Product.resolve_publisher_properties` resolved for it.
    A product is also withheld when a format option names the agent host as its publisher:
    that option names a catalog no file publishes, so no buyer can resolve it, and
    ``core/product.json`` ``format_options`` makes an unresolved reference "fail closed"
    (``src/core/publisher_format_catalog.py``).
    """
    if not publisher_properties:
        return (
            "its property selection names no publisher this seller holds a verified authorized property of. "
            "Add and verify the publisher's properties under Authorized Properties."
        )
    declarations = product.format_contract.format_options or ()
    # The tenant is read only for a contract with a publisher-scoped option: a dynamic variant
    # reaches here detached (#2291), and product-local declarations name no host to compare.
    if not any(declaration.get("publisher_domain") for declaration in declarations):
        return None
    return agent_host_refusal(declarations, product.tenant.virtual_host_name)


class ProductRepository:
    """Tenant-scoped data access for Product.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation — there is no way to query across tenants.

    Write methods add objects to the session but never commit — the Unit of Work
    handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "product_id"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Single Product lookups
    # ------------------------------------------------------------------

    def get_by_id(self, product_id: str) -> Product | None:
        """Get a product by its ID within the tenant."""
        return self._session.scalars(
            select(Product).where(
                Product.tenant_id == self._tenant_id,
                Product.product_id == product_id,
            )
        ).first()

    def get_by_id_with_pricing(self, product_id: str) -> Product | None:
        """Get a product by ID with pricing_options eagerly loaded."""
        return self._session.scalars(
            select(Product)
            .options(joinedload(Product.pricing_options))
            .where(
                Product.tenant_id == self._tenant_id,
                Product.product_id == product_id,
            )
        ).first()

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def list_all(self) -> list[Product]:
        """Get all products for the tenant, ordered by product_id.

        Eagerly loads pricing_options and tenant to avoid N+1 queries.
        """
        stmt = (
            select(Product)
            .options(joinedload(Product.pricing_options), joinedload(Product.tenant))
            .where(Product.tenant_id == self._tenant_id)
            .order_by(Product.product_id)
        )
        return list(self._session.execute(stmt).unique().scalars().all())

    def list_dynamic_templates(self) -> list[Product]:
        """Live dynamic product templates for the tenant.

        A TEMPLATE, not a variant: ``is_dynamic`` marks the row the signals-driven
        variants are generated FROM, and the generated variants carry
        ``is_dynamic_variant`` instead. ``archived_at IS NULL`` keeps retired
        templates from producing new variants.

        Deliberately not ``list_all()`` filtered in Python: that query fetches every
        product for the tenant with two eager loads, so filtering afterwards would
        load the whole catalogue to find a handful of templates.
        """
        stmt = select(Product).where(
            Product.tenant_id == self._tenant_id,
            Product.is_dynamic.is_(True),
            Product.archived_at.is_(None),
        )
        return list(self._session.scalars(stmt).all())

    def list_for_offer(self, product_ids: Collection[str] | None = None) -> list[Product]:
        """The tenant's products, or only *product_ids*, loaded for :func:`offered_products` and conversion.

        Pricing options, inventory profile and tenant are loaded eagerly, ordered by product_id.
        """
        stmt = (
            select(Product)
            .options(
                selectinload(Product.pricing_options),
                selectinload(Product.inventory_profile),
                selectinload(Product.tenant),
            )
            .where(Product.tenant_id == self._tenant_id)
            .order_by(Product.product_id)
        )
        if product_ids is not None:
            stmt = stmt.where(Product.product_id.in_(list(product_ids)))
        return list(self._session.scalars(stmt).all())

    def list_by_ids(self, product_ids: list[str]) -> list[Product]:
        """Get products by a list of IDs within the tenant.

        Eagerly loads pricing_options and tenant.
        """
        if not product_ids:
            return []
        stmt = (
            select(Product)
            .options(joinedload(Product.pricing_options), joinedload(Product.tenant))
            .where(
                Product.tenant_id == self._tenant_id,
                Product.product_id.in_(product_ids),
            )
        )
        return list(self._session.execute(stmt).unique().scalars().all())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(self, product: Product) -> Product:
        """Persist a new product within this tenant.

        The product.tenant_id must match the repository's tenant_id.
        Raises ValueError if there is a tenant mismatch, or if the product sells several
        publishers without placements and declares an option not all of them accept
        (:func:`~src.core.format_satisfaction.refuse_outside_common_intersection`).

        Does NOT commit — the UoW handles that.
        """
        if product.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: product.tenant_id={product.tenant_id!r} != repository tenant_id={self._tenant_id!r}"
            )
        refuse_outside_common_intersection(product)
        self._session.add(product)
        self._session.flush()
        return product

    def update_fields(self, product_id: str, **kwargs: Any) -> Product | None:
        """Update arbitrary fields on a product within this tenant.

        Only updates fields that are valid Product column attributes.
        Returns the updated Product, or None if not found in this tenant.
        Raises ValueError if any kwarg is not a valid Product attribute or
        if the caller attempts to update an immutable field (tenant_id,
        product_id), and for the updated product :meth:`create` refuses.
        """
        blocked = self._IMMUTABLE_FIELDS & kwargs.keys()
        if blocked:
            raise ValueError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")
        product = self.get_by_id(product_id)
        if product is None:
            return None
        for key, value in kwargs.items():
            if not hasattr(product, key):
                raise ValueError(f"Product has no attribute {key!r}")
            setattr(product, key, value)
        refuse_outside_common_intersection(product)
        self._session.flush()
        return product

    # ------------------------------------------------------------------
    # PricingOption queries
    # ------------------------------------------------------------------

    def get_all_pricing_options(self) -> list[PricingOption]:
        """Get all pricing options for the tenant."""
        return list(
            self._session.scalars(
                select(PricingOption).where(
                    PricingOption.tenant_id == self._tenant_id,
                )
            ).all()
        )

"""Product repository — tenant-scoped data access for products.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.core.database.integrity import resolve_or_write
from src.core.database.models import PricingOption, Product
from src.core.database.product_pricing import pricing_option_terms
from src.core.database.repositories.effects import SessionEffectsMixin
from src.core.format_satisfaction import refuse_outside_common_intersection

logger = logging.getLogger(__name__)


class ProductRepository(SessionEffectsMixin):
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

    def get_by_id_with_pricing(self, product_id: str, *, refresh: bool = False) -> Product | None:
        """Get a product by ID with pricing_options eagerly loaded."""
        return self._session.scalars(
            select(Product)
            .options(joinedload(Product.pricing_options))
            .execution_options(populate_existing=refresh)
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

    def list_all_with_inventory(self) -> list[Product]:
        """Get all products with pricing, inventory profile, and tenant loaded.

        Used by get_product_catalog which needs full product data for conversion.
        """
        stmt = (
            select(Product)
            .options(
                selectinload(Product.pricing_options),
                selectinload(Product.inventory_profile),
                selectinload(Product.tenant),
            )
            .where(Product.tenant_id == self._tenant_id)
        )
        return list(self._session.scalars(stmt).all())

    def list_dynamic_templates(self) -> list[Product]:
        """Active templates with the pricing their generated variants inherit."""
        stmt = (
            select(Product)
            .options(selectinload(Product.pricing_options), selectinload(Product.inventory_profile))
            .where(Product.tenant_id == self._tenant_id, Product.is_dynamic.is_(True), Product.archived_at.is_(None))
            .order_by(Product.product_id)
        )
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

    def get_or_create_variant(self, template: Product, variant_id: str, build: Callable[[], Product]) -> Product:
        """Create or repair one variant; a concurrent winner owns its existing pricing."""
        if template.tenant_id != self._tenant_id:
            raise ValueError("Template must belong to the repository tenant")
        with self.savepoint():
            candidate = self.get_by_id_with_pricing(variant_id)
            is_new = candidate is None
            if candidate is None:
                candidate = build()

            def complete_variant() -> Product | None:
                row = self.get_by_id_with_pricing(variant_id, refresh=True)
                return row if row is not None and row.pricing_options else None

            def write() -> None:
                self.inherit_missing_variant_pricing(candidate, template)
                if is_new:
                    self.create(candidate)

            existing = resolve_or_write(
                self._session,
                conflict=complete_variant,
                write=write,
                constraint=("products_pkey", "uq_pricing_options_option_id"),
            )
            variant = existing if existing is not None else candidate
            variant.last_synced_at = datetime.now(UTC)
            expiration = datetime.now(UTC) + timedelta(days=template.variant_ttl_days or 30)
            if variant.expires_at is None or variant.expires_at < expiration:
                variant.expires_at = expiration
        return variant

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

    def inherit_missing_variant_pricing(self, variant: Product, template: Product) -> None:
        """Complete an unpriced variant once, preserving any existing pricing."""
        if variant.tenant_id != self._tenant_id or template.tenant_id != self._tenant_id:
            raise ValueError("Variant and template must belong to the repository tenant")
        if variant.pricing_options:
            return
        variant.pricing_options = [
            PricingOption.create(
                tenant_id=self._tenant_id,
                product_id=variant.product_id,
                pricing_option_id=option.pricing_option_id,
                **pricing_option_terms(option),
            )
            for option in template.pricing_options
        ]

    def get_all_pricing_options(self) -> list[PricingOption]:
        """Get all pricing options for the tenant."""
        return list(
            self._session.scalars(
                select(PricingOption).where(
                    PricingOption.tenant_id == self._tenant_id,
                )
            ).all()
        )

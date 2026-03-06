"""Product repository — tenant-scoped data access for products.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-rn59
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from src.core.database.models import Product


class ProductRepository:
    """Tenant-scoped data access for Product.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Products are always eager-loaded with pricing_options, inventory_profile,
    and tenant relationships to avoid DetachedInstanceError and N+1 queries.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def _base_query(self) -> Select[tuple[Product]]:
        """Base query with tenant filter and eager loading."""
        return (
            select(Product)
            .filter_by(tenant_id=self._tenant_id)
            .options(
                selectinload(Product.pricing_options),
                selectinload(Product.inventory_profile),
                selectinload(Product.tenant),
            )
        )

    # ------------------------------------------------------------------
    # Single Product lookups
    # ------------------------------------------------------------------

    def get_by_id(self, product_id: str) -> Product | None:
        """Get a product by its ID within the tenant."""
        return self._session.scalars(self._base_query().where(Product.product_id == product_id)).first()

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def get_all_for_tenant(self) -> list[Product]:
        """Get all products for the tenant, ordered by product_id.

        Products are returned with pricing_options, inventory_profile, and
        tenant relationships eager-loaded.
        """
        return list(self._session.scalars(self._base_query().order_by(Product.product_id)).all())

"""Tenant-scoped reads of the GAM orders an inventory sync stored (``gam_orders``)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import GAMOrder
from src.core.database.repositories.base import Repository
from src.core.operator_errors import OrderNotFound


class GAMOrderRepository(Repository):
    """Tenant-scoped access to the stored GAM orders.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    NOT_FOUND = OrderNotFound

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get(self, order_id: str) -> GAMOrder | None:
        """The stored order *order_id* of this tenant, or ``None``."""
        return self._session.scalars(select(GAMOrder).filter_by(tenant_id=self._tenant_id, order_id=order_id)).first()

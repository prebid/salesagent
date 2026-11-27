"""Tenant-scoped reads of the GAM orders and line items an orders sync stored (``gam_orders``, ``gam_line_items``)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import GAMLineItem, GAMOrder
from src.core.database.repositories.base import Repository
from src.core.operator_errors import LineItemNotFound, OrderNotFound


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


class GAMLineItemRepository(Repository):
    """Tenant-scoped access to the stored GAM line items.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    NOT_FOUND = LineItemNotFound

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get(self, line_item_id: str) -> GAMLineItem | None:
        """The stored line item *line_item_id* of this tenant, or ``None``."""
        return self._session.scalars(
            select(GAMLineItem).filter_by(tenant_id=self._tenant_id, line_item_id=line_item_id)
        ).first()

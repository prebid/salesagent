"""GAM inventory repository — tenant-scoped reads on ``gam_inventory``.

``gam_inventory`` is the synced copy of a tenant's Google Ad Manager inventory. The sync
(``GAMInventoryService``) writes it in bulk batches on its own session; what the sync
reads first, to tell an item it already holds from a new one, comes from here, and so do
the counts the admin inventory views show.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import GAMInventory


class GAMInventoryRepository:
    """Tenant-scoped read access to synced GAM inventory.

    All queries filter by tenant_id automatically.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def row_ids(self, inventory_type: str | None = None) -> dict[tuple[str, str], int]:
        """The row id of every synced item, keyed by ``(inventory_type, inventory_id)``.

        What a sync reads before it writes, to update an item it already holds instead of
        inserting it again. *inventory_type* narrows it to one type.
        """
        stmt = select(GAMInventory.inventory_type, GAMInventory.inventory_id, GAMInventory.id).where(
            GAMInventory.tenant_id == self._tenant_id
        )
        if inventory_type is not None:
            stmt = stmt.where(GAMInventory.inventory_type == inventory_type)
        return {(kind, inventory_id): row_id for kind, inventory_id, row_id in self._session.execute(stmt).tuples()}

    def inventory_ids_by_name(self, inventory_type: str) -> dict[str, str]:
        """The GAM ``inventory_id`` of every synced item of *inventory_type*, keyed by its name."""
        stmt = select(GAMInventory.name, GAMInventory.inventory_id).where(
            GAMInventory.tenant_id == self._tenant_id,
            GAMInventory.inventory_type == inventory_type,
        )
        return dict(self._session.execute(stmt).tuples().all())

    def counts_by_type_and_status(self) -> dict[str, dict[str, int]]:
        """How many synced items there are of each inventory type, per status."""
        stmt = (
            select(GAMInventory.inventory_type, GAMInventory.status, func.count())
            .where(GAMInventory.tenant_id == self._tenant_id)
            .group_by(GAMInventory.inventory_type, GAMInventory.status)
        )
        counts: dict[str, dict[str, int]] = {}
        for inventory_type, status, count in self._session.execute(stmt).tuples():
            counts.setdefault(inventory_type, {})[status] = count
        return counts

    def last_synced_at(self) -> datetime | None:
        """When the most recently synced item was synced; None before the first sync."""
        return self._session.scalar(
            select(func.max(GAMInventory.last_synced)).where(GAMInventory.tenant_id == self._tenant_id)
        )

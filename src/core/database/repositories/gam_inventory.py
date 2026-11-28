"""GAM inventory repository — tenant-scoped reads and writes on ``gam_inventory``.

``gam_inventory`` is the synced copy of a tenant's Google Ad Manager inventory. The sync
(``GAMInventoryService``) writes it through :meth:`GAMInventoryRepository.upsert` on its own
session and commits each batch itself; the counts and sync times the admin inventory views
show come from here too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import GAMInventory

#: The kinds of GAM item the sync writes, as ``gam_inventory.inventory_type`` stores them.
InventoryType = Literal[
    "ad_unit", "placement", "label", "custom_targeting_key", "custom_targeting_value", "audience_segment"
]

#: The columns of ``uq_gam_inventory``: what names one synced item, so an upsert never rewrites them.
_ITEM_KEY = frozenset({"tenant_id", "inventory_type", "inventory_id"})


@dataclass(frozen=True)
class InventoryCounts:
    """How many synced items a tenant holds, per inventory type and status (:meth:`GAMInventoryRepository.counts`)."""

    by_type_and_status: dict[str, dict[str, int]]

    def total(self, inventory_type: InventoryType | None = None, *, status: str | None = None) -> int:
        """How many items there are; *inventory_type* and *status* each narrow the count to one value."""
        return sum(
            count
            for kind, statuses in self.by_type_and_status.items()
            if inventory_type is None or kind == inventory_type
            for state, count in statuses.items()
            if status is None or state == status
        )

    def by_type(self) -> dict[str, int]:
        """How many items there are of each inventory type the tenant holds, every status included."""
        return {kind: sum(statuses.values()) for kind, statuses in self.by_type_and_status.items()}


class GAMInventoryRepository:
    """Tenant-scoped access to synced GAM inventory.

    All queries filter by tenant_id automatically, and every write is to that tenant.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def upsert(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Write *rows*: insert each item the tenant does not hold, and update the one it does in place.

        ``uq_gam_inventory`` (tenant, type, GAM id) is the conflict key, so a held item keeps
        its row id and no read of the held ids comes first. An update rewrites every column a
        row carries except that key. Each row is written to this repository's tenant, whatever
        its own ``tenant_id`` says. Nothing is committed: the caller owns the transaction.

        Every row draws an id from the table's sequence, a held one included, as PostgreSQL
        does for any ``INSERT ... ON CONFLICT``. A full sync already draws one per item, since
        it deletes the tenant's rows before writing them.
        """
        if not rows:
            return
        values = [{**row, "tenant_id": self._tenant_id} for row in rows]
        insert = pg_insert(GAMInventory)
        updated = {column: insert.excluded[column] for column in values[0].keys() - _ITEM_KEY}
        # ON CONFLICT runs no column ``onupdate``, so the update stamps updated_at itself. The
        # rows go as one executemany, which SQLAlchemy batches under one compiled statement: a
        # VALUES list per batch compiles anew each time and measured three times slower.
        self._session.execute(
            insert.on_conflict_do_update(constraint="uq_gam_inventory", set_={**updated, "updated_at": func.now()}),
            values,
        )

    def inventory_ids_by_name(self, inventory_type: InventoryType) -> dict[str, str]:
        """The GAM ``inventory_id`` of every synced item of *inventory_type*, keyed by its name."""
        stmt = select(GAMInventory.name, GAMInventory.inventory_id).where(
            GAMInventory.tenant_id == self._tenant_id,
            GAMInventory.inventory_type == inventory_type,
        )
        return dict(self._session.execute(stmt).tuples().all())

    def counts(self) -> InventoryCounts:
        """How many synced items there are of each inventory type, per status."""
        stmt = (
            select(GAMInventory.inventory_type, GAMInventory.status, func.count())
            .where(GAMInventory.tenant_id == self._tenant_id)
            .group_by(GAMInventory.inventory_type, GAMInventory.status)
        )
        by_type_and_status: dict[str, dict[str, int]] = {}
        for inventory_type, status, count in self._session.execute(stmt).tuples():
            by_type_and_status.setdefault(inventory_type, {})[status] = count
        return InventoryCounts(by_type_and_status)

    def last_synced_at(self, inventory_type: InventoryType | None = None) -> datetime | None:
        """When the most recently synced item was synced; None before the first sync.

        *inventory_type* narrows it to one type.
        """
        stmt = select(func.max(GAMInventory.last_synced)).where(GAMInventory.tenant_id == self._tenant_id)
        if inventory_type is not None:
            stmt = stmt.where(GAMInventory.inventory_type == inventory_type)
        return self._session.scalar(stmt)

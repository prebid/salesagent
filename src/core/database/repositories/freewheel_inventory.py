"""FreeWheelInventory repository — tenant-scoped access to the FW inventory cache.

Centralizes all reads against the ``freewheel_inventory`` table so callers
don't issue raw ``select()`` statements outside of a repository (enforced
by the no-raw-select structural guard).

Core invariant: every query filters by ``tenant_id``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import FreeWheelInventory


class FreeWheelInventoryRepository:
    """Tenant-scoped access for the FW inventory cache.

    All queries filter by ``tenant_id``. Used by the product setup UI
    (via the admin inventory query endpoint) and by the inventory sync
    service (for reads — the sync writes go through bulk ``ON CONFLICT``
    upserts in :class:`FreeWheelInventorySync`).
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def list_by_type(
        self,
        entity_type: str,
        *,
        parent_id: str | None = None,
    ) -> list[FreeWheelInventory]:
        """Return all cached rows for ``entity_type`` (optionally narrowed to
        children of ``parent_id``). Results aren't paginated — callers expect
        the full set for UI dropdown population."""
        stmt = select(FreeWheelInventory).filter_by(tenant_id=self._tenant_id, entity_type=entity_type)
        if parent_id is not None:
            stmt = stmt.filter_by(parent_id=parent_id)
        return list(self._session.scalars(stmt).all())

    def search(
        self,
        entity_type: str,
        *,
        q: str | None = None,
        parent_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[FreeWheelInventory]:
        """Search cached FreeWheel inventory for embedded product authoring."""
        stmt = select(FreeWheelInventory).filter_by(tenant_id=self._tenant_id, entity_type=entity_type)
        if parent_id is not None:
            stmt = stmt.filter_by(parent_id=parent_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where((FreeWheelInventory.name.ilike(pattern)) | (FreeWheelInventory.entity_id.ilike(pattern)))
        stmt = (
            stmt.order_by(FreeWheelInventory.name.asc(), FreeWheelInventory.entity_id.asc()).offset(offset).limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def latest_sync_at(self) -> datetime | None:
        """Return the most recent ``last_synced_at`` across all cached entities
        for this tenant, or ``None`` if the tenant has never synced. The
        freshness banner uses this to flag a stale cache."""
        stmt = select(func.max(FreeWheelInventory.last_synced_at)).filter_by(tenant_id=self._tenant_id)
        return self._session.scalar(stmt)

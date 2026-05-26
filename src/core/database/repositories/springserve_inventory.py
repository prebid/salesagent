"""Repository for the SpringServe inventory taxonomy cache.

Tenant-scoped reads and bulk upserts over ``springserve_inventory``.
Reads feed the SpringServe adapter product-configuration UI; writes
come from :class:`SpringServeInventorySync`.

Core invariant: every query filters by ``tenant_id``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import SpringServeInventory


class SpringServeInventoryRepository:
    """Tenant-scoped access for the SpringServe inventory cache."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def list_by_type(
        self,
        entity_type: str,
        *,
        supply_partner_id: str | None = None,
        supply_router_id: str | None = None,
        key_id: str | None = None,
    ) -> list[SpringServeInventory]:
        """Return cached rows of one entity_type, optionally filtered by FK.

        At most one FK filter is expected per call -- the natural pairings:

        * ``entity_type="supply_router", supply_partner_id=X``
        * ``entity_type="supply_tag", supply_router_id=X`` (tags in a router)
        * ``entity_type="supply_tag", supply_partner_id=X`` (all tags incl. orphans)
        * ``entity_type="value_list", key_id=X``
        """
        stmt = select(SpringServeInventory).filter_by(tenant_id=self._tenant_id, entity_type=entity_type)
        if supply_partner_id is not None:
            stmt = stmt.filter(SpringServeInventory.supply_partner_id == supply_partner_id)
        if supply_router_id is not None:
            stmt = stmt.filter(SpringServeInventory.supply_router_id == supply_router_id)
        if key_id is not None:
            stmt = stmt.filter(SpringServeInventory.key_id == key_id)
        return list(self._session.scalars(stmt).all())

    def search(
        self,
        entity_type: str,
        *,
        q: str | None = None,
        parent_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[SpringServeInventory]:
        """Search cached SpringServe inventory for embedded product authoring."""
        stmt = select(SpringServeInventory).filter_by(tenant_id=self._tenant_id, entity_type=entity_type)
        if parent_id is not None:
            if entity_type == "supply_router":
                stmt = stmt.filter(SpringServeInventory.supply_partner_id == parent_id)
            elif entity_type == "supply_tag":
                stmt = stmt.filter(SpringServeInventory.supply_router_id == parent_id)
            elif entity_type == "value_list":
                stmt = stmt.filter(SpringServeInventory.key_id == parent_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                (SpringServeInventory.name.ilike(pattern)) | (SpringServeInventory.entity_id.ilike(pattern))
            )
        stmt = (
            stmt.order_by(SpringServeInventory.name.asc(), SpringServeInventory.entity_id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def bulk_upsert(self, rows: Iterable[dict]) -> int:
        """Insert or update inventory rows. ``rows`` items must carry
        ``entity_type``, ``entity_id``, ``raw_json`` at minimum;
        ``tenant_id`` is forced to the repository's scope.

        Returns the number of rows touched.
        """
        payloads = [{**row, "tenant_id": self._tenant_id} for row in rows]
        if not payloads:
            return 0
        stmt = pg_insert(SpringServeInventory).values(payloads)
        update_cols = {
            col.name: stmt.excluded[col.name]
            for col in SpringServeInventory.__table__.columns
            if col.name not in ("tenant_id", "entity_type", "entity_id")
        }
        stmt = stmt.on_conflict_do_update(index_elements=["tenant_id", "entity_type", "entity_id"], set_=update_cols)
        result = self._session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

    def latest_sync_at(self) -> datetime | None:
        """Return the most recent ``last_synced_at`` for this tenant, or
        ``None`` if the inventory sync has never run."""
        stmt = select(func.max(SpringServeInventory.last_synced_at)).filter_by(tenant_id=self._tenant_id)
        return self._session.scalar(stmt)

    def delete_all(self) -> int:
        """Wipe the tenant's inventory cache. Used when an operator triggers
        a full resync via the admin UI."""
        from sqlalchemy import delete

        stmt = delete(SpringServeInventory).filter_by(tenant_id=self._tenant_id)
        result = self._session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

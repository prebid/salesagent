"""GAM sync repository — tenant-scoped reads/writes for synced GAM data.

The ``gam_advertisers``, ``gam_orders``, and ``gam_line_items`` tables
are read-mostly caches hydrated by the sync workers. This repository
gives non-sync code (the buyer-routing UI, the get_media_buys
projection) typed access without dropping to raw selects.

Core invariant: every query includes ``tenant_id`` in the WHERE clause.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import GamAdvertiser, GAMInventory, GAMLineItem, GAMOrder


class GAMSyncRepository:
    """Tenant-scoped access to synced GAM advertiser/order/line-item data."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Advertisers
    # ------------------------------------------------------------------

    def list_advertisers(self) -> list[GamAdvertiser]:
        """All advertisers in the synced cache, ordered by name."""
        return list(
            self._session.scalars(
                select(GamAdvertiser)
                .where(GamAdvertiser.tenant_id == self._tenant_id)
                .order_by(GamAdvertiser.name.asc(), GamAdvertiser.advertiser_id.asc())
            ).all()
        )

    def get_advertiser(self, advertiser_id: str) -> GamAdvertiser | None:
        return self._session.scalars(
            select(GamAdvertiser).filter_by(tenant_id=self._tenant_id, advertiser_id=advertiser_id)
        ).first()

    def list_advertiser_ids_assigned_to(self, principal_id: str) -> list[str]:
        """Advertiser ids whose ``principal_id`` matches the given agent."""
        rows = self._session.scalars(
            select(GamAdvertiser.advertiser_id).where(
                GamAdvertiser.tenant_id == self._tenant_id,
                GamAdvertiser.principal_id == principal_id,
            )
        ).all()
        return list(rows)

    # ------------------------------------------------------------------
    # Orders + line items (read-only projection inputs)
    # ------------------------------------------------------------------

    def list_orders_for_advertisers(self, advertiser_ids: list[str]) -> list[GAMOrder]:
        if not advertiser_ids:
            return []
        return list(
            self._session.scalars(
                select(GAMOrder).where(
                    GAMOrder.tenant_id == self._tenant_id,
                    GAMOrder.advertiser_id.in_(advertiser_ids),
                )
            ).all()
        )

    def list_line_items_for_orders(self, order_ids: list[str]) -> list[GAMLineItem]:
        if not order_ids:
            return []
        return list(
            self._session.scalars(
                select(GAMLineItem).where(
                    GAMLineItem.tenant_id == self._tenant_id,
                    GAMLineItem.order_id.in_(order_ids),
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # GAMInventory readers — fuel the signals bulk-map UI
    # ------------------------------------------------------------------

    def count_inventory(self, inventory_type: str) -> int:
        """Number of synced GAM inventory rows of one type for the tenant.

        Used by the dashboard's Job 1 coverage hint (#485) as the
        denominator: "N of M ad units in a bundle."
        """
        return (
            self._session.scalar(
                select(func.count())
                .select_from(GAMInventory)
                .where(
                    GAMInventory.tenant_id == self._tenant_id,
                    GAMInventory.inventory_type == inventory_type,
                )
            )
            or 0
        )

    def list_inventory_not_in_set(
        self,
        inventory_types: tuple[str, ...],
        bundled_ids_by_type: dict[str, set[str]],
        limit: int,
    ) -> list[GAMInventory]:
        """Return GAMInventory rows whose ``(inventory_type, inventory_id)``
        is not in the corresponding ``bundled_ids_by_type[inventory_type]``.

        Used by the inventory-bundles list page's "What's not bundled" rail
        (#485 follow-up): the caller computes which entities are currently
        referenced by some ``InventoryProfile`` (via the
        ``InventoryBundleReference`` denormalization) and passes the set in.

        Ordered by ``inventory_type`` then ``name`` so placements surface
        first (they cascade — bundling a placement covers its children).
        """
        if not inventory_types:
            return []
        stmt = select(GAMInventory).where(
            GAMInventory.tenant_id == self._tenant_id,
            GAMInventory.inventory_type.in_(inventory_types),
        )
        for inv_type, ids in bundled_ids_by_type.items():
            if not ids:
                continue
            stmt = stmt.where(~((GAMInventory.inventory_type == inv_type) & (GAMInventory.inventory_id.in_(ids))))
        stmt = stmt.order_by(GAMInventory.inventory_type, GAMInventory.name).limit(limit)
        return list(self._session.scalars(stmt).all())

    def list_inventory(self, inventory_type: str, limit: int | None = None) -> list[GAMInventory]:
        """Return synced GAM inventory rows of one type
        (``audience_segment``, ``custom_targeting_key``, …) ordered by
        name. Empty when the tenant hasn't synced.

        ``limit`` caps the result set — used by the bundle list page's
        seed-suggestions peek (#481) where a fresh tenant with thousands
        of placements just needs the first few.
        """
        stmt = (
            select(GAMInventory)
            .where(
                GAMInventory.tenant_id == self._tenant_id,
                GAMInventory.inventory_type == inventory_type,
            )
            .order_by(GAMInventory.name)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    def search_inventory(
        self,
        inventory_type: str,
        *,
        q: str | None = None,
        parent_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[GAMInventory]:
        """Search synced GAM inventory rows for embedded product authoring."""
        stmt = select(GAMInventory).where(
            GAMInventory.tenant_id == self._tenant_id,
            GAMInventory.inventory_type == inventory_type,
        )
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where((GAMInventory.name.ilike(pattern)) | (GAMInventory.inventory_id.ilike(pattern)))
        if parent_id is not None:
            stmt = stmt.where(GAMInventory.inventory_metadata["parent_id"].astext == str(parent_id))
        stmt = stmt.order_by(GAMInventory.name.asc(), GAMInventory.inventory_id.asc()).offset(offset).limit(limit)
        return list(self._session.scalars(stmt).all())

    def list_values_for_key(self, key_id: str) -> list[GAMInventory]:
        """Custom-targeting-value rows for one key, ordered by name.

        Cache for the signals bulk-map UI's "click key → see values" path.
        Persisted lazily on first live GAM fetch (see
        ``inventory.get_targeting_values``) and during bulk sync when
        operators opt into pre-fetching PREDEFINED-key values.
        """
        return list(
            self._session.scalars(
                select(GAMInventory)
                .where(
                    GAMInventory.tenant_id == self._tenant_id,
                    GAMInventory.inventory_type == "custom_targeting_value",
                    GAMInventory.inventory_metadata["custom_targeting_key_id"].astext == str(key_id),
                )
                .order_by(GAMInventory.name)
            ).all()
        )

    def list_values_for_keys(self, key_ids: set[str]) -> dict[str, list[GAMInventory]]:
        """Custom-targeting-value rows for several keys, grouped by key ID.

        Used by the signals page to pre-render only keys that already have
        mapped values. Unmapped keys lazy-load values on expansion.
        """
        if not key_ids:
            return {}
        rows = self._session.scalars(
            select(GAMInventory)
            .where(
                GAMInventory.tenant_id == self._tenant_id,
                GAMInventory.inventory_type == "custom_targeting_value",
                GAMInventory.inventory_metadata["custom_targeting_key_id"].astext.in_(sorted(key_ids)),
            )
            .order_by(GAMInventory.inventory_metadata["custom_targeting_key_id"].astext, GAMInventory.name)
        ).all()
        grouped: dict[str, list[GAMInventory]] = {key_id: [] for key_id in key_ids}
        for row in rows:
            key_id = str((row.inventory_metadata or {}).get("custom_targeting_key_id") or "")
            if key_id:
                grouped.setdefault(key_id, []).append(row)
        return grouped

    def delete_values_for_key_except(self, key_id: str, keep_ids: set[str]) -> int:
        """Delete cached custom-targeting values for ``key_id`` not in ``keep_ids``.

        Per-key refreshes are replacement syncs: values removed upstream in
        GAM must disappear from the local cache too.
        """
        deleted = 0
        for row in self.list_values_for_key(key_id):
            if row.inventory_id in keep_ids:
                continue
            self._session.delete(row)
            deleted += 1
        return deleted

    def find_inventory_item(self, inventory_type: str, inventory_id: str) -> GAMInventory | None:
        """One inventory row by ``(inventory_type, inventory_id)``, or None."""
        return self._session.scalars(
            select(GAMInventory).filter_by(
                tenant_id=self._tenant_id,
                inventory_type=inventory_type,
                inventory_id=inventory_id,
            )
        ).first()

    def update_inventory_metadata(self, inventory_type: str, inventory_id: str, metadata: dict) -> GAMInventory | None:
        """Replace metadata for one tenant-scoped synced inventory row."""
        row = self.find_inventory_item(inventory_type, inventory_id)
        if row is None:
            return None
        row.inventory_metadata = metadata
        return row

    def list_inventory_by_ids(self, inventory_type: str, inventory_ids: list[str]) -> list[GAMInventory]:
        """Batch lookup: rows matching ``(inventory_type, inventory_id IN ...)``.

        Used by the bundle editor (#530) to resolve external IDs from
        ``inventory_config.ad_units`` / ``placements`` into human-readable
        names in a single query instead of N round-trips.
        """
        if not inventory_ids:
            return []
        return list(
            self._session.scalars(
                select(GAMInventory).where(
                    GAMInventory.tenant_id == self._tenant_id,
                    GAMInventory.inventory_type == inventory_type,
                    GAMInventory.inventory_id.in_(inventory_ids),
                )
            ).all()
        )

    def add(self, item: GAMInventory) -> None:
        """Add a new GAMInventory row. Caller commits.

        Used by the targeting-value lazy persistence path. Sync workers
        use bulk-batch upsert directly through Core SQL for perf — they
        don't go through this repository.
        """
        if item.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: item.tenant_id={item.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(item)

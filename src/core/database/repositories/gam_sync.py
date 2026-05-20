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

    def list_inventory(self, inventory_type: str) -> list[GAMInventory]:
        """Return synced GAM inventory rows of one type
        (``audience_segment``, ``custom_targeting_key``, …) ordered by
        name. Empty when the tenant hasn't synced.
        """
        return list(
            self._session.scalars(
                select(GAMInventory)
                .where(
                    GAMInventory.tenant_id == self._tenant_id,
                    GAMInventory.inventory_type == inventory_type,
                )
                .order_by(GAMInventory.name)
            ).all()
        )

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

    def find_inventory_item(self, inventory_type: str, inventory_id: str) -> GAMInventory | None:
        """One inventory row by ``(inventory_type, inventory_id)``, or None."""
        return self._session.scalars(
            select(GAMInventory).filter_by(
                tenant_id=self._tenant_id,
                inventory_type=inventory_type,
                inventory_id=inventory_id,
            )
        ).first()

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

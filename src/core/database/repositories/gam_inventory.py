"""GAM inventory repository: a tenant's ``gam_inventory`` rows, as a GAM sync discovered them."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.database.models import GAMInventory

if TYPE_CHECKING:
    from src.adapters.gam_inventory_discovery import CustomTargetingKey, CustomTargetingValue, GAMInventoryDiscovery

logger = logging.getLogger(__name__)

#: Rows written per commit, so a large inventory is held neither in memory nor in one transaction.
BATCH_SIZE = 500


def inventory_row(tenant_id: str, inventory_type: str, item: Any, sync_time: datetime) -> dict[str, Any]:
    """The ``gam_inventory`` row of one discovered *item* of *inventory_type*, synced at *sync_time*."""
    if inventory_type == "ad_unit":
        path, status = item.path, item.status.value
        metadata = {
            "ad_unit_code": item.ad_unit_code,
            "parent_id": item.parent_id,
            "description": item.description,
            "target_window": item.target_window,
            "explicitly_targeted": item.explicitly_targeted,
            "has_children": item.has_children,
            "sizes": item.sizes,
            "effective_applied_labels": item.effective_applied_labels,
        }
    elif inventory_type == "placement":
        path, status = [item.name], item.status  # Placements don't have hierarchy
        metadata = {
            "placement_code": item.placement_code,
            "description": item.description,
            "is_ad_sense_targeting_enabled": item.is_ad_sense_targeting_enabled,
            "ad_unit_ids": item.ad_unit_ids,
            "targeting_description": item.targeting_description,
        }
    elif inventory_type == "label":
        path, status = [item.name], "ACTIVE" if item.is_active else "INACTIVE"
        metadata = {"description": item.description, "ad_category": item.ad_category, "label_type": item.label_type}
    elif inventory_type == "custom_targeting_key":
        path, status = [item.display_name], item.status
        metadata = {
            "display_name": item.display_name,
            "type": item.type,  # PREDEFINED or FREEFORM
            "reportable_type": item.reportable_type,
        }
    elif inventory_type == "audience_segment":
        path, status = [item.type, item.name], item.status  # e.g. ["FIRST_PARTY", "Sports Enthusiasts"]
        metadata = {
            "description": item.description,
            "category_ids": item.category_ids,
            "type": item.type,  # FIRST_PARTY or THIRD_PARTY
            "size": item.size,
            "data_provider_name": item.data_provider_name,
            "segment_type": item.segment_type,  # RULE_BASED, SHARED, etc.
        }
    else:
        raise ValueError(f"Unknown inventory type: {inventory_type}")
    return _row(tenant_id, inventory_type, item.id, item.name, path, status, metadata, sync_time)


def targeting_value_row(
    tenant_id: str, value: CustomTargetingValue, key: CustomTargetingKey, sync_time: datetime
) -> dict[str, Any]:
    """The ``gam_inventory`` row of the custom targeting *value* of *key*, synced at *sync_time*."""
    metadata = {
        "custom_targeting_key_id": value.custom_targeting_key_id,
        "display_name": value.display_name,
        "match_type": value.match_type,
        "key_name": key.name,
        "key_display_name": key.display_name,
    }
    path = [key.display_name, value.display_name]
    return _row(tenant_id, "custom_targeting_value", value.id, value.name, path, value.status, metadata, sync_time)


def _row(
    tenant_id: str,
    inventory_type: str,
    inventory_id: str,
    name: str,
    path: list[str],
    status: str,
    metadata: dict[str, Any],
    sync_time: datetime,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "inventory_type": inventory_type,
        "inventory_id": inventory_id,
        "name": name,
        "path": path,
        "status": status,
        "inventory_metadata": metadata,
        "last_synced": sync_time,
    }


class GAMInventoryRepository:
    """Tenant-scoped writes of GAM inventory.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def save_discovery(self, discovery: GAMInventoryDiscovery) -> None:
        """Write every item *discovery* holds as this tenant's inventory, and mark what it no longer names stale.

        A row this tenant already has for an item is updated in place. Each type is
        committed in batches of :data:`BATCH_SIZE`, so a failure keeps the batches written
        before it. Ad units the discovery no longer names stay as they are; every other
        type is marked ``STALE``, committed when the unit of work closes.
        """
        sync_time = datetime.now(UTC)
        tenant_id = self._tenant_id
        existing = self._session.execute(
            select(GAMInventory.inventory_type, GAMInventory.inventory_id, GAMInventory.id).where(
                GAMInventory.tenant_id == tenant_id
            )
        ).all()
        existing_ids = {(row.inventory_type, row.inventory_id): row.id for row in existing}

        if not discovery.placements:
            logger.warning(
                f"No placements found in discovery object for tenant {tenant_id}. "
                f"This could indicate: (1) no placements exist in GAM, (2) placement discovery failed, "
                f"or (3) all placements are ARCHIVED"
            )

        inserted = updated = 0
        for rows in self._rows_by_type(discovery, sync_time):
            batch_inserted, batch_updated = self._write(rows, existing_ids)
            inserted += batch_inserted
            updated += batch_updated

        self._session.execute(
            update(GAMInventory)
            .where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.last_synced < sync_time - timedelta(seconds=1),
                GAMInventory.inventory_type != "ad_unit",  # Keep ad units active
            )
            .values(status="STALE")
        )
        logger.info(f"Saved inventory to database: {inserted} new, {updated} updated (batched)")

    def _rows_by_type(
        self, discovery: GAMInventoryDiscovery, sync_time: datetime
    ) -> Iterator[Iterator[dict[str, Any]]]:
        """The rows of *discovery*, one group per type; a custom targeting key's values follow the key."""
        tenant_id = self._tenant_id
        for inventory_type, items in (
            ("ad_unit", discovery.ad_units),
            ("placement", discovery.placements),
            ("label", discovery.labels),
        ):
            yield (inventory_row(tenant_id, inventory_type, item, sync_time) for item in items.values())
        yield (
            row
            for key in discovery.custom_targeting_keys.values()
            for row in (
                inventory_row(tenant_id, "custom_targeting_key", key, sync_time),
                *(
                    targeting_value_row(tenant_id, value, key, sync_time)
                    for value in discovery.custom_targeting_values.get(key.id, [])
                ),
            )
        )
        yield (
            inventory_row(tenant_id, "audience_segment", segment, sync_time)
            for segment in discovery.audience_segments.values()
        )

    def _write(self, rows: Iterable[dict[str, Any]], existing_ids: dict[tuple[str, str], int]) -> tuple[int, int]:
        """Insert or update *rows*, committing every :data:`BATCH_SIZE` rows and once at the end.

        Returns:
            How many rows were inserted, and how many updated.
        """
        inserted = updated = 0
        to_insert: list[dict[str, Any]] = []
        to_update: list[dict[str, Any]] = []
        for row in rows:
            row_id = existing_ids.get((row["inventory_type"], row["inventory_id"]))
            if row_id is None:
                to_insert.append(row)
            else:
                to_update.append({**row, "id": row_id})
            if len(to_insert) + len(to_update) >= BATCH_SIZE:
                inserted, updated = inserted + len(to_insert), updated + len(to_update)
                self._commit_batch(to_insert, to_update)
        inserted, updated = inserted + len(to_insert), updated + len(to_update)
        self._commit_batch(to_insert, to_update)
        return inserted, updated

    def _commit_batch(self, to_insert: list[dict[str, Any]], to_update: list[dict[str, Any]]) -> None:
        """Write and commit one batch, then empty it."""
        if to_insert:
            self._session.bulk_insert_mappings(GAMInventory.__mapper__, to_insert)
        if to_update:
            self._session.bulk_update_mappings(GAMInventory.__mapper__, to_update)
        self._session.commit()
        to_insert.clear()
        to_update.clear()

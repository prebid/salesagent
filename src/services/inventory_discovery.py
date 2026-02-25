"""Inventory discovery service — query synced inventory data.

Provides read access to GAMInventory records (ad units, placements,
targeting keys, audience segments, labels, sizes) that were synced
from the ad server.

Extracted from src/admin/blueprints/inventory.py Flask blueprint.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import GAMInventory
from src.core.exceptions import AdCPNotFoundError

logger = logging.getLogger(__name__)


class InventoryDiscoveryService:
    """Stateless service for querying synced inventory data."""

    def get_inventory(
        self,
        tenant_id: str,
        inventory_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Get synced inventory items (ad units, placements)."""
        with get_db_session() as session:
            stmt = select(GAMInventory).filter_by(tenant_id=tenant_id)

            if inventory_type:
                stmt = stmt.filter(GAMInventory.inventory_type == inventory_type)
            else:
                # Default: ad units and placements
                stmt = stmt.filter(GAMInventory.inventory_type.in_(["ad_unit", "placement"]))

            if status:
                stmt = stmt.filter(GAMInventory.status == status)

            if search:
                escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                stmt = stmt.filter(GAMInventory.name.ilike(f"%{escaped}%"))

            stmt = stmt.order_by(GAMInventory.name).limit(limit)
            items = session.scalars(stmt).all()

            return {
                "items": [self._to_dict(item) for item in items],
                "count": len(items),
                "inventory_type": inventory_type or "ad_unit,placement",
            }

    def get_sizes(self, tenant_id: str) -> dict[str, Any]:
        """Extract unique creative sizes from inventory metadata."""
        with get_db_session() as session:
            stmt = (
                select(GAMInventory)
                .filter_by(tenant_id=tenant_id)
                .filter(GAMInventory.inventory_type.in_(["ad_unit", "placement"]))
            )
            items = session.scalars(stmt).all()

            sizes: set[str] = set()
            for item in items:
                metadata = item.inventory_metadata or {}
                for size in metadata.get("sizes", []):
                    if isinstance(size, dict) and "width" in size and "height" in size:
                        sizes.add(f"{size['width']}x{size['height']}")
                    elif isinstance(size, str):
                        sizes.add(size)

            return {"sizes": sorted(sizes), "count": len(sizes)}

    def get_targeting(self, tenant_id: str) -> dict[str, Any]:
        """Get targeting data (custom keys, audiences, labels)."""
        with get_db_session() as session:
            result: dict[str, Any] = {
                "custom_targeting_keys": [],
                "audience_segments": [],
                "labels": [],
            }

            for inv_type, key in [
                ("custom_targeting_key", "custom_targeting_keys"),
                ("audience_segment", "audience_segments"),
                ("label", "labels"),
            ]:
                stmt = (
                    select(GAMInventory)
                    .filter_by(tenant_id=tenant_id, inventory_type=inv_type)
                    .order_by(GAMInventory.name)
                )
                items = session.scalars(stmt).all()
                result[key] = [self._to_dict(item) for item in items]

            return result

    def get_targeting_values(self, tenant_id: str, key_id: str) -> dict[str, Any]:
        """Get values for a specific targeting key.

        Checks synced data first. For GAM tenants, values may be
        fetched from the ad server on demand (via the adapter).
        """
        with get_db_session() as session:
            stmt = select(GAMInventory).filter_by(
                tenant_id=tenant_id, inventory_type="custom_targeting_key", inventory_id=key_id
            )
            key = session.scalars(stmt).first()
            if not key:
                raise AdCPNotFoundError(f"Targeting key '{key_id}' not found")

            metadata = key.inventory_metadata or {}
            values = metadata.get("values", [])

            return {
                "key_id": key_id,
                "key_name": key.name,
                "values": values,
                "count": len(values),
            }

    def _to_dict(self, item: GAMInventory) -> dict[str, Any]:
        """Convert GAMInventory ORM object to dict."""
        return {
            "id": item.id,
            "inventory_type": item.inventory_type,
            "inventory_id": item.inventory_id,
            "name": item.name,
            "path": item.path if item.path else None,
            "status": item.status,
            "metadata": item.inventory_metadata if item.inventory_metadata else {},
            "last_synced": item.last_synced.isoformat() if item.last_synced else None,
        }

"""
Service layer for GAM inventory management.

This service:
- Syncs inventory from GAM to database
- Provides inventory browsing and search
- Manages product-inventory mappings
- Handles inventory updates and caching
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, String, and_, delete, func, or_, select, text
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from src.adapters.gam_inventory_discovery import (
    GAMInventoryDiscovery,
)
from src.core.database.database_session import get_engine
from src.core.database.integrity import resolve_or_write
from src.core.database.models import GAMInventory, Product, ProductInventoryMapping
from src.core.database.repositories.gam_inventory import inventory_row
from src.services.gam_product_config_service import product_gam_slots


class _ProcessEngineSession(Session):
    """A session on the process's one engine (``get_engine``), looked up when it connects, not at import."""

    def get_bind(self, *args: Any, **kwargs: Any) -> Engine:
        return get_engine()


#: Thread-local sessions for the sync API, separate from ``get_db_session``'s, on the same
#: engine: a sync route holds this one open while the sync's units of work open and close
#: ``get_db_session``'s, and a unit's exit closes only the session it opened.
db_session: scoped_session[Session] = scoped_session(sessionmaker(class_=_ProcessEngineSession))

logger = logging.getLogger(__name__)


class GAMInventoryService:
    """Service for managing GAM inventory data."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def sync_tenant_inventory(self, tenant_id: str, gam_client) -> dict[str, Any]:
        """
        Sync all inventory for a tenant from GAM to database using streaming approach.

        This method processes inventory in chunks to minimize memory usage:
        - Fetches data from GAM API in paginated batches
        - Writes each batch to database immediately
        - Clears batch from memory before fetching next batch

        This prevents OOM errors with large inventories (10k+ items).

        Args:
            tenant_id: Tenant ID
            gam_client: Initialized GAM client

        Returns:
            Sync summary with counts and timing
        """
        logger.info(f"Starting streaming inventory sync for tenant {tenant_id}")

        # Create discovery instance for streaming sync
        from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery

        discovery = GAMInventoryDiscovery(gam_client, tenant_id)

        # Use streaming sync: each inventory type is synced separately and written to DB immediately
        sync_summary = self._streaming_sync_all_inventory(tenant_id, discovery)

        return sync_summary

    def _streaming_sync_all_inventory(self, tenant_id: str, discovery: "GAMInventoryDiscovery") -> dict[str, Any]:
        """
        Stream inventory sync: fetch and write each type separately to minimize memory.

        This method syncs inventory types one at a time:
        1. Fetch ad units from GAM → write to DB → clear from memory
        2. Fetch placements from GAM → write to DB → clear from memory
        3. Fetch labels from GAM → write to DB → clear from memory
        4. Fetch custom targeting keys (values lazy loaded) → write to DB → clear from memory
        5. Fetch audience segments → write to DB → clear from memory

        Memory usage stays bounded regardless of inventory size.

        Args:
            tenant_id: Tenant ID
            discovery: GAMInventoryDiscovery instance (empty at start)

        Returns:
            Sync summary with counts and timing
        """
        from datetime import datetime

        start_time = datetime.now(UTC)
        sync_time = datetime.now(UTC)

        logger.info(f"Starting streaming inventory sync for tenant {tenant_id}")

        # Track counts
        counts = {
            "ad_units": 0,
            "placements": 0,
            "labels": 0,
            "custom_targeting_keys": 0,
            "custom_targeting_values": 0,
            "audience_segments": 0,
        }

        # 1. Sync ad units (stream and write)
        logger.info("Streaming ad units...")
        ad_units = discovery.discover_ad_units()
        self._write_inventory_batch(tenant_id, "ad_unit", ad_units, sync_time)
        counts["ad_units"] = len(ad_units)
        discovery.ad_units.clear()  # Clear from memory immediately
        logger.info(f"Synced {counts['ad_units']} ad units")

        # 2. Sync placements (stream and write)
        logger.info("Streaming placements...")
        try:
            placements = discovery.discover_placements()
            self._write_inventory_batch(tenant_id, "placement", placements, sync_time)
            counts["placements"] = len(placements)
            discovery.placements.clear()  # Clear from memory
            logger.info(f"Synced {counts['placements']} placements")
        except Exception as e:
            logger.error(f"⏰ Placements sync timed out or failed: {e}. Continuing with other inventory types...")
            counts["placements"] = 0

        # 3. Sync labels (stream and write)
        logger.info("Streaming labels...")
        try:
            labels = discovery.discover_labels()
            self._write_inventory_batch(tenant_id, "label", labels, sync_time)
            counts["labels"] = len(labels)
            discovery.labels.clear()  # Clear from memory
            logger.info(f"Synced {counts['labels']} labels")
        except Exception as e:
            logger.error(f"⏰ Labels sync timed out or failed: {e}. Continuing with other inventory types...")
            counts["labels"] = 0

        # 4. Sync custom targeting KEYS ONLY (values lazy loaded on demand)
        logger.info("Streaming custom targeting keys (values lazy loaded)...")
        try:
            custom_targeting = discovery.discover_custom_targeting(
                max_values_per_key=None,
                fetch_values=False,  # Don't fetch values  # Lazy load values on demand
            )
            self._write_custom_targeting_keys(tenant_id, list(discovery.custom_targeting_keys.values()), sync_time)
            counts["custom_targeting_keys"] = len(discovery.custom_targeting_keys)
            counts["custom_targeting_values"] = custom_targeting.get("total_values", 0)
            discovery.custom_targeting_keys.clear()  # Clear from memory
            discovery.custom_targeting_values.clear()  # Clear from memory
            logger.info(f"Synced {counts['custom_targeting_keys']} custom targeting keys (values lazy loaded)")
        except Exception as e:
            logger.error(f"⏰ Custom targeting sync timed out or failed: {e}. Continuing with other inventory types...")
            counts["custom_targeting_keys"] = 0
            counts["custom_targeting_values"] = 0

        # 5. Sync audience segments (first-party only)
        logger.info("Streaming audience segments...")
        audience_segments = discovery.discover_audience_segments()
        self._write_inventory_batch(tenant_id, "audience_segment", audience_segments, sync_time)
        counts["audience_segments"] = len(audience_segments)
        discovery.audience_segments.clear()  # Clear from memory
        logger.info(f"Synced {counts['audience_segments']} audience segments")

        # Mark old items as stale
        self._mark_stale_inventory(tenant_id, sync_time)

        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        summary = {
            "tenant_id": tenant_id,
            "sync_time": sync_time.isoformat(),
            "duration_seconds": duration,
            "ad_units": {"total": counts["ad_units"]},
            "placements": {"total": counts["placements"]},
            "labels": {"total": counts["labels"]},
            "custom_targeting": {
                "total_keys": counts["custom_targeting_keys"],
                "total_values": counts["custom_targeting_values"],
                "note": "Values lazy loaded on demand",
            },
            "audience_segments": {"total": counts["audience_segments"]},
            "streaming": True,
            "memory_optimized": True,
        }

        logger.info(f"Streaming sync completed in {duration:.2f}s: {counts}")
        return summary

    def _write_inventory_batch(self, tenant_id: str, inventory_type: str, items: list, sync_time: datetime):
        """Write a batch of inventory items to database efficiently.

        Args:
            tenant_id: Tenant ID
            inventory_type: Type of inventory (ad_unit, placement, label, audience_segment)
            items: List of inventory items to write
            sync_time: Sync timestamp
        """
        if not items:
            return

        logger.info(f"📊 Writing {len(items)} {inventory_type} items to database...")

        BATCH_SIZE = 500

        # Load existing inventory IDs once
        logger.info(f"🔍 Loading existing {inventory_type} IDs from database...")
        stmt = select(GAMInventory.inventory_id, GAMInventory.id).where(
            and_(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == inventory_type)
        )
        existing = self.db.execute(stmt).all()
        existing_ids = {row.inventory_id: row.id for row in existing}
        logger.info(f"✅ Found {len(existing_ids)} existing {inventory_type} items")

        to_insert: list[dict[str, Any]] = []
        to_update: list[dict[str, Any]] = []
        batch_num = 0

        logger.info(f"🔄 Processing {len(items)} items (batch size: {BATCH_SIZE})...")
        for idx, item in enumerate(items):
            item_data = inventory_row(tenant_id, inventory_type, item, sync_time)

            if item.id in existing_ids:
                item_data["id"] = existing_ids[item.id]
                to_update.append(item_data)
            else:
                to_insert.append(item_data)

            # Flush batch
            if (len(to_insert) + len(to_update)) >= BATCH_SIZE:
                batch_num += 1
                logger.info(f"💾 Flushing batch {batch_num} (processed {idx + 1}/{len(items)} items)...")
                self._flush_batch(to_insert, to_update)
                to_insert.clear()
                to_update.clear()

        # Flush remaining
        if to_insert or to_update:
            batch_num += 1
            logger.info(f"💾 Flushing final batch {batch_num} ({len(to_insert)} new, {len(to_update)} updates)...")
            self._flush_batch(to_insert, to_update)

        logger.info(f"✅ Completed writing {len(items)} {inventory_type} items in {batch_num} batch(es)")

    def _write_custom_targeting_keys(self, tenant_id: str, keys: list, sync_time: datetime):
        """Write custom targeting keys to database (values are lazy loaded separately).

        Args:
            tenant_id: Tenant ID
            keys: List of CustomTargetingKey objects
            sync_time: Sync timestamp
        """
        if not keys:
            return

        BATCH_SIZE = 500

        # Load existing key IDs once
        # Use a fresh query to avoid stale connection issues
        stmt = select(GAMInventory.inventory_id, GAMInventory.id).where(
            and_(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "custom_targeting_key")
        )

        # Explicitly expire_all() and test connection health before querying
        # This prevents hanging on stale connections in long-running syncs
        self.db.expire_all()

        # Test connection is alive with a simple query (connection keep-alive)
        try:
            self.db.execute(text("SELECT 1")).scalar()
        except Exception as e:
            logger.warning(f"Connection test failed, will retry query: {e}")
            # Connection is stale - SQLAlchemy will automatically reconnect on next query

        existing = self.db.execute(stmt).all()
        existing_ids = {row.inventory_id: row.id for row in existing}

        to_insert: list[dict[str, Any]] = []
        to_update: list[dict[str, Any]] = []

        for key in keys:
            item_data = inventory_row(tenant_id, "custom_targeting_key", key, sync_time)

            if key.id in existing_ids:
                item_data["id"] = existing_ids[key.id]
                to_update.append(item_data)
            else:
                to_insert.append(item_data)

            # Flush batch
            if (len(to_insert) + len(to_update)) >= BATCH_SIZE:
                self._flush_batch(to_insert, to_update)
                to_insert.clear()
                to_update.clear()

        # Flush remaining
        self._flush_batch(to_insert, to_update)

    def _update_adapter_config_targeting_keys(self, tenant_id: str):
        """Update adapter_config.custom_targeting_keys from gam_inventory table.

        This ensures GAMTargetingManager.resolve_custom_targeting_key_id()
        can find the key name → GAM ID mapping needed for Media Buy approval.

        Args:
            tenant_id: Tenant ID to update
        """
        try:
            # Build mapping from gam_inventory table
            stmt = select(GAMInventory.name, GAMInventory.inventory_id).where(
                and_(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "custom_targeting_key",
                )
            )
            results = self.db.execute(stmt).all()
            key_mapping = {row.name: row.inventory_id for row in results}

            if key_mapping:
                # Update adapter_config via repository
                from src.core.database.repositories.adapter_config import AdapterConfigRepository

                adapter_repo = AdapterConfigRepository(self.db, tenant_id)
                adapter_repo.update_custom_targeting_keys(key_mapping)
                self.db.commit()
                logger.info(
                    f"Updated adapter_config.custom_targeting_keys with {len(key_mapping)} keys for tenant {tenant_id}"
                )
            else:
                logger.info(f"No custom targeting keys found in gam_inventory for tenant {tenant_id}")
        except Exception as e:
            logger.error(f"Failed to update adapter_config.custom_targeting_keys: {e}", exc_info=True)

    def _flush_batch(self, to_insert: list, to_update: list):
        """Flush a batch of inserts and updates to database with timeout and connection recovery.

        Args:
            to_insert: List of items to insert
            to_update: List of items to update
        """
        from sqlalchemy.exc import DBAPIError, OperationalError

        from src.adapters.gam.utils.timeout_handler import timeout
        from src.core.exceptions import AdCPServiceUnavailableError

        @timeout(seconds=120)  # 2 minute timeout for database operations
        def _commit_with_timeout():
            """Commit with timeout to prevent indefinite hangs."""
            self.db.commit()

        try:
            if to_insert:
                logger.info(f"📝 Starting bulk insert of {len(to_insert)} items...")
                # SQLAlchemy accepts model class but mypy expects Mapper type
                self.db.bulk_insert_mappings(GAMInventory, to_insert)  # type: ignore[arg-type]
                logger.info(f"✅ Batch inserted {len(to_insert)} items")
            if to_update:
                logger.info(f"📝 Starting bulk update of {len(to_update)} items...")
                # SQLAlchemy accepts model class but mypy expects Mapper type
                self.db.bulk_update_mappings(GAMInventory, to_update)  # type: ignore[arg-type]
                logger.info(f"✅ Batch updated {len(to_update)} items")
            logger.info("💾 Committing batch transaction (120s timeout)...")
            _commit_with_timeout()
            logger.info("✅ Batch committed successfully")
        except AdCPServiceUnavailableError as e:
            logger.error(f"⏰ Database commit timed out after 120s: {e}")
            logger.error(f"   Insert count: {len(to_insert)}, Update count: {len(to_update)}")
            logger.error("   This usually indicates: lost connection, lock contention, or large transaction")
            self.db.rollback()
            # A DB commit timeout is SERVICE_UNAVAILABLE, per AdCP.1
            # transport-errors.mdx Rule 1, which names this exact translation.
            raise AdCPServiceUnavailableError(internal_detail=e) from e
        except (OperationalError, DBAPIError) as e:
            # Connection errors - log and re-raise with context
            logger.error(f"❌ Database connection error during batch write: {e}")
            logger.error(f"   Insert count: {len(to_insert)}, Update count: {len(to_update)}")
            logger.error("   Connection may have been lost during long-running sync")
            self.db.rollback()
            # Re-raise the original error with additional context
            raise OperationalError(
                "Database connection lost during batch write. This can happen in long-running syncs if the connection times out.",
                params=None,
                orig=e.orig if hasattr(e, "orig") and e.orig is not None else Exception("Unknown error"),
            )
        except Exception as e:
            logger.error(f"❌ Batch write failed: {e}", exc_info=True)
            logger.error(f"   Insert count: {len(to_insert)}, Update count: {len(to_update)}")
            self.db.rollback()
            raise

    def _mark_stale_inventory(self, tenant_id: str, sync_time: datetime):
        """Mark inventory items not updated in this sync as stale.

        Args:
            tenant_id: Tenant ID
            sync_time: Current sync timestamp
        """
        from sqlalchemy import update

        stale_cutoff = sync_time - timedelta(seconds=1)

        # Don't mark ad units as STALE - they should remain ACTIVE
        stmt = (
            update(GAMInventory)
            .where(
                and_(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.last_synced < stale_cutoff,
                    GAMInventory.inventory_type != "ad_unit",  # Keep ad units active
                )
            )
            .values(status="STALE")
        )
        self.db.execute(stmt)
        self.db.commit()
        logger.info("Marked stale inventory items")

    def _upsert_inventory_item(
        self,
        tenant_id: str,
        inventory_type: str,
        inventory_id: str,
        name: str,
        path: list[str],
        status: str,
        inventory_metadata: dict,
        last_synced: datetime,
    ):
        """Insert or update a single inventory item in database.

        Used for lazy loading individual items (e.g., custom targeting values).

        Args:
            tenant_id: Tenant ID
            inventory_type: Type of inventory
            inventory_id: GAM inventory ID
            name: Item name
            path: Item path
            status: Item status
            inventory_metadata: Item metadata
            last_synced: Sync timestamp
        """
        # Check if item exists
        stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == inventory_type,
                GAMInventory.inventory_id == inventory_id,
            )
        )
        item = GAMInventory(
            tenant_id=tenant_id,
            inventory_type=inventory_type,
            inventory_id=inventory_id,
            name=name,
            path=path,
            status=status,
            inventory_metadata=inventory_metadata,
            last_synced=last_synced,
        )
        existing = resolve_or_write(
            self.db,
            conflict=lambda: self.db.scalars(stmt).first(),
            write=lambda: self.db.add(item),
            constraint="uq_gam_inventory",
        )

        if existing is not None:
            # Update existing — also where a sync that lost the insert race lands.
            existing.name = name
            existing.path = path
            existing.status = status
            existing.inventory_metadata = inventory_metadata
            # Properly assign datetime to DateTime column
            existing.last_synced = last_synced

        self.db.commit()

    def get_ad_unit_tree(self, tenant_id: str, limit: int = 1000) -> dict[str, Any]:
        """
        Get hierarchical ad unit tree from database.

        For large inventories (10k+ ad units), this returns a limited set
        to prevent browser timeouts. Use the search endpoint for finding
        specific ad units.

        Args:
            tenant_id: Tenant ID
            limit: Maximum number of ad units to return (default 1000)

        Returns:
            Hierarchical tree structure with limited ad units
        """
        # Get ad units with limit to prevent timeouts on large inventories
        stmt = (
            select(GAMInventory)
            .where(
                and_(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "ad_unit",
                    GAMInventory.status != "STALE",
                )
            )
            .order_by(GAMInventory.name)
            .limit(limit)
        )
        ad_units = self.db.scalars(stmt).all()

        # Build lookup maps
        unit_map = {}
        root_units = []

        for unit in ad_units:
            unit_data = {
                "id": unit.inventory_id,
                "name": unit.name,
                "path": unit.path,
                "status": unit.status,
                "metadata": unit.inventory_metadata,
                "children": [],
            }
            unit_map[unit.inventory_id] = unit_data

            # Check if root (no parent or parent not in path)
            parent_id = unit.inventory_metadata.get("parent_id") if unit.inventory_metadata else None
            if not parent_id:
                root_units.append(unit_data)

        # Build hierarchy
        for unit in ad_units:
            parent_id = unit.inventory_metadata.get("parent_id") if unit.inventory_metadata else None
            if parent_id and parent_id in unit_map:
                children_list = unit_map[parent_id]["children"]
                if isinstance(children_list, list):
                    children_list.append(unit_map[unit.inventory_id])

        # Get last sync info from gam_inventory table
        last_sync_stmt = select(func.max(GAMInventory.last_synced)).where(GAMInventory.tenant_id == tenant_id)
        last_sync_result = self.db.scalar(last_sync_stmt)
        # last_sync_result is a datetime object from func.max(), not DateTime column
        last_sync: str | None = None
        if last_sync_result is not None:
            from datetime import datetime

            if isinstance(last_sync_result, datetime):
                last_sync = last_sync_result.isoformat()

        # Get counts for other inventory types
        placements_count = (
            self.db.scalar(
                select(func.count()).where(
                    and_(
                        GAMInventory.tenant_id == tenant_id,
                        GAMInventory.inventory_type == "placement",
                        GAMInventory.status != "STALE",
                    )
                )
            )
            or 0
        )

        labels_count = (
            self.db.scalar(
                select(func.count()).where(
                    and_(
                        GAMInventory.tenant_id == tenant_id,
                        GAMInventory.inventory_type == "label",
                        GAMInventory.status != "STALE",
                    )
                )
            )
            or 0
        )

        custom_targeting_keys_count = (
            self.db.scalar(
                select(func.count()).where(
                    and_(
                        GAMInventory.tenant_id == tenant_id,
                        GAMInventory.inventory_type == "custom_targeting_key",
                        GAMInventory.status != "STALE",
                    )
                )
            )
            or 0
        )

        audience_segments_count = (
            self.db.scalar(
                select(func.count()).where(
                    and_(
                        GAMInventory.tenant_id == tenant_id,
                        GAMInventory.inventory_type == "audience_segment",
                        GAMInventory.status != "STALE",
                    )
                )
            )
            or 0
        )

        return {
            "root_units": root_units,
            "total_units": len(ad_units),
            "placements": placements_count,
            "labels": labels_count,
            "custom_targeting_keys": custom_targeting_keys_count,
            "audience_segments": audience_segments_count,
            "last_sync": last_sync,
            "needs_refresh": self._needs_refresh(last_sync),
        }

    def _needs_refresh(self, last_sync_str: str | None) -> bool:
        """Check if inventory needs refresh (older than 24 hours)."""
        if not last_sync_str:
            return True

        try:
            last_sync = datetime.fromisoformat(last_sync_str)
            return datetime.now(UTC) - last_sync > timedelta(hours=24)
        except Exception:
            return True

    def search_inventory(
        self,
        tenant_id: str,
        query: str | None = None,
        inventory_type: str | None = None,
        status: str | None = None,
        sizes: list[dict[str, int]] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Search inventory with filters.

        For large inventories, results are limited to prevent timeouts.
        Use more specific search terms to narrow results.

        Args:
            tenant_id: Tenant ID
            query: Text search in name/path
            inventory_type: Filter by type (ad_unit, placement, label)
            status: Filter by status
            sizes: Filter ad units by size support
            limit: Maximum results to return (default 500)

        Returns:
            List of matching inventory items (up to limit)
        """
        filters = [GAMInventory.tenant_id == tenant_id, GAMInventory.status != "STALE"]

        if inventory_type:
            filters.append(GAMInventory.inventory_type == inventory_type)

        if status:
            filters.append(GAMInventory.status == status)

        if query:
            # Search in name and path
            filters.append(
                or_(GAMInventory.name.ilike(f"%{query}%"), func.cast(GAMInventory.path, String).ilike(f"%{query}%"))
            )

        stmt = select(GAMInventory).where(and_(*filters)).order_by(GAMInventory.name).limit(limit)
        results = self.db.scalars(stmt).all()

        # Filter by sizes if specified
        if sizes and inventory_type in (None, "ad_unit"):
            filtered_results = []
            for result in results:
                if result.inventory_type != "ad_unit":
                    if inventory_type is None:
                        continue
                    filtered_results.append(result)
                    continue

                unit_sizes = result.inventory_metadata.get("sizes", []) if result.inventory_metadata else []
                has_matching_size = False

                for required_size in sizes:
                    for unit_size in unit_sizes:
                        if (
                            unit_size["width"] == required_size["width"]
                            and unit_size["height"] == required_size["height"]
                        ):
                            has_matching_size = True
                            break
                    if has_matching_size:
                        break

                if has_matching_size:
                    filtered_results.append(result)

            results = filtered_results

        # Convert to dict format
        return [
            {
                "id": item.inventory_id,
                "type": item.inventory_type,
                "name": item.name,
                "path": item.path,
                "status": item.status,
                "metadata": item.inventory_metadata,
                # item.last_synced is a datetime object from the database, not DateTime column
                "last_synced": (item.last_synced.isoformat() if isinstance(item.last_synced, datetime) else None),
            }
            for item in results
        ]

    def get_product_inventory(self, tenant_id: str, product_id: str) -> dict[str, Any] | None:
        """
        Get inventory mappings for a product.

        Args:
            tenant_id: Tenant ID
            product_id: Product ID

        Returns:
            Product inventory configuration or None if product not found
        """
        # Get product
        product_stmt = select(Product).where(and_(Product.tenant_id == tenant_id, Product.product_id == product_id))
        product = self.db.scalars(product_stmt).first()

        if not product:
            return None

        # Get mappings
        mappings_stmt = select(ProductInventoryMapping).where(
            and_(ProductInventoryMapping.tenant_id == tenant_id, ProductInventoryMapping.product_id == product_id)
        )
        mappings = self.db.scalars(mappings_stmt).all()

        # Get inventory details
        ad_units = []
        placements = []

        for mapping in mappings:
            inventory_stmt = select(GAMInventory).where(
                and_(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == mapping.inventory_type,
                    GAMInventory.inventory_id == mapping.inventory_id,
                )
            )
            inventory = self.db.scalars(inventory_stmt).first()

            if inventory:
                item = {
                    "id": inventory.inventory_id,
                    "name": inventory.name,
                    "path": inventory.path,
                    "is_primary": mapping.is_primary,
                    "metadata": inventory.inventory_metadata,
                }

                if mapping.inventory_type == "ad_unit":
                    ad_units.append(item)
                elif mapping.inventory_type == "placement":
                    placements.append(item)

        return {
            "product_id": product_id,
            "product_name": product.name,
            "ad_units": ad_units,
            "placements": placements,
            "total_mappings": len(mappings),
        }

    def update_product_inventory(
        self,
        tenant_id: str,
        product_id: str,
        ad_unit_ids: list[str],
        placement_ids: list[str] | None = None,
        primary_ad_unit_id: str | None = None,
    ) -> bool:
        """
        Update product inventory mappings.

        Args:
            tenant_id: Tenant ID
            product_id: Product ID
            ad_unit_ids: List of ad unit IDs to map
            placement_ids: Optional list of placement IDs
            primary_ad_unit_id: Optional primary ad unit

        Returns:
            Success boolean
        """
        try:
            # Verify product exists
            product_stmt = select(Product).where(and_(Product.tenant_id == tenant_id, Product.product_id == product_id))
            product = self.db.scalars(product_stmt).first()

            if not product:
                return False

            # Delete existing mappings
            delete_stmt = delete(ProductInventoryMapping).where(
                and_(ProductInventoryMapping.tenant_id == tenant_id, ProductInventoryMapping.product_id == product_id)
            )
            self.db.execute(delete_stmt)

            # Add new ad unit mappings
            for ad_unit_id in ad_unit_ids:
                mapping = ProductInventoryMapping(
                    tenant_id=tenant_id,
                    product_id=product_id,
                    inventory_type="ad_unit",
                    inventory_id=ad_unit_id,
                    is_primary=(ad_unit_id == primary_ad_unit_id),
                )
                self.db.add(mapping)

            # Add placement mappings if provided
            if placement_ids:
                for placement_id in placement_ids:
                    mapping = ProductInventoryMapping(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        inventory_type="placement",
                        inventory_id=placement_id,
                        is_primary=False,
                    )
                    self.db.add(mapping)

            # Update product implementation config
            if not product.implementation_config:
                product.implementation_config = {}

            product.implementation_config["targeted_ad_unit_ids"] = ad_unit_ids
            if placement_ids:
                product.implementation_config["targeted_placement_ids"] = placement_ids

            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to update product inventory: {e}")
            self.db.rollback()
            return False

    def suggest_inventory_for_product(self, tenant_id: str, product_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Suggest inventory based on product configuration.

        Args:
            tenant_id: Tenant ID
            product_id: Product ID
            limit: Maximum suggestions to return

        Returns:
            List of suggested inventory items with scores
        """
        # Get product
        product_stmt = select(Product).where(and_(Product.tenant_id == tenant_id, Product.product_id == product_id))
        product = self.db.scalars(product_stmt).first()

        if not product:
            return []

        # The slots this product's format declarations state, asked of the ONE reader that
        # answers that question for GAM -- the same one that seeds a product's
        # implementation_config and that the order manager reads a package's snapshot with.
        # The size match below scores an ad unit against them.
        #
        # Read off ``Product.format_contract``, not the product's own columns: a product
        # linked to an inventory profile is served and graded against the PROFILE's
        # declarations, so sizing it from its own arms scored ad units against a contract no
        # buyer was ever offered.
        #
        # THE LOOP THIS REPLACES WAS DEAD, not merely legacy-only. It tested
        # ``isinstance(format_id, str)`` while ``models.py:402`` types the column
        # ``Mapped[list[dict[str, str]] | None]`` and every writer stores FormatId DOCUMENTS,
        # so the body never ran and ``creative_sizes`` was ALWAYS empty -- every ad unit was
        # scored with no size signal at all. That is the same defect shape as the GAM seeder's
        # deleted dimension map, which read a ``format_id`` key the stored entries do not
        # carry (salesagent-ne2t3, salesagent-ki6a4).
        contract = product.format_contract
        creative_sizes: list[dict[str, int]] = [
            {"width": slot["width"], "height": slot["height"]}
            for slot in product_gam_slots(contract.format_ids, contract.format_options)
        ]

        # Get keywords from product name and description
        keywords: list[str] = []
        if product.name:
            keywords.extend(product.name.lower().split())
        if product.description:
            keywords.extend(product.description.lower().split()[:5])  # First 5 words

        # Search for matching ad units
        suggestions: list[dict[str, Any]] = []

        # Get active ad units
        ad_units_stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "ad_unit",
                GAMInventory.status == "ACTIVE",
            )
        )
        ad_units = self.db.scalars(ad_units_stmt).all()

        for unit in ad_units:
            score = 0
            reasons: list[str] = []

            # Check size match
            unit_metadata = unit.inventory_metadata if unit.inventory_metadata else {}
            unit_sizes = unit_metadata.get("sizes", [])
            for creative_size in creative_sizes:
                for unit_size in unit_sizes:
                    if unit_size["width"] == creative_size["width"] and unit_size["height"] == creative_size["height"]:
                        score += 10
                        reasons.append(f"Size match: {unit_size['width']}x{unit_size['height']}")

            # Check keyword match
            unit_path_strs = [str(p).lower() for p in unit.path] if unit.path else []
            unit_text = " ".join([unit.name.lower()] + unit_path_strs)
            for keyword in keywords:
                if keyword in unit_text:
                    score += 5
                    reasons.append(f"Keyword match: {keyword}")

            # Prefer explicitly targeted units
            if unit_metadata.get("explicitly_targeted"):
                score += 3
                reasons.append("Explicitly targeted")

            # Prefer specific placements
            if unit.path and len(unit.path) > 2:
                score += 2
                reasons.append("Specific placement")

            if score > 0:
                path_str = " > ".join([str(p) for p in unit.path]) if unit.path else ""
                suggestions.append(
                    {
                        "inventory": {
                            "id": unit.inventory_id,
                            "name": unit.name,
                            "path": path_str,
                            "sizes": unit_sizes,
                        },
                        "score": score,
                        "reasons": reasons,
                    }
                )

        # Sort by score and limit
        suggestions.sort(key=lambda x: int(x["score"]), reverse=True)
        return suggestions[:limit]

    def get_all_targeting_data(self, tenant_id: str) -> dict[str, Any]:
        """
        Get all targeting data for browsing.

        Args:
            tenant_id: Tenant ID

        Returns:
            Dictionary with all targeting data organized by type
        """
        # Get custom targeting keys
        stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "custom_targeting_key",
                GAMInventory.status != "STALE",
            )
        )
        custom_keys = self.db.scalars(stmt).all()

        # Get custom targeting values grouped by key
        custom_values: dict[str, list[dict[str, str]]] = {}
        stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "custom_targeting_value",
                GAMInventory.status != "STALE",
            )
        )
        all_values = self.db.scalars(stmt).all()

        for value in all_values:
            if not value.inventory_metadata:
                continue
            key_id = value.inventory_metadata.get("custom_targeting_key_id")
            if key_id:
                if key_id not in custom_values:
                    custom_values[key_id] = []
                custom_values[key_id].append(
                    {
                        "id": value.inventory_id,
                        "name": value.name,
                        "display_name": value.inventory_metadata.get("display_name", value.name),
                        "match_type": value.inventory_metadata.get("match_type", "EXACT"),
                        "status": value.status,
                    }
                )

        # Get audience segments
        stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "audience_segment",
                GAMInventory.status != "STALE",
            )
        )
        audiences = self.db.scalars(stmt).all()

        # Get labels
        stmt = select(GAMInventory).where(
            and_(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "label",
                GAMInventory.status != "STALE",
            )
        )
        labels = self.db.scalars(stmt).all()

        # Get last sync info from gam_inventory table
        last_sync_stmt_2 = select(func.max(GAMInventory.last_synced)).where(GAMInventory.tenant_id == tenant_id)
        last_sync_result_2 = self.db.scalar(last_sync_stmt_2)
        last_sync_2: str | None = None
        if last_sync_result_2 is not None:
            from datetime import datetime

            if isinstance(last_sync_result_2, datetime):
                last_sync_2 = last_sync_result_2.isoformat()
        last_sync = last_sync_2

        # Format response
        return {
            "customKeys": [
                {
                    "id": key.inventory_id,
                    "name": key.name,
                    "display_name": (
                        key.inventory_metadata.get("display_name", key.name) if key.inventory_metadata else key.name
                    ),
                    "type": key.inventory_metadata.get("type", "UNKNOWN") if key.inventory_metadata else "UNKNOWN",
                    "status": key.status,
                    "metadata": {
                        "reportable_type": (
                            key.inventory_metadata.get("reportable_type") if key.inventory_metadata else None
                        ),
                        "values_count": len(custom_values.get(key.inventory_id, [])),
                        "values_loaded": key.inventory_id in custom_values and len(custom_values[key.inventory_id]) > 0,
                    },
                }
                for key in custom_keys
            ],
            "customValues": custom_values,
            "audiences": [
                {
                    "id": seg.inventory_id,
                    "name": seg.name,
                    "description": seg.inventory_metadata.get("description") if seg.inventory_metadata else None,
                    "type": seg.inventory_metadata.get("type", "UNKNOWN") if seg.inventory_metadata else "UNKNOWN",
                    "size": seg.inventory_metadata.get("size") if seg.inventory_metadata else None,
                    "data_provider_name": (
                        seg.inventory_metadata.get("data_provider_name") if seg.inventory_metadata else None
                    ),
                    "segment_type": (
                        seg.inventory_metadata.get("segment_type", "UNKNOWN") if seg.inventory_metadata else "UNKNOWN"
                    ),
                    "status": seg.status,
                    "category_ids": seg.inventory_metadata.get("category_ids", []) if seg.inventory_metadata else [],
                }
                for seg in audiences
            ],
            "labels": [
                {
                    "id": label.inventory_id,
                    "name": label.name,
                    "description": label.inventory_metadata.get("description") if label.inventory_metadata else None,
                    "is_active": label.status == "ACTIVE",
                    "ad_category": label.inventory_metadata.get("ad_category") if label.inventory_metadata else None,
                    "label_type": (
                        label.inventory_metadata.get("label_type", "UNKNOWN") if label.inventory_metadata else "UNKNOWN"
                    ),
                }
                for label in labels
            ],
            "last_sync": last_sync,
        }

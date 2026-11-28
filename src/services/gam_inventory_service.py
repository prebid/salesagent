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
from src.core.database.repositories.gam_inventory import GAMInventoryRepository, InventoryType
from src.services.gam_product_config_service import product_gam_slots


class _ProcessEngineSession(Session):
    """A session on the process's one engine (``get_engine``), looked up when it connects, not at import."""

    def get_bind(self, *args: Any, **kwargs: Any) -> Engine:
        return get_engine()


#: Thread-local sessions for the sync API, separate from ``get_db_session``'s, on the same
#: engine: a sync's failure is recorded in a unit of work of its own while this session
#: still holds the failed work.
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

    def _save_inventory_to_db(self, tenant_id: str, discovery: GAMInventoryDiscovery):
        """Save discovered inventory to database using batched operations to prevent memory issues.

        Writes to database incrementally in batches rather than loading everything into memory.
        This prevents OOM errors with large inventories (10k+ items).
        """
        sync_time = datetime.now(UTC)
        BATCH_SIZE = 500  # Write every 500 items to keep memory usage low

        inventory = GAMInventoryRepository(self.db, tenant_id)

        # Track totals for logging
        total_written = 0

        # The current batch: the repository inserts a new item and updates a held one in place
        batch: list[dict[str, Any]] = []

        def flush_batch():
            """Flush current batch to database with error handling."""
            nonlocal total_written
            try:
                if batch:
                    inventory.upsert(batch)
                    total_written += len(batch)
                    logger.info("Batch wrote %s inventory items", len(batch))
                    batch.clear()
                self.db.commit()  # Commit each batch
            except Exception as e:
                logger.error("Batch write failed at %s items: %s", total_written, e, exc_info=True)
                self.db.rollback()
                raise  # Re-raise to fail the sync properly

        # Process ad units
        for ad_unit in discovery.ad_units.values():
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "ad_unit",
                "inventory_id": ad_unit.id,
                "name": ad_unit.name,
                "path": ad_unit.path,
                "status": ad_unit.status.value,
                "inventory_metadata": {
                    "ad_unit_code": ad_unit.ad_unit_code,
                    "parent_id": ad_unit.parent_id,
                    "description": ad_unit.description,
                    "target_window": ad_unit.target_window,
                    "explicitly_targeted": ad_unit.explicitly_targeted,
                    "has_children": ad_unit.has_children,
                    "sizes": ad_unit.sizes,
                    "effective_applied_labels": ad_unit.effective_applied_labels,
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Flush batch every BATCH_SIZE items
            if len(batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining ad units
        flush_batch()
        logger.info(f"Completed saving ad units: {len(discovery.ad_units)} total")

        # Process placements
        placements_count = len(discovery.placements)
        logger.info(f"Processing {placements_count} placements for database save")

        if placements_count == 0:
            logger.warning(
                f"No placements found in discovery object for tenant {tenant_id}. "
                f"This could indicate: (1) no placements exist in GAM, (2) placement discovery failed, "
                f"or (3) all placements are ARCHIVED"
            )

        for placement in discovery.placements.values():
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "placement",
                "inventory_id": placement.id,
                "name": placement.name,
                "path": [placement.name],  # Placements don't have hierarchy
                "status": placement.status,
                "inventory_metadata": {
                    "placement_code": placement.placement_code,
                    "description": placement.description,
                    "is_ad_sense_targeting_enabled": placement.is_ad_sense_targeting_enabled,
                    "ad_unit_ids": placement.ad_unit_ids,
                    "targeting_description": placement.targeting_description,
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Flush batch every BATCH_SIZE items
            if len(batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining placements
        flush_batch()
        logger.info(f"Completed saving placements: {placements_count} total processed")

        # Process labels
        for label in discovery.labels.values():
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "label",
                "inventory_id": label.id,
                "name": label.name,
                "path": [label.name],
                "status": "ACTIVE" if label.is_active else "INACTIVE",
                "inventory_metadata": {
                    "description": label.description,
                    "ad_category": label.ad_category,
                    "label_type": label.label_type,
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Flush batch every BATCH_SIZE items
            if len(batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining labels
        flush_batch()
        logger.info("Completed saving labels")

        # Process custom targeting keys
        for targeting_key in discovery.custom_targeting_keys.values():
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "custom_targeting_key",
                "inventory_id": targeting_key.id,
                "name": targeting_key.name,
                "path": [targeting_key.display_name],
                "status": targeting_key.status,
                "inventory_metadata": {
                    "display_name": targeting_key.display_name,
                    "type": targeting_key.type,  # PREDEFINED or FREEFORM
                    "reportable_type": targeting_key.reportable_type,
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Process values for this key
            values = discovery.custom_targeting_values.get(targeting_key.id, [])
            for value in values:
                value_data = {
                    "tenant_id": tenant_id,
                    "inventory_type": "custom_targeting_value",
                    "inventory_id": value.id,
                    "name": value.name,
                    "path": [targeting_key.display_name, value.display_name],
                    "status": value.status,
                    "inventory_metadata": {
                        "custom_targeting_key_id": value.custom_targeting_key_id,
                        "display_name": value.display_name,
                        "match_type": value.match_type,
                        "key_name": targeting_key.name,
                        "key_display_name": targeting_key.display_name,
                    },
                    "last_synced": sync_time,
                }

                batch.append(value_data)

                # Flush batch every BATCH_SIZE items (values can be huge)
                if len(batch) >= BATCH_SIZE:
                    flush_batch()

        # Flush remaining targeting keys/values
        flush_batch()
        logger.info("Completed saving targeting keys and values")

        # Process audience segments
        for segment in discovery.audience_segments.values():
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "audience_segment",
                "inventory_id": segment.id,
                "name": segment.name,
                "path": [segment.type, segment.name],  # e.g. ["FIRST_PARTY", "Sports Enthusiasts"]
                "status": segment.status,
                "inventory_metadata": {
                    "description": segment.description,
                    "category_ids": segment.category_ids,
                    "type": segment.type,  # FIRST_PARTY or THIRD_PARTY
                    "size": segment.size,
                    "data_provider_name": segment.data_provider_name,
                    "segment_type": segment.segment_type,  # RULE_BASED, SHARED, etc.
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Flush batch every BATCH_SIZE items
            if len(batch) >= BATCH_SIZE:
                flush_batch()

        # Flush remaining audience segments
        flush_batch()
        logger.info("Completed saving audience segments")

        # Mark old items as potentially stale (but keep ad units active)
        stale_cutoff = sync_time - timedelta(seconds=1)

        # Don't mark ad units as STALE - they should remain ACTIVE
        from sqlalchemy import update

        update_stmt = (
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
        self.db.execute(update_stmt)
        self.db.commit()

        logger.info("Saved inventory to database: %s items written (batched operations - prevents OOM)", total_written)

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

    def _write_inventory_batch(self, tenant_id: str, inventory_type: InventoryType, items: list, sync_time: datetime):
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

        batch: list[dict[str, Any]] = []
        batch_num = 0

        logger.info(f"🔄 Processing {len(items)} items (batch size: {BATCH_SIZE})...")
        for idx, item in enumerate(items):
            batch.append(self._convert_item_to_db_format(tenant_id, inventory_type, item, sync_time))

            # Flush batch
            if len(batch) >= BATCH_SIZE:
                batch_num += 1
                logger.info(f"💾 Flushing batch {batch_num} (processed {idx + 1}/{len(items)} items)...")
                self._flush_batch(tenant_id, batch)
                batch.clear()

        # Flush remaining
        if batch:
            batch_num += 1
            logger.info("💾 Flushing final batch %s (%s items)...", batch_num, len(batch))
            self._flush_batch(tenant_id, batch)

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

        # Explicitly expire_all() and test connection health before querying
        # This prevents hanging on stale connections in long-running syncs
        self.db.expire_all()

        # Test connection is alive with a simple query (connection keep-alive)
        try:
            self.db.execute(text("SELECT 1")).scalar()
        except Exception as e:
            logger.warning(f"Connection test failed, will retry query: {e}")
            # Connection is stale - SQLAlchemy will automatically reconnect on next query

        batch: list[dict[str, Any]] = []

        for key in keys:
            item_data = {
                "tenant_id": tenant_id,
                "inventory_type": "custom_targeting_key",
                "inventory_id": key.id,
                "name": key.name,
                "path": [key.display_name],
                "status": key.status,
                "inventory_metadata": {
                    "display_name": key.display_name,
                    "type": key.type,
                    "reportable_type": key.reportable_type,
                },
                "last_synced": sync_time,
            }

            batch.append(item_data)

            # Flush batch
            if len(batch) >= BATCH_SIZE:
                self._flush_batch(tenant_id, batch)
                batch.clear()

        # Flush remaining
        self._flush_batch(tenant_id, batch)

    def _update_adapter_config_targeting_keys(self, tenant_id: str):
        """Update adapter_config.custom_targeting_keys from gam_inventory table.

        This ensures GAMTargetingManager.resolve_custom_targeting_key_id()
        can find the key name → GAM ID mapping needed for Media Buy approval.

        Args:
            tenant_id: Tenant ID to update
        """
        try:
            # Build mapping from gam_inventory table
            key_mapping = GAMInventoryRepository(self.db, tenant_id).inventory_ids_by_name("custom_targeting_key")

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

    def _convert_item_to_db_format(self, tenant_id: str, inventory_type: str, item, sync_time: datetime) -> dict:
        """Convert inventory item to database format.

        Args:
            tenant_id: Tenant ID
            inventory_type: Type of inventory
            item: Inventory item object
            sync_time: Sync timestamp

        Returns:
            Dictionary ready for database insert/update
        """

        if inventory_type == "ad_unit":
            return {
                "tenant_id": tenant_id,
                "inventory_type": "ad_unit",
                "inventory_id": item.id,
                "name": item.name,
                "path": item.path,
                "status": item.status.value,
                "inventory_metadata": {
                    "ad_unit_code": item.ad_unit_code,
                    "parent_id": item.parent_id,
                    "description": item.description,
                    "target_window": item.target_window,
                    "explicitly_targeted": item.explicitly_targeted,
                    "has_children": item.has_children,
                    "sizes": item.sizes,
                    "effective_applied_labels": item.effective_applied_labels,
                },
                "last_synced": sync_time,
            }
        elif inventory_type == "placement":
            return {
                "tenant_id": tenant_id,
                "inventory_type": "placement",
                "inventory_id": item.id,
                "name": item.name,
                "path": [item.name],
                "status": item.status,
                "inventory_metadata": {
                    "placement_code": item.placement_code,
                    "description": item.description,
                    "is_ad_sense_targeting_enabled": item.is_ad_sense_targeting_enabled,
                    "ad_unit_ids": item.ad_unit_ids,
                    "targeting_description": item.targeting_description,
                },
                "last_synced": sync_time,
            }
        elif inventory_type == "label":
            return {
                "tenant_id": tenant_id,
                "inventory_type": "label",
                "inventory_id": item.id,
                "name": item.name,
                "path": [item.name],
                "status": "ACTIVE" if item.is_active else "INACTIVE",
                "inventory_metadata": {
                    "description": item.description,
                    "ad_category": item.ad_category,
                    "label_type": item.label_type,
                },
                "last_synced": sync_time,
            }
        elif inventory_type == "audience_segment":
            return {
                "tenant_id": tenant_id,
                "inventory_type": "audience_segment",
                "inventory_id": item.id,
                "name": item.name,
                "path": [item.type, item.name],
                "status": item.status,
                "inventory_metadata": {
                    "description": item.description,
                    "category_ids": item.category_ids,
                    "type": item.type,
                    "size": item.size,
                    "data_provider_name": item.data_provider_name,
                    "segment_type": item.segment_type,
                },
                "last_synced": sync_time,
            }
        else:
            raise ValueError(f"Unknown inventory type: {inventory_type}")

    def _flush_batch(self, tenant_id: str, rows: list[dict[str, Any]]):
        """Write a batch of rows and commit it, with timeout and connection recovery.

        Args:
            tenant_id: Tenant ID
            rows: The batch; each row inserts a new item or updates a held one in place
        """
        from sqlalchemy.exc import DBAPIError, OperationalError

        from src.adapters.gam.utils.timeout_handler import timeout
        from src.core.exceptions import AdCPServiceUnavailableError

        @timeout(seconds=120)  # 2 minute timeout for database operations
        def _commit_with_timeout():
            """Commit with timeout to prevent indefinite hangs."""
            self.db.commit()

        try:
            if rows:
                logger.info("📝 Starting upsert of %s items...", len(rows))
                GAMInventoryRepository(self.db, tenant_id).upsert(rows)
                logger.info("✅ Batch wrote %s items", len(rows))
            logger.info("💾 Committing batch transaction (120s timeout)...")
            _commit_with_timeout()
            logger.info("✅ Batch committed successfully")
        except AdCPServiceUnavailableError as e:
            logger.error(f"⏰ Database commit timed out after 120s: {e}")
            logger.error("   Item count: %s", len(rows))
            logger.error("   This usually indicates: lost connection, lock contention, or large transaction")
            self.db.rollback()
            # A DB commit timeout is SERVICE_UNAVAILABLE, per AdCP.1
            # transport-errors.mdx Rule 1, which names this exact translation.
            raise AdCPServiceUnavailableError(internal_detail=e) from e
        except (OperationalError, DBAPIError) as e:
            # Connection errors - log and re-raise with context
            logger.error(f"❌ Database connection error during batch write: {e}")
            logger.error("   Item count: %s", len(rows))
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
            logger.error("   Item count: %s", len(rows))
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

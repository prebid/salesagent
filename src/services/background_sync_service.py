"""
Background sync service for long-running inventory syncs.

This service runs syncs in background threads to prevent blocking the web server
and losing progress on container restarts.
"""

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob

logger = logging.getLogger(__name__)

# Global registry of running sync threads
_active_syncs: dict[str, threading.Thread] = {}
_sync_lock = threading.Lock()


def start_inventory_sync_background(
    tenant_id: str,
    sync_mode: str = "incremental",
    sync_types: list[str] | None = None,
    custom_targeting_limit: int | None = None,
    audience_segment_limit: int | None = None,
) -> str:
    """
    Start an inventory sync in the background.

    Args:
        tenant_id: Tenant ID to sync
        sync_mode: "full" (delete all and resync) or "incremental" (only sync changed items since last successful sync)
        sync_types: Optional list of inventory types to sync
        custom_targeting_limit: Optional limit on custom targeting values
        audience_segment_limit: Optional limit on audience segments

    Returns:
        sync_id: The sync job ID for tracking progress

    Raises:
        ValueError: If a sync is already running for this tenant
    """

    # Create sync job record
    with get_db_session() as db:
        # Get adapter type for the tenant
        from src.core.database.models import AdapterConfig

        adapter_config_stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter_config = db.scalars(adapter_config_stmt).first()
        adapter_type = adapter_config.adapter_type if adapter_config else "mock"

        # Check if sync already running
        stmt = select(SyncJob).where(
            SyncJob.tenant_id == tenant_id, SyncJob.status == "running", SyncJob.sync_type == "inventory"
        )
        existing_sync = db.scalars(stmt).first()

        if existing_sync:
            # Check if sync is stale (running for >1 hour with no progress updates)
            from datetime import timedelta

            # Get started_at as datetime (SQLAlchemy returns datetime for DateTime columns)
            started_at_value: datetime = existing_sync.started_at

            # Make started_at timezone-aware if it's naive (from database)
            if started_at_value.tzinfo is None:
                started_at_value = started_at_value.replace(tzinfo=UTC)

            time_running = datetime.now(UTC) - started_at_value
            is_stale = time_running > timedelta(hours=1) and not existing_sync.progress

            if is_stale:
                # Mark stale sync as failed and allow new sync to start
                existing_sync.status = "failed"
                # SQLAlchemy DateTime column accepts datetime objects
                existing_sync.completed_at = datetime.now(UTC)
                existing_sync.error_message = (
                    "Sync thread died (stale after 1+ hour with no progress) - marked as failed to allow fresh sync"
                )
                db.commit()
                logger.warning(
                    f"Marked stale sync {existing_sync.sync_id} as failed (running since {existing_sync.started_at}, no progress)"
                )
            else:
                # Sync is actually running, raise error
                raise ValueError(
                    f"Sync already running for tenant {tenant_id}: {existing_sync.sync_id} (started {started_at_value})"
                )

        # Create new sync job
        sync_id = f"sync_{tenant_id}_{int(datetime.now(UTC).timestamp())}"

        sync_job = SyncJob(
            sync_id=sync_id,
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            sync_type="inventory",
            status="running",
            started_at=datetime.now(UTC),
            triggered_by="admin_ui",
            triggered_by_id="system",
            progress={
                "phase": "Starting",
                "sync_types": sync_types,
                "custom_targeting_limit": custom_targeting_limit,
                "audience_segment_limit": audience_segment_limit,
            },
        )
        db.add(sync_job)
        db.commit()

    # Start background thread
    thread = threading.Thread(
        target=_run_sync_thread,
        args=(tenant_id, sync_id, sync_mode, sync_types, custom_targeting_limit, audience_segment_limit),
        daemon=True,
        name=f"sync-{sync_id}",
    )

    with _sync_lock:
        _active_syncs[sync_id] = thread

    thread.start()
    logger.info(f"Started background sync thread: {sync_id}")

    return sync_id


def _run_sync_thread(
    tenant_id: str,
    sync_id: str,
    sync_mode: str,
    sync_types: list[str] | None,
    custom_targeting_limit: int | None,
    audience_segment_limit: int | None,
):
    """
    Run the actual sync in a background thread with detailed phase-by-phase progress.

    This function runs in a separate thread and updates the SyncJob record
    as it progresses. If the thread is interrupted (container restart), the
    job will remain in 'running' state until cleaned up.

    Progress tracking:
    - Phase 0 (full mode only): Deleting existing inventory (1/7)
    - Phase 1: Discovering Ad Units (2/7 or 1/6)
    - Phase 2: Discovering Placements (3/7 or 2/6)
    - Phase 3: Discovering Labels (4/7 or 3/6)
    - Phase 4: Discovering Custom Targeting (5/7 or 4/6)
    - Phase 5: Discovering Audience Segments (6/7 or 5/6)
    - Phase 6: Marking Stale Inventory (7/7 or 6/6)
    """
    try:
        logger.info(f"[{sync_id}] Starting inventory sync for {tenant_id}")

        # Import here to avoid circular dependencies
        import os
        import tempfile

        import google.oauth2.service_account
        from googleads import ad_manager, oauth2

        from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery
        from src.core.database.models import AdapterConfig, Tenant
        from src.services.gam_inventory_service import GAMInventoryService

        # Get tenant and adapter config (fresh session per thread)
        with get_db_session() as db:
            tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                _mark_sync_failed(sync_id, "Tenant not found")
                return

            adapter_config = db.scalars(
                select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
            ).first()

            if not adapter_config or not adapter_config.gam_network_code:
                _mark_sync_failed(sync_id, "GAM not configured")
                return

            # Determine auth method
            auth_method = getattr(adapter_config, "gam_auth_method", None)
            if not auth_method:
                if adapter_config.gam_refresh_token:
                    auth_method = "oauth"
                elif hasattr(adapter_config, "gam_service_account_json") and adapter_config.gam_service_account_json:
                    auth_method = "service_account"
                else:
                    _mark_sync_failed(sync_id, "No GAM authentication configured")
                    return

            # Create GAM client based on auth method
            if auth_method == "service_account":
                service_account_json_str = adapter_config.gam_service_account_json
                if not service_account_json_str:
                    _mark_sync_failed(sync_id, "Service account JSON not found")
                    return

                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                    f.write(service_account_json_str)
                    temp_keyfile = f.name

                try:
                    credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                        temp_keyfile, scopes=["https://www.googleapis.com/auth/dfp"]
                    )
                    oauth2_client = oauth2.GoogleCredentialsClient(credentials)
                    client = ad_manager.AdManagerClient(
                        oauth2_client, "Prebid Sales Agent", network_code=adapter_config.gam_network_code
                    )
                finally:
                    try:
                        os.unlink(temp_keyfile)
                    except Exception:
                        pass
            else:  # OAuth
                oauth2_client = oauth2.GoogleRefreshTokenClient(
                    client_id=os.environ.get("GAM_OAUTH_CLIENT_ID"),
                    client_secret=os.environ.get("GAM_OAUTH_CLIENT_SECRET"),
                    refresh_token=adapter_config.gam_refresh_token,
                )
                client = ad_manager.AdManagerClient(
                    oauth2_client, "Prebid Sales Agent", network_code=adapter_config.gam_network_code
                )

        # Get last successful sync time for incremental mode
        last_sync_time = None
        if sync_mode == "incremental":
            with get_db_session() as db:
                from sqlalchemy import desc

                last_successful_sync = db.scalars(
                    select(SyncJob)
                    .where(
                        SyncJob.tenant_id == tenant_id,
                        SyncJob.sync_type == "inventory",
                        SyncJob.status == "completed",
                    )
                    .order_by(desc(SyncJob.completed_at))
                ).first()

                if last_successful_sync and last_successful_sync.started_at:
                    # Use started_at (not completed_at) to avoid missing items modified during the sync
                    last_sync_time = last_successful_sync.started_at
                    logger.info(f"[{sync_id}] Incremental sync: using last sync start time: {last_sync_time}")
                else:
                    logger.warning(
                        f"[{sync_id}] Incremental sync requested but no previous successful sync found - falling back to full sync"
                    )
                    sync_mode = "full"
                    last_sync_time = None

        # Calculate total phases
        total_phases = 7 if sync_mode == "full" else 6  # Add delete phase for full reset
        phase_offset = 1 if sync_mode == "full" else 0

        # Initialize discovery
        discovery = GAMInventoryDiscovery(client=client, tenant_id=tenant_id)
        start_time = datetime.now()

        # Helper function to update progress
        def update_progress(phase: str, phase_num: int, count: int = 0):
            _update_sync_progress(
                sync_id,
                {
                    "phase": phase,
                    "phase_num": phase_num,
                    "total_phases": total_phases,
                    "count": count,
                    "mode": sync_mode,
                },
            )

        # Phase 0: Full reset - delete all existing inventory (only for full sync)
        if sync_mode == "full":
            update_progress("Deleting Existing Inventory", 1)
            with get_db_session() as db:
                from sqlalchemy import delete

                from src.core.database.models import GAMInventory

                stmt = delete(GAMInventory).where(GAMInventory.tenant_id == tenant_id)
                db.execute(stmt)
                db.commit()
                logger.info(f"[{sync_id}] Full reset: deleted all existing inventory for tenant {tenant_id}")

        # Initialize inventory service for streaming writes
        with get_db_session() as db:
            inventory_service = GAMInventoryService(db)
            sync_time = datetime.now()

            # Phase 1: Ad Units (fetch → write → clear memory)
            update_progress("Discovering Ad Units", 1 + phase_offset)
            ad_units = discovery.discover_ad_units(since=last_sync_time)
            update_progress("Writing Ad Units to DB", 1 + phase_offset, len(ad_units))
            inventory_service._write_inventory_batch(tenant_id, "ad_unit", ad_units, sync_time)
            ad_units_count = len(ad_units)
            discovery.ad_units.clear()  # Clear from memory
            logger.info(f"[{sync_id}] Wrote {ad_units_count} ad units to database")

            # Phase 2: Placements (fetch → write → clear memory)
            update_progress("Discovering Placements", 2 + phase_offset)
            placements = discovery.discover_placements(since=last_sync_time)
            update_progress("Writing Placements to DB", 2 + phase_offset, len(placements))
            inventory_service._write_inventory_batch(tenant_id, "placement", placements, sync_time)
            placements_count = len(placements)
            discovery.placements.clear()  # Clear from memory
            logger.info(f"[{sync_id}] Wrote {placements_count} placements to database")

            # Phase 3: Labels (fetch → write → clear memory)
            update_progress("Discovering Labels", 3 + phase_offset)
            labels = discovery.discover_labels(since=last_sync_time)
            update_progress("Writing Labels to DB", 3 + phase_offset, len(labels))
            inventory_service._write_inventory_batch(tenant_id, "label", labels, sync_time)
            labels_count = len(labels)
            discovery.labels.clear()  # Clear from memory
            logger.info(f"[{sync_id}] Wrote {labels_count} labels to database")

            # Phase 4: Custom Targeting Keys (fetch → write → clear memory)
            update_progress("Discovering Targeting Keys", 4 + phase_offset)
            custom_targeting = discovery.discover_custom_targeting(fetch_values=False, since=last_sync_time)
            update_progress(
                "Writing Targeting Keys to DB",
                4 + phase_offset,
                custom_targeting.get("total_keys", 0),
            )
            inventory_service._write_custom_targeting_keys(
                tenant_id, list(discovery.custom_targeting_keys.values()), sync_time
            )
            targeting_count = len(discovery.custom_targeting_keys)
            discovery.custom_targeting_keys.clear()  # Clear from memory
            discovery.custom_targeting_values.clear()  # Clear from memory
            logger.info(f"[{sync_id}] Wrote {targeting_count} targeting keys to database")

            # Also update adapter_config.custom_targeting_keys for GAMTargetingManager
            # This mapping is used by resolve_custom_targeting_key_id() during Media Buy approval
            inventory_service._update_adapter_config_targeting_keys(tenant_id)
            logger.info(f"[{sync_id}] Updated adapter_config targeting key mapping")

            # Phase 5: Audience Segments (fetch → write → clear memory)
            # NOTE: Audience segments ALWAYS use full sync because GAM API doesn't support
            # lastModifiedDateTime filtering (returns ParseError.UNPARSABLE).
            # This is a known GAM API limitation, not a bug in our code.
            update_progress("Discovering Audience Segments", 5 + phase_offset)
            audience_segments = discovery.discover_audience_segments(since=None)  # Always None = full sync
            update_progress("Writing Audience Segments to DB", 5 + phase_offset, len(audience_segments))
            inventory_service._write_inventory_batch(tenant_id, "audience_segment", audience_segments, sync_time)
            segments_count = len(audience_segments)
            discovery.audience_segments.clear()  # Clear from memory
            logger.info(
                f"[{sync_id}] Wrote {segments_count} audience segments to database (always full sync - GAM API limitation)"
            )

            # Phase 6: Mark stale inventory (ONLY for full sync)
            # In incremental mode, we intentionally don't fetch unchanged items,
            # so we can't mark them as stale - they're still valid in GAM.
            # See GitHub issue #812: Incremental sync incorrectly marks unchanged placements as STALE
            if sync_mode == "full":
                update_progress("Marking Stale Inventory", 6 + phase_offset)
                inventory_service._mark_stale_inventory(tenant_id, sync_time)
            else:
                logger.info(f"[{sync_id}] Skipping stale marking for incremental sync")

        # Build result summary
        end_time = datetime.now()

        # For incremental sync, also report total counts from database (not just newly synced items)
        if sync_mode == "incremental":
            with get_db_session() as db:
                # Import here to avoid circular imports
                from sqlalchemy import func

                from src.core.database.models import GAMInventory

                # Count total items by inventory_type
                total_ad_units = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "ad_unit")
                    )
                    or 0
                )

                total_placements = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "placement")
                    )
                    or 0
                )

                total_labels = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "label")
                    )
                    or 0
                )

                total_audience_segments = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "audience_segment")
                    )
                    or 0
                )

                total_targeting_keys = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(
                            GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "custom_targeting_key"
                        )
                    )
                    or 0
                )

                # For incremental, show both synced count and total count
                ad_units_summary = {"synced": ad_units_count, "total": total_ad_units}
                placements_summary = {"synced": placements_count, "total": total_placements}
                labels_summary = {"synced": labels_count, "total": total_labels}
                targeting_summary = {
                    "synced": targeting_count,
                    "total_keys": total_targeting_keys,
                    "note": "Values lazy loaded on demand",
                }
                segments_summary = {"synced": segments_count, "total": total_audience_segments}
        else:
            # For full sync, synced count == total count
            ad_units_summary = {"total": ad_units_count}
            placements_summary = {"total": placements_count}
            labels_summary = {"total": labels_count}
            targeting_summary = {"total_keys": targeting_count, "note": "Values lazy loaded on demand"}
            segments_summary = {"total": segments_count}

        result = {
            "tenant_id": tenant_id,
            "sync_time": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "mode": sync_mode,
            "ad_units": ad_units_summary,
            "placements": placements_summary,
            "labels": labels_summary,
            "custom_targeting": targeting_summary,
            "audience_segments": segments_summary,
            "streaming": True,
            "memory_optimized": True,
        }

        # Mark complete
        _mark_sync_complete(sync_id, result)
        logger.info(f"[{sync_id}] Sync completed successfully")

        # Invalidate inventory tree cache after successful sync
        try:
            from flask import current_app

            cache = getattr(current_app, "cache", None)
            if cache:
                cache_key = f"inventory_tree:v2:{tenant_id}"
                cache.delete(cache_key)
                logger.info(f"[{sync_id}] Invalidated inventory tree cache: {cache_key}")
        except Exception as cache_error:
            # Don't fail the sync if cache invalidation fails
            logger.warning(f"[{sync_id}] Failed to invalidate cache: {cache_error}")

    except Exception as e:
        logger.error(f"[{sync_id}] Sync failed: {e}", exc_info=True)
        _mark_sync_failed(sync_id, str(e))

    finally:
        # Remove from active syncs
        with _sync_lock:
            _active_syncs.pop(sync_id, None)


def _update_sync_progress(sync_id: str, progress_data: dict[str, Any]):
    """Update sync job progress in database."""
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == sync_id)
            sync_job = db.scalars(stmt).first()
            if sync_job:
                sync_job.progress = progress_data
                db.commit()
    except Exception as e:
        logger.warning(f"Failed to update sync progress: {e}")


def _mark_sync_complete(sync_id: str, summary: dict[str, Any]):
    """Mark sync as completed with summary."""
    try:
        with get_db_session() as db:
            import json

            stmt = select(SyncJob).where(SyncJob.sync_id == sync_id)
            sync_job = db.scalars(stmt).first()
            if sync_job:
                sync_job.status = "completed"
                # SQLAlchemy DateTime column accepts datetime objects
                sync_job.completed_at = datetime.now(UTC)
                # Convert summary dict to JSON string (summary field is Text, not JSON)
                sync_job.summary = json.dumps(summary) if summary else None
                db.commit()
    except Exception as e:
        logger.error(f"Failed to mark sync complete: {e}")


def _mark_sync_failed(sync_id: str, error_message: str):
    """Mark sync as failed with error message."""
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == sync_id)
            sync_job = db.scalars(stmt).first()
            if sync_job:
                sync_job.status = "failed"
                # SQLAlchemy DateTime column accepts datetime objects
                completed_at = datetime.now(UTC)
                sync_job.completed_at = completed_at
                sync_job.error_message = error_message
                # Note: SyncJob doesn't have duration_seconds field - duration is calculated from started_at/completed_at
                db.commit()
    except Exception as e:
        logger.error(f"Failed to mark sync failed: {e}")


def get_active_syncs() -> list[str]:
    """Get list of sync IDs currently running in background threads."""
    with _sync_lock:
        return list(_active_syncs.keys())


def is_sync_running(sync_id: str) -> bool:
    """Check if a sync is currently running in a background thread."""
    with _sync_lock:
        return sync_id in _active_syncs

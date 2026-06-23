"""
Background sync service for long-running inventory syncs.

This service runs syncs in background threads to prevent blocking the web server
and losing progress on container restarts.
"""

import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob

logger = logging.getLogger(__name__)

# Global registry of running sync threads
_active_syncs: dict[str, threading.Thread] = {}
_sync_lock = threading.Lock()
STALE_RUNNING_SYNC_AFTER = timedelta(hours=1)


@contextmanager
def _sync_session():
    """Open a DB session flagged as a platform background worker.

    Inventory sync writes platform-managed columns (notably
    ``adapter_config.custom_targeting_keys``, which the GAM targeting
    manager reads at media-buy approval time). The embedded-tenant guard
    in :mod:`src.core.database.embedded_tenant_guard` blocks writes to
    those surfaces unless the session is flagged. Sync workers run on
    behalf of the platform — kicked off by the Tenant Management API's
    first-sync-on-provision hook, by ``POST /tenants/{tid}/refresh``, or
    by cron — so every sync session is platform-authorized.
    """
    with get_db_session() as db:
        db.info["platform_background_worker"] = True
        yield db


def _is_stale_running_sync(sync_job: SyncJob, *, now: datetime | None = None) -> bool:
    """Return whether a running SyncJob is old enough to be treated as dead.

    Progress is not evidence that the sync is still alive: it may be a stale
    phase payload left behind by a thread or container that died mid-sync.
    This guard only runs when a caller is trying to start another sync for the
    same tenant, so an hour-old running row is safer to clear than to let it
    block future refreshes indefinitely.
    """
    started_at = sync_job.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)

    return (now or datetime.now(UTC)) - started_at > STALE_RUNNING_SYNC_AFTER


def _sync_types_include(sync_types: list[str] | None, sync_type: str) -> bool:
    """Return whether an inventory worker run includes a bundled sub-sync."""
    return sync_types is None or sync_type in sync_types


def _new_custom_targeting_sync_id(tenant_id: str) -> str:
    return f"sync_{tenant_id}_custom_targeting_{uuid.uuid4().hex[:8]}"


def _invalidate_inventory_tree_cache(tenant_id: str, sync_id: str) -> None:
    """Invalidate Flask cache when the worker is running inside an app context."""
    try:
        from flask import current_app, has_app_context

        if not has_app_context():
            logger.debug("[%s] Skipping inventory tree cache invalidation: no Flask app context", sync_id)
            return

        cache = getattr(current_app, "cache", None)
        if cache is None:
            return

        cache_key = f"inventory_tree:v2:{tenant_id}"
        cache.delete(cache_key)
        logger.info("[%s] Invalidated inventory tree cache: %s", sync_id, cache_key)
    except Exception as cache_error:
        # Don't fail the sync if cache invalidation fails.
        logger.warning("[%s] Failed to invalidate cache: %s", sync_id, cache_error)


def start_inventory_sync_background(
    tenant_id: str,
    sync_mode: str = "incremental",
    sync_types: list[str] | None = None,
    custom_targeting_limit: int | None = None,
    audience_segment_limit: int | None = None,
    *,
    pending_sync_id: str | None = None,
    targeting_sync_id: str | None = None,
    triggered_by: str = "admin_ui",
    triggered_by_id: str | None = "system",
) -> str:
    """
    Start an inventory sync in the background.

    Args:
        tenant_id: Tenant ID to sync
        sync_mode: "full" (fetch all and mark stale after success) or "incremental" (only sync changed items since last successful sync)
        sync_types: Optional list of inventory types to sync
        custom_targeting_limit: Optional limit on custom targeting values
        audience_segment_limit: Optional limit on audience segments
        pending_sync_id: If provided, transition that existing pending row
            from ``pending → running`` instead of creating a new SyncJob.
            Lets ``POST /tenants/{tid}/refresh`` own the row creation while
            this function spawns the worker. When omitted (admin-button
            path), creates a fresh row as before.
        targeting_sync_id: Optional companion ``custom_targeting`` SyncJob
            row id. Inventory sync does targeting work internally; the
            companion row is transitioned alongside the inventory row so
            the publisher's per-type progress UI reflects reality.
        triggered_by: Provenance stamped on newly-created rows. Ignored
            when ``pending_sync_id`` points at an existing row.
        triggered_by_id: Optional actor/source stamped on newly-created rows.

    Returns:
        sync_id: The sync job ID for tracking progress

    Raises:
        ValueError: If a sync is already running for this tenant
    """

    # Create sync job record
    with _sync_session() as db:
        # Get adapter type for the tenant via repository
        from src.core.database.repositories.adapter_config import AdapterConfigRepository

        adapter_repo = AdapterConfigRepository(db, tenant_id)
        adapter_type = adapter_repo.get_adapter_type() or "mock"

        # Check if sync already running
        stmt = select(SyncJob).where(
            SyncJob.tenant_id == tenant_id, SyncJob.status == "running", SyncJob.sync_type == "inventory"
        )
        existing_sync = db.scalars(stmt).first()

        # When the caller passed a pending_sync_id, "already running" means
        # something OTHER than this row — the row we're about to transition
        # is in pending state, not running, so it shouldn't trip the
        # in-progress guard. Skip the guard if the running row matches our
        # pending id (defensive — shouldn't happen).
        if existing_sync and existing_sync.sync_id != pending_sync_id:
            if _is_stale_running_sync(existing_sync):
                # Mark stale sync as failed and allow new sync to start
                existing_sync.status = "failed"
                # SQLAlchemy DateTime column accepts datetime objects
                existing_sync.completed_at = datetime.now(UTC)
                existing_sync.error_message = (
                    "Sync thread died (stale after 1+ hour) - marked as failed to allow fresh sync"
                )
                db.commit()
                logger.warning(
                    f"Marked stale sync {existing_sync.sync_id} as failed (running since {existing_sync.started_at})"
                )
            else:
                # Sync is actually running, raise error
                raise ValueError(
                    f"Sync already running for tenant {tenant_id}: {existing_sync.sync_id} "
                    f"(started {existing_sync.started_at})"
                )

        # Use the caller-supplied pending row when provided; otherwise create
        # a fresh row (admin-button / cron path).
        worker_started_at = datetime.now(UTC)

        if pending_sync_id is not None:
            pending_row = db.scalars(select(SyncJob).filter_by(sync_id=pending_sync_id)).first()
            if pending_row is None:
                # Caller said "use this id" but it doesn't exist — be loud,
                # don't silently fall through to creating a new row.
                raise ValueError(
                    f"start_inventory_sync_background called with pending_sync_id="
                    f"{pending_sync_id!r} but no SyncJob row matches"
                )
            sync_id = pending_row.sync_id
            pending_row.status = "running"
            # Restamp ``started_at`` so the value reflects when the worker
            # actually picked up the row, not when /refresh queued it.
            # The 60s idempotency window in ``_create_and_spawn_refresh``
            # compares against ``started_at`` — without restamping, a row
            # that sat pending for >60s and just transitioned to running
            # would falsely look like a stale in-flight conflict on the
            # next /refresh.
            pending_row.started_at = worker_started_at
            pending_row.progress = {
                "phase": "Starting",
                "sync_types": sync_types,
                "custom_targeting_limit": custom_targeting_limit,
                "audience_segment_limit": audience_segment_limit,
            }
        else:
            sync_id = f"sync_{tenant_id}_{int(worker_started_at.timestamp())}"
            sync_job = SyncJob(
                sync_id=sync_id,
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                sync_type="inventory",
                status="running",
                started_at=worker_started_at,
                triggered_by=triggered_by,
                triggered_by_id=triggered_by_id,
                progress={
                    "phase": "Starting",
                    "sync_types": sync_types,
                    "custom_targeting_limit": custom_targeting_limit,
                    "audience_segment_limit": audience_segment_limit,
                },
            )
            db.add(sync_job)

        # Inventory sync covers custom targeting internally. Guarantee a
        # companion custom_targeting row for every full/bundled run, whether
        # it came from /refresh (pre-created row) or from scheduler/admin paths
        # that only enqueue inventory.
        if targeting_sync_id is None and _sync_types_include(sync_types, "custom_targeting"):
            targeting_sync_id = _new_custom_targeting_sync_id(tenant_id)

        if targeting_sync_id is not None:
            targeting_row = db.scalars(select(SyncJob).filter_by(sync_id=targeting_sync_id)).first()
            if targeting_row is not None:
                targeting_row.status = "running"
                # Restamp so the worker-pickup time is what ``/refresh``
                # idempotency compares against (see comment on
                # ``pending_row.started_at`` above).
                targeting_row.started_at = worker_started_at
                targeting_row.progress = {"phase": "Starting", "bundled_with": sync_id}
            else:
                targeting_row = SyncJob(
                    sync_id=targeting_sync_id,
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    sync_type="custom_targeting",
                    status="running",
                    started_at=worker_started_at,
                    triggered_by=triggered_by,
                    triggered_by_id=triggered_by_id,
                    progress={"phase": "Starting", "bundled_with": sync_id},
                )
                db.add(targeting_row)

        db.commit()

    # Start background thread
    thread = threading.Thread(
        target=_run_sync_thread,
        args=(
            tenant_id,
            sync_id,
            sync_mode,
            sync_types,
            custom_targeting_limit,
            audience_segment_limit,
            targeting_sync_id,
        ),
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
    targeting_sync_id: str | None = None,
):
    """
    Run the actual sync in a background thread with detailed phase-by-phase progress.

    This function runs in a separate thread and updates the SyncJob record
    as it progresses. If the thread is interrupted (container restart), the
    job will remain in 'running' state until cleaned up.

    Progress tracking:
    - Phase 0 (full mode only): Preparing full inventory sync (1/7)
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
        from src.adapters.gam import GAMClientManager, build_gam_config_from_adapter
        from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery
        from src.core.database.models import Tenant
        from src.services.gam_inventory_service import GAMInventoryService

        # Get tenant and adapter config (fresh session per thread)
        with _sync_session() as db:
            tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                _mark_sync_failed(sync_id, "Tenant not found")
                return

            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            adapter_repo = AdapterConfigRepository(db, tenant_id)
            adapter_config = adapter_repo.find_by_tenant()

            if (
                not adapter_config
                or adapter_config.adapter_type != "google_ad_manager"
                or not adapter_config.gam_network_code
            ):
                _mark_sync_failed(sync_id, "GAM not configured")
                return

            # build_gam_config_from_adapter detects auth method from credential
            # presence (service_account_json wins over refresh_token), so this
            # path stays consistent with the connection-test and advertisers
            # sync paths even when the row's gam_auth_method column is stale.
            gam_config = build_gam_config_from_adapter(adapter_config)
            if "service_account_json" not in gam_config and "refresh_token" not in gam_config:
                _mark_sync_failed(sync_id, "No GAM authentication configured")
                return

            client = GAMClientManager(gam_config, network_code=adapter_config.gam_network_code).get_client()

        # Get last successful sync time for incremental mode
        last_sync_time = None
        if sync_mode == "incremental":
            with _sync_session() as db:
                from sqlalchemy import desc, func

                from src.core.database.models import GAMInventory

                current_ad_units = (
                    db.scalar(
                        select(func.count())
                        .select_from(GAMInventory)
                        .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == "ad_unit")
                    )
                    or 0
                )

                if current_ad_units == 0:
                    logger.warning(
                        f"[{sync_id}] Incremental sync requested but tenant has no ad units cached - "
                        "falling back to full sync"
                    )
                    sync_mode = "full"
                else:
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
                            f"[{sync_id}] Incremental sync requested but no previous successful sync found - "
                            "falling back to full sync"
                        )
                        sync_mode = "full"
                        last_sync_time = None

        # Calculate total phases
        total_phases = 7 if sync_mode == "full" else 6  # Add delete phase for full reset
        phase_offset = 1 if sync_mode == "full" else 0

        # Initialize discovery
        discovery = GAMInventoryDiscovery(client=client, tenant_id=tenant_id)
        start_time = datetime.now(UTC)

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

        # Phase 0: Full sync setup. Do not delete existing inventory before
        # the first GAM read: if credentials fail or the thread dies, deleting
        # up front empties the catalog and blocks selector binding. Successful
        # full syncs upsert fresh rows and mark untouched non-ad-unit rows stale
        # at the end.
        if sync_mode == "full":
            update_progress("Preparing Full Inventory Sync", 1)
            logger.info(f"[{sync_id}] Full sync will preserve existing inventory until fresh GAM data is written")

        # Initialize inventory service for streaming writes
        with _sync_session() as db:
            inventory_service = GAMInventoryService(db)
            sync_time = datetime.now(UTC)

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
        end_time = datetime.now(UTC)

        # For incremental sync, also report total counts from database (not just newly synced items)
        if sync_mode == "incremental":
            with _sync_session() as db:
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
        # Mirror completion onto the companion custom_targeting row when
        # /refresh fanned one out — same lifecycle, bundled work.
        if targeting_sync_id is not None:
            _mark_sync_complete(
                targeting_sync_id,
                {
                    "bundled_with": sync_id,
                    "summary": "custom_targeting synced as part of inventory",
                    "custom_targeting": targeting_summary,
                },
            )
        logger.info(f"[{sync_id}] Sync completed successfully")

        # Invalidate inventory tree cache after successful sync.
        _invalidate_inventory_tree_cache(tenant_id, sync_id)

        # Sync GAM orders alongside inventory — same GAM client config.
        try:
            from src.services.gam_advertisers_sync import _build_gam_client_for_tenant
            from src.services.gam_orders_service import GAMOrdersService
            with _sync_session() as db:
                GAMOrdersService(db).sync_tenant_orders(tenant_id, _build_gam_client_for_tenant(tenant_id))
        except Exception as _oe:
            logger.warning("[%s] GAM orders sync after inventory failed: %s", sync_id, _oe)

    except Exception as e:
        logger.error(f"[{sync_id}] Sync failed: {e}", exc_info=True)
        _mark_sync_failed(sync_id, str(e))
        # Targeting companion row tracks the inventory lifecycle.
        if targeting_sync_id is not None:
            _mark_sync_failed(targeting_sync_id, f"Bundled inventory sync failed: {e}")

    finally:
        # Remove from active syncs
        with _sync_lock:
            _active_syncs.pop(sync_id, None)


def _update_sync_progress(sync_id: str, progress_data: dict[str, Any]):
    """Update sync job progress in database."""
    try:
        with _sync_session() as db:
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
        with _sync_session() as db:
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
        with _sync_session() as db:
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

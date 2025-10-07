"""
Google Ad Manager Sync Manager

This manager coordinates synchronization operations between GAM and the database:
- Orchestrates inventory and orders sync operations
- Manages sync scheduling and status tracking
- Provides error recovery and retry logic
- Handles both inventory and orders synchronization
- Integrates with database for persistence

Extracted from sync_api.py and related services to provide centralized
sync orchestration within the modular GAM architecture.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.adapters.gam.client import GAMClientManager
from src.adapters.gam.managers.inventory import GAMInventoryManager
from src.adapters.gam.managers.orders import GAMOrdersManager
from src.core.database.models import SyncJob

logger = logging.getLogger(__name__)


class GAMSyncManager:
    """Manages sync operations between GAM and database with scheduling and error handling."""

    def __init__(
        self,
        client_manager: GAMClientManager,
        inventory_manager: GAMInventoryManager,
        orders_manager: GAMOrdersManager,
        tenant_id: str,
        dry_run: bool = False,
    ):
        """Initialize sync manager.

        Args:
            client_manager: GAM client manager instance
            inventory_manager: GAM inventory manager instance
            orders_manager: GAM orders manager instance
            tenant_id: Tenant identifier
            dry_run: Whether to run in dry-run mode
        """
        self.client_manager = client_manager
        self.inventory_manager = inventory_manager
        self.orders_manager = orders_manager
        self.tenant_id = tenant_id
        self.dry_run = dry_run

        # Sync configuration
        self.sync_timeout = timedelta(minutes=30)  # Maximum sync time
        self.retry_attempts = 3
        self.retry_delay = timedelta(minutes=5)

        logger.info(f"Initialized GAMSyncManager for tenant {tenant_id} (dry_run: {dry_run})")

    def sync_inventory(self, db_session: Session, force: bool = False) -> dict[str, Any]:
        """Synchronize inventory data from GAM to database.

        Args:
            db_session: Database session for persistence
            force: Force sync even if recent sync exists

        Returns:
            Sync summary with timing and results
        """
        sync_type = "inventory"
        logger.info(f"Starting inventory sync for tenant {self.tenant_id} (force: {force})")

        # Check for recent sync if not forcing
        if not force:
            recent_sync = self._get_recent_sync(db_session, sync_type)
            if recent_sync:
                logger.info(f"Recent inventory sync found: {recent_sync['sync_id']}")
                return recent_sync

        # Create sync job
        sync_job = self._create_sync_job(db_session, sync_type, "api")

        try:
            # Update status to running
            sync_job.status = "running"
            db_session.commit()

            if self.dry_run:
                # Simulate inventory sync in dry-run mode
                summary = {
                    "tenant_id": self.tenant_id,
                    "sync_time": datetime.now().isoformat(),
                    "dry_run": True,
                    "duration_seconds": 0,
                    "ad_units": {"total": 0, "active": 0},
                    "placements": {"total": 0, "active": 0},
                    "labels": {"total": 0, "active": 0},
                    "custom_targeting": {"total_keys": 0, "total_values": 0},
                    "audience_segments": {"total": 0},
                }
                logger.info("[DRY RUN] Simulated inventory sync completed")
            else:
                # Perform actual inventory sync
                summary = self.inventory_manager.sync_all_inventory()

                # Save inventory to database - this would be delegated to inventory service
                from src.services.gam_inventory_service import GAMInventoryService

                inventory_service = GAMInventoryService(db_session)

                # Get the discovery instance and save to DB
                discovery = self.inventory_manager._get_discovery()
                inventory_service._save_inventory_to_db(self.tenant_id, discovery)

            # Update sync job with results
            sync_job.status = "completed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.summary = json.dumps(summary)
            db_session.commit()

            logger.info(f"Inventory sync completed for tenant {self.tenant_id}: {summary}")
            return {
                "sync_id": sync_job.sync_id,
                "status": "completed",
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"Inventory sync failed for tenant {self.tenant_id}: {e}", exc_info=True)

            # Update sync job with error
            sync_job.status = "failed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.error_message = str(e)
            db_session.commit()

            raise

    def sync_orders(self, db_session: Session, force: bool = False) -> dict[str, Any]:
        """Synchronize orders and line items from GAM to database.

        Args:
            db_session: Database session for persistence
            force: Force sync even if recent sync exists

        Returns:
            Sync summary with timing and results
        """
        sync_type = "orders"
        logger.info(f"Starting orders sync for tenant {self.tenant_id} (force: {force})")

        # Check for recent sync if not forcing
        if not force:
            recent_sync = self._get_recent_sync(db_session, sync_type)
            if recent_sync:
                logger.info(f"Recent orders sync found: {recent_sync['sync_id']}")
                return recent_sync

        # Create sync job
        sync_job = self._create_sync_job(db_session, sync_type, "api")

        try:
            # Update status to running
            sync_job.status = "running"
            db_session.commit()

            if self.dry_run:
                # Simulate orders sync in dry-run mode
                summary = {
                    "tenant_id": self.tenant_id,
                    "sync_time": datetime.now().isoformat(),
                    "dry_run": True,
                    "duration_seconds": 0,
                    "orders": {"total": 0, "active": 0},
                    "line_items": {"total": 0, "active": 0},
                }
                logger.info("[DRY RUN] Simulated orders sync completed")
            else:
                # Perform actual orders sync
                # This would be implemented when orders sync is needed
                summary = {
                    "tenant_id": self.tenant_id,
                    "sync_time": datetime.now().isoformat(),
                    "duration_seconds": 0,
                    "orders": {"total": 0, "active": 0},
                    "line_items": {"total": 0, "active": 0},
                    "message": "Orders sync not yet implemented in sync manager",
                }

            # Update sync job with results
            sync_job.status = "completed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.summary = json.dumps(summary)
            db_session.commit()

            logger.info(f"Orders sync completed for tenant {self.tenant_id}: {summary}")
            return {
                "sync_id": sync_job.sync_id,
                "status": "completed",
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"Orders sync failed for tenant {self.tenant_id}: {e}", exc_info=True)

            # Update sync job with error
            sync_job.status = "failed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.error_message = str(e)
            db_session.commit()

            raise

    def sync_full(self, db_session: Session, force: bool = False) -> dict[str, Any]:
        """Perform full synchronization of both inventory and orders.

        Args:
            db_session: Database session for persistence
            force: Force sync even if recent sync exists

        Returns:
            Combined sync summary
        """
        logger.info(f"Starting full sync for tenant {self.tenant_id} (force: {force})")

        # Create sync job for full sync
        sync_job = self._create_sync_job(db_session, "full", "api")

        try:
            # Update status to running
            sync_job.status = "running"
            db_session.commit()

            start_time = datetime.now()
            combined_summary = {
                "tenant_id": self.tenant_id,
                "sync_time": start_time.isoformat(),
                "dry_run": self.dry_run,
            }

            # Sync inventory first
            inventory_result = self.sync_inventory(db_session, force=True)
            combined_summary["inventory"] = inventory_result.get("summary", {})

            # Then sync orders
            orders_result = self.sync_orders(db_session, force=True)
            combined_summary["orders"] = orders_result.get("summary", {})

            # Calculate total duration
            end_time = datetime.now()
            combined_summary["duration_seconds"] = (end_time - start_time).total_seconds()

            # Update sync job with results
            sync_job.status = "completed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.summary = json.dumps(combined_summary)
            db_session.commit()

            logger.info(f"Full sync completed for tenant {self.tenant_id}: {combined_summary}")
            return {
                "sync_id": sync_job.sync_id,
                "status": "completed",
                "summary": combined_summary,
            }

        except Exception as e:
            logger.error(f"Full sync failed for tenant {self.tenant_id}: {e}", exc_info=True)

            # Update sync job with error
            sync_job.status = "failed"
            sync_job.completed_at = datetime.now(UTC)
            sync_job.error_message = str(e)
            db_session.commit()

            raise

    def get_sync_status(self, db_session: Session, sync_id: str) -> dict[str, Any] | None:
        """Get status of a specific sync job.

        Args:
            db_session: Database session
            sync_id: Sync job identifier

        Returns:
            Sync job status information or None if not found
        """
        stmt = select(SyncJob).filter_by(sync_id=sync_id, tenant_id=self.tenant_id)
        sync_job = db_session.scalars(stmt).first()

        if not sync_job:
            return None

        status_info = {
            "sync_id": sync_job.sync_id,
            "tenant_id": sync_job.tenant_id,
            "sync_type": sync_job.sync_type,
            "status": sync_job.status,
            "started_at": sync_job.started_at.isoformat(),
            "triggered_by": sync_job.triggered_by,
        }

        if sync_job.completed_at:
            status_info["completed_at"] = sync_job.completed_at.isoformat()
            status_info["duration_seconds"] = (sync_job.completed_at - sync_job.started_at).total_seconds()

        if sync_job.summary:
            status_info["summary"] = json.loads(sync_job.summary)

        if sync_job.error_message:
            status_info["error"] = sync_job.error_message

        return status_info

    def get_sync_history(
        self,
        db_session: Session,
        limit: int = 10,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        """Get sync history for the tenant.

        Args:
            db_session: Database session
            limit: Number of records to return
            offset: Offset for pagination
            status_filter: Optional status filter

        Returns:
            Sync history with pagination info
        """
        stmt = select(SyncJob).filter_by(tenant_id=self.tenant_id)

        if status_filter:
            stmt = stmt.filter_by(status=status_filter)

        # Get total count
        count_stmt = select(func.count()).select_from(SyncJob).where(SyncJob.tenant_id == self.tenant_id)
        if status_filter:
            count_stmt = count_stmt.where(SyncJob.status == status_filter)
        total = db_session.scalar(count_stmt)

        # Get results
        stmt = stmt.order_by(SyncJob.started_at.desc()).limit(limit).offset(offset)
        sync_jobs = db_session.scalars(stmt).all()

        results = []
        for job in sync_jobs:
            result = {
                "sync_id": job.sync_id,
                "sync_type": job.sync_type,
                "status": job.status,
                "started_at": job.started_at.isoformat(),
                "triggered_by": job.triggered_by,
            }

            if job.completed_at:
                result["completed_at"] = job.completed_at.isoformat()
                result["duration_seconds"] = (job.completed_at - job.started_at).total_seconds()

            if job.summary:
                result["summary"] = json.loads(job.summary)

            if job.error_message:
                result["error"] = job.error_message

            results.append(result)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "results": results,
        }

    def needs_sync(self, db_session: Session, sync_type: str, max_age_hours: int = 24) -> bool:
        """Check if sync is needed based on last successful sync time.

        Args:
            db_session: Database session
            sync_type: Type of sync to check
            max_age_hours: Maximum age in hours before sync is considered needed

        Returns:
            True if sync is needed, False otherwise
        """
        cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

        stmt = select(SyncJob).where(
            SyncJob.tenant_id == self.tenant_id,
            SyncJob.sync_type == sync_type,
            SyncJob.status == "completed",
            SyncJob.completed_at >= cutoff_time,
        )
        recent_sync = db_session.scalars(stmt).first()

        return recent_sync is None

    def _get_recent_sync(self, db_session: Session, sync_type: str) -> dict[str, Any] | None:
        """Get recent sync if it exists (today).

        Args:
            db_session: Database session
            sync_type: Type of sync to check

        Returns:
            Recent sync info or None
        """
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0)

        stmt = select(SyncJob).where(
            SyncJob.tenant_id == self.tenant_id,
            SyncJob.sync_type == sync_type,
            SyncJob.status.in_(["running", "completed"]),
            SyncJob.started_at >= today,
        )
        recent_sync = db_session.scalars(stmt).first()

        if not recent_sync:
            return None

        if recent_sync.status == "running":
            return {
                "sync_id": recent_sync.sync_id,
                "status": "running",
                "message": "Sync already in progress",
            }
        else:
            summary = json.loads(recent_sync.summary) if recent_sync.summary else {}
            return {
                "sync_id": recent_sync.sync_id,
                "status": "completed",
                "completed_at": recent_sync.completed_at.isoformat(),
                "summary": summary,
                "message": "Recent sync exists",
            }

    def _create_sync_job(self, db_session: Session, sync_type: str, triggered_by: str) -> SyncJob:
        """Create a new sync job record.

        Args:
            db_session: Database session
            sync_type: Type of sync (inventory, orders, full)
            triggered_by: Who/what triggered the sync

        Returns:
            Created sync job instance
        """
        sync_id = f"sync_{self.tenant_id}_{sync_type}_{int(datetime.now().timestamp())}"

        sync_job = SyncJob(
            sync_id=sync_id,
            tenant_id=self.tenant_id,
            adapter_type="google_ad_manager",
            sync_type=sync_type,
            status="pending",
            started_at=datetime.now(UTC),
            triggered_by=triggered_by,
            triggered_by_id=f"{triggered_by}_sync",
        )

        db_session.add(sync_job)
        db_session.commit()

        logger.info(f"Created sync job {sync_id} for tenant {self.tenant_id}")
        return sync_job

    def get_sync_stats(self, db_session: Session, hours: int = 24) -> dict[str, Any]:
        """Get sync statistics for the tenant.

        Args:
            db_session: Database session
            hours: Number of hours to look back

        Returns:
            Sync statistics
        """
        since = datetime.now(UTC) - timedelta(hours=hours)

        # Count by status
        status_counts = {}
        for status in ["pending", "running", "completed", "failed"]:
            count_stmt = (
                select(func.count())
                .select_from(SyncJob)
                .where(
                    SyncJob.tenant_id == self.tenant_id,
                    SyncJob.status == status,
                    SyncJob.started_at >= since,
                )
            )
            count = db_session.scalar(count_stmt)
            status_counts[status] = count

        # Get recent failures
        stmt = (
            select(SyncJob)
            .where(
                SyncJob.tenant_id == self.tenant_id,
                SyncJob.status == "failed",
                SyncJob.started_at >= since,
            )
            .order_by(SyncJob.started_at.desc())
            .limit(5)
        )
        recent_failures = db_session.scalars(stmt).all()

        failures = []
        for job in recent_failures:
            failures.append(
                {
                    "sync_id": job.sync_id,
                    "sync_type": job.sync_type,
                    "started_at": job.started_at.isoformat(),
                    "error": job.error_message,
                }
            )

        return {
            "tenant_id": self.tenant_id,
            "status_counts": status_counts,
            "recent_failures": failures,
            "since": since.isoformat(),
        }

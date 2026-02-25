"""Inventory sync service — business logic for triggering and monitoring sync jobs.

Extracted from src/admin/sync_api.py Flask blueprint.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, SyncJob, Tenant
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class InventorySyncService:
    """Stateless service for inventory sync operations."""

    def trigger_sync(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with get_db_session() as session:
            # Validate tenant
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                raise AdCPNotFoundError(f"Tenant '{tenant_id}' not found")

            if tenant.ad_server != "google_ad_manager":
                raise AdCPValidationError("Only Google Ad Manager sync is currently supported")

            adapter_config = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
            if not adapter_config:
                raise AdCPValidationError("Adapter not configured")

            sync_type = data.get("sync_type", "full")
            force = data.get("force", False)
            custom_targeting_limit = data.get("custom_targeting_limit", 1000)
            audience_segment_limit = data.get("audience_segment_limit")
            sync_types = data.get("sync_types", [])

            # Validate sync_type before creating the job
            valid_sync_types = ("full", "inventory", "targeting", "selective")
            if sync_type not in valid_sync_types:
                raise AdCPValidationError(
                    f"Unsupported sync type: {sync_type}. Must be one of: {', '.join(valid_sync_types)}"
                )
            if sync_type == "selective" and not sync_types:
                raise AdCPValidationError("sync_types required for selective sync")

            # Check for recent sync
            if not force:
                sync_stmt = select(SyncJob).where(
                    SyncJob.tenant_id == tenant_id,
                    SyncJob.status.in_(["running", "completed"]),
                    SyncJob.started_at >= datetime.now(UTC).replace(hour=0, minute=0, second=0),
                )
                recent = session.scalars(sync_stmt).first()
                if recent:
                    if recent.status == "running":
                        return {"message": "Sync already in progress", "sync_id": recent.sync_id}
                    elif recent.completed_at:
                        return {
                            "message": "Recent sync exists",
                            "sync_id": recent.sync_id,
                            "completed_at": recent.completed_at.isoformat(),
                        }

            # Create sync job
            sync_id = f"sync_{tenant_id}_{int(datetime.now(UTC).timestamp())}"
            sync_job = SyncJob(
                sync_id=sync_id,
                tenant_id=tenant_id,
                adapter_type="google_ad_manager",
                sync_type=sync_type,
                status="pending",
                started_at=datetime.now(UTC),
                triggered_by="api",
                triggered_by_id="admin_api",
            )
            session.add(sync_job)
            session.commit()

            # Run sync
            try:
                from src.adapters.google_ad_manager import GoogleAdManager
                from src.core.schemas import Principal

                principal = Principal(principal_id="system", name="System", platform_mappings={})
                gam_config: dict[str, Any] = {
                    "enabled": True,
                    "network_code": adapter_config.gam_network_code,
                    "refresh_token": adapter_config.gam_refresh_token,
                    "trafficker_id": adapter_config.gam_trafficker_id,
                    "manual_approval_required": adapter_config.gam_manual_approval_required,
                }

                adapter = GoogleAdManager(
                    config=gam_config,
                    principal=principal,
                    network_code=adapter_config.gam_network_code or "",
                    advertiser_id=None,
                    trafficker_id=adapter_config.gam_trafficker_id or None,
                    dry_run=False,
                    audit_logger=None,
                    tenant_id=tenant_id,
                )

                if sync_type == "full":
                    result = adapter.sync_full(session, force=force, custom_targeting_limit=custom_targeting_limit)
                elif sync_type in ("inventory", "targeting"):
                    result = adapter.sync_inventory(session, force=force, custom_targeting_limit=custom_targeting_limit)
                elif sync_type == "selective":
                    result = adapter.sync_selective(
                        session,
                        sync_types=sync_types,
                        custom_targeting_limit=custom_targeting_limit,
                        audience_segment_limit=audience_segment_limit,
                    )
                else:
                    raise AdCPValidationError(f"Unsupported sync type: {sync_type}")

                return result

            except (AdCPValidationError, AdCPNotFoundError):
                raise
            except Exception as e:
                logger.error(f"Sync failed for tenant {tenant_id}: {e}", exc_info=True)
                try:
                    sync_job.status = "failed"
                    sync_job.completed_at = datetime.now(UTC)
                    sync_job.error_message = str(e)
                    session.commit()
                except Exception:
                    logger.error("Failed to update sync job status in DB", exc_info=True)
                raise AdCPValidationError(f"Sync failed: {e}")

    def get_sync_status(self, sync_id: str) -> dict[str, Any]:
        with get_db_session() as session:
            job = session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()
            if not job:
                raise AdCPNotFoundError(f"Sync job '{sync_id}' not found")

            result: dict[str, Any] = {
                "sync_id": job.sync_id,
                "tenant_id": job.tenant_id,
                "adapter_type": job.adapter_type,
                "sync_type": job.sync_type,
                "status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "triggered_by": job.triggered_by,
            }

            if job.completed_at:
                result["completed_at"] = job.completed_at.isoformat()
                if job.started_at:
                    duration = job.completed_at - job.started_at
                    result["duration_seconds"] = duration.total_seconds()

            if job.summary:
                result["summary"] = json.loads(job.summary)

            if job.error_message:
                result["error"] = job.error_message

            return result

    def get_sync_history(
        self, tenant_id: str, limit: int = 10, offset: int = 0, status: str | None = None
    ) -> dict[str, Any]:
        with get_db_session() as session:
            stmt = select(SyncJob).filter_by(tenant_id=tenant_id)
            if status:
                stmt = stmt.filter_by(status=status)

            total = session.scalar(select(func.count()).select_from(stmt.subquery()))

            jobs = session.scalars(stmt.order_by(SyncJob.started_at.desc()).limit(limit).offset(offset)).all()

            results = []
            for job in jobs:
                item: dict[str, Any] = {
                    "sync_id": job.sync_id,
                    "sync_type": job.sync_type,
                    "status": job.status,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "triggered_by": job.triggered_by,
                }
                if job.completed_at:
                    item["completed_at"] = job.completed_at.isoformat()
                    if job.started_at:
                        item["duration_seconds"] = (job.completed_at - job.started_at).total_seconds()
                if job.summary:
                    item["summary"] = json.loads(job.summary)
                if job.error_message:
                    item["error"] = job.error_message
                results.append(item)

            return {"total": total, "limit": limit, "offset": offset, "results": results}

    def get_sync_stats(self) -> dict[str, Any]:
        with get_db_session() as session:
            since = datetime.now(UTC).replace(hour=0, minute=0, second=0)

            status_counts: dict[str, int] = {}
            for status in ("pending", "running", "completed", "failed"):
                count = session.scalar(
                    select(func.count())
                    .select_from(SyncJob)
                    .where(SyncJob.status == status, SyncJob.started_at >= since)
                )
                status_counts[status] = count or 0

            # Recent failures
            failures_stmt = (
                select(SyncJob)
                .where(SyncJob.status == "failed", SyncJob.started_at >= since)
                .order_by(SyncJob.started_at.desc())
                .limit(5)
            )
            recent_failures = [
                {
                    "sync_id": j.sync_id,
                    "tenant_id": j.tenant_id,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "error": j.error_message,
                }
                for j in session.scalars(failures_stmt).all()
            ]

            # Stale tenants
            gam_tenants = session.scalars(select(Tenant).filter_by(ad_server="google_ad_manager")).all()
            stale_tenants = []
            for tenant in gam_tenants:
                last = session.scalars(
                    select(SyncJob)
                    .where(SyncJob.tenant_id == tenant.tenant_id, SyncJob.status == "completed")
                    .order_by(SyncJob.completed_at.desc())
                ).first()
                needs_sync = not last or (last.completed_at and (datetime.now(UTC) - last.completed_at).days > 1)
                if needs_sync:
                    stale_tenants.append(
                        {
                            "tenant_id": tenant.tenant_id,
                            "tenant_name": tenant.name,
                            "last_sync": last.completed_at.isoformat() if (last and last.completed_at) else None,
                        }
                    )

            return {
                "status_counts": status_counts,
                "recent_failures": recent_failures,
                "stale_tenants": stale_tenants,
                "since": since.isoformat(),
            }

    def list_gam_tenants(self) -> dict[str, Any]:
        with get_db_session() as session:
            tenants = session.scalars(select(Tenant).filter_by(ad_server="google_ad_manager")).all()

            results = []
            for tenant in tenants:
                adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
                last_sync = session.scalars(
                    select(SyncJob)
                    .where(SyncJob.tenant_id == tenant.tenant_id, SyncJob.status == "completed")
                    .order_by(SyncJob.completed_at.desc())
                ).first()

                info: dict[str, Any] = {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "has_adapter_config": adapter is not None,
                    "last_sync": (
                        {
                            "sync_id": last_sync.sync_id,
                            "completed_at": last_sync.completed_at.isoformat() if last_sync.completed_at else None,
                            "summary": json.loads(last_sync.summary) if last_sync.summary else None,
                        }
                        if last_sync
                        else None
                    ),
                }
                if adapter:
                    info["gam_network_code"] = adapter.gam_network_code
                results.append(info)

            return {"total": len(results), "tenants": results}

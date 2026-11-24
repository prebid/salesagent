"""Sync job repository: tenant-scoped writes to the ``sync_jobs`` rows background work reports through."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import SyncJob
from src.core.database.repositories.base import Repository
from src.core.operator_errors import SyncJobNotFound


class SyncJobRepository(Repository):
    """Tenant-scoped access to sync jobs (inventory syncs, order approvals).

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    NOT_FOUND = SyncJobNotFound

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def create(
        self,
        sync_id: str,
        sync_type: str,
        *,
        adapter_type: str,
        status: str,
        triggered_by: str,
        triggered_by_id: str,
        progress: dict[str, Any] | None = None,
    ) -> SyncJob:
        """Add the job *sync_id* of this tenant, started now, in *status*."""
        job = SyncJob(
            sync_id=sync_id,
            tenant_id=self._tenant_id,
            adapter_type=adapter_type,
            sync_type=sync_type,
            status=status,
            started_at=datetime.now(UTC),
            triggered_by=triggered_by,
            triggered_by_id=triggered_by_id,
            progress=progress,
        )
        self._session.add(job)
        return job

    def get(self, sync_id: str) -> SyncJob | None:
        """The job *sync_id* of this tenant, or ``None``."""
        return self._session.scalars(select(SyncJob).filter_by(tenant_id=self._tenant_id, sync_id=sync_id)).first()

    def find_running(self, sync_type: str) -> SyncJob | None:
        """This tenant's running job of *sync_type*, or ``None``."""
        return self._session.scalars(
            select(SyncJob).filter_by(tenant_id=self._tenant_id, sync_type=sync_type, status="running")
        ).first()

    def find_recent(self, sync_type: str, since: datetime) -> SyncJob | None:
        """A job of *sync_type* started at or after *since* that is running or completed, or ``None``."""
        return self._session.scalars(
            select(SyncJob).where(
                SyncJob.tenant_id == self._tenant_id,
                SyncJob.sync_type == sync_type,
                SyncJob.status.in_(["running", "completed"]),
                SyncJob.started_at >= since,
            )
        ).first()

    def mark_running(self, sync_id: str) -> SyncJob | None:
        """Mark the job *sync_id* running; ``None`` if there is none."""
        job = self.get(sync_id)
        if job is not None:
            job.status = "running"
        return job

    def mark_completed(self, sync_id: str, summary: dict[str, Any]) -> SyncJob | None:
        """Mark the job *sync_id* completed now, with *summary*; ``None`` if there is none."""
        job = self.get(sync_id)
        if job is not None:
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.summary = json.dumps(summary)
        return job

    def record_failure(self, sync_id: str, operator_message: str) -> SyncJob | None:
        """Mark the job *sync_id* failed with *operator_message* (:meth:`SyncJob.mark_failed`); ``None`` if there is none."""
        job = self.get(sync_id)
        if job is not None:
            job.mark_failed(operator_message)
        return job

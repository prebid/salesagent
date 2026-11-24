"""Sync job repository: tenant-scoped writes to the ``sync_jobs`` rows background work reports through."""

from __future__ import annotations

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

    def get(self, sync_id: str) -> SyncJob | None:
        """The job *sync_id* of this tenant, or ``None``."""
        return self._session.scalars(select(SyncJob).filter_by(tenant_id=self._tenant_id, sync_id=sync_id)).first()

    def record_failure(self, sync_id: str, operator_message: str) -> SyncJob | None:
        """Mark the job *sync_id* failed with *operator_message* (:meth:`SyncJob.mark_failed`); ``None`` if there is none."""
        job = self.get(sync_id)
        if job is not None:
            job.mark_failed(operator_message)
        return job

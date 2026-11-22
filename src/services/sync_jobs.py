"""Recording that a sync job failed, for every place a job's work runs.

The GAM sync manager, the admin sync API, the background inventory sync and the order
approval poller each run a ``SyncJob``'s work and report its failure on the job, where
the admin UI's status routes read it. They all write it through
``SyncJobRepository.record_failure``: a sync's own work through :func:`recording_failure`, on
its session, and a standalone caller through :func:`record_sync_failure`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from src.core.database.models import SyncJob
from src.core.operator_errors import RecordedFailure, safe_error_message


class SyncFailed(RecordedFailure):
    """A sync job failed and its failure is recorded; raised in place of the cause, which it chains.

    ``operator_message`` is the fixed text stored on the job, ending with the log reference
    the cause was logged under. A caller that records the same failure on another job, or
    answers it, reuses this text, so one failure keeps one log record and one reference.
    """

    def __init__(self, sync_id: str, operator_message: str):
        super().__init__(operator_message, f"Sync {sync_id} failed: {operator_message}")
        self.sync_id = sync_id

    def response_fields(self) -> dict[str, object]:
        return {"sync_id": self.sync_id, "status": "failed"}


def record_sync_failure(tenant_id: str, sync_id: str, operator_message: str) -> dict[str, Any]:
    """Mark the job *sync_id* failed with *operator_message*, in a unit of work of its own, for a caller that holds none.

    *operator_message* is fixed text (:meth:`SyncJob.mark_failed`). Returns the job's
    progress as it stood, for a caller that reports it on (the approval webhook); empty
    when the job has none or does not exist.
    """
    # Imported here: the GAM sync manager imports this module while the repositories
    # package is still importing the adapters (repositories.account -> core.helpers).
    from src.core.database.repositories.uow import SyncJobUoW

    with SyncJobUoW(tenant_id) as uow:
        assert uow.sync_jobs is not None
        job = uow.sync_jobs.record_failure(sync_id, operator_message)
        return dict(job.progress or {}) if job is not None else {}


@contextmanager
def recording_failure(db_session: Session, sync_job: SyncJob, context: str) -> Iterator[None]:
    """Run a sync's work on *db_session*; if it raises, mark ``sync_job`` failed and raise :class:`SyncFailed`.

    The cause is logged once, by ``safe_error_message(cause, context)``, unless it is a
    nested sync's :class:`SyncFailed`, whose text (and reference) the job takes over.
    *db_session* is rolled back first: after a failed flush it holds a transaction the
    database has aborted, which can neither commit the failure nor release the row locks
    the failure's own write would wait on. The rollback ends that transaction, so the
    failure is then written and committed on the same session, through the repository
    :func:`record_sync_failure` writes through too.
    """
    # Imported here for the reason record_sync_failure gives.
    from src.core.database.repositories.sync_job import SyncJobRepository

    tenant_id, sync_id = sync_job.tenant_id, sync_job.sync_id
    try:
        yield
    except Exception as e:  # noqa: BLE001 - every failure of a sync is recorded on its job, then re-raised
        operator_message = e.operator_message if isinstance(e, SyncFailed) else safe_error_message(e, context)
        db_session.rollback()
        SyncJobRepository(db_session, tenant_id).record_failure(sync_id, operator_message)
        db_session.commit()
        raise SyncFailed(sync_id, operator_message) from e

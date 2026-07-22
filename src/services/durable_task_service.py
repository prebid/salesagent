"""Durable task-outcome resolution for async media-buy tasks.

Transport-neutral read of the persisted workflow step behind an async task id
(``tasks/get``-style polls). The transport layer (A2A handler) resolves the
caller's identity and frames the outcome into its own Task/Artifact types; this
service owns the session and the step lookup, so the handler performs no DB
access itself. #1544.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from src.core.database.database_session import get_db_session
from src.core.database.repositories.workflow import WorkflowRepository

# The one persisted step status whose stored response_data is a two-layer error
# envelope (completed stores the success payload; rejected stores the typed
# rejection error — both are result-shaped, not error-artifact-shaped).
_ERROR_STEP_STATUS = "failed"


class DurableTaskOutcome(NamedTuple):
    """Transport-neutral durable view of an async media-buy task.

    ``step_status`` is the persisted workflow-step status (the transport maps it
    to its own task-state enum); ``is_error`` selects error-vs-result artifact
    framing; ``response_data`` is the stored payload (a two-layer error envelope
    for a failed step, the result payload otherwise).
    """

    step_status: str
    context_id: str
    is_error: bool
    response_data: dict[str, Any] | None


def resolve_durable_task_outcome(tenant_id: str, task_id: str) -> DurableTaskOutcome | None:
    """Resolve the persisted outcome for ``task_id`` within ``tenant_id``.

    Returns ``None`` when no workflow step stored this transport task id (the
    id is unknown, or belongs to another tenant — the tenant-scoped repository
    lookup cannot see it).
    """
    with get_db_session() as session:
        step = WorkflowRepository(session, tenant_id).get_by_external_task_id(task_id)
        if step is None:
            return None
        return DurableTaskOutcome(
            step_status=step.status,
            context_id=step.context_id,
            is_error=step.status == _ERROR_STEP_STATUS,
            response_data=step.response_data,
        )

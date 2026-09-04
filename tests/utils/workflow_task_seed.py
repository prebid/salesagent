"""Shared seed for principal-owned durable workflow tasks (get_task / complete_task).

Used by integration principal-scope tests and UC-027 sibling-isolation BDD so the
owner+step contract cannot drift between layers (#1812 review).
"""

from __future__ import annotations

from typing import Any

from src.core.context_manager import ContextManager
from src.core.database.models import WorkflowStep


def create_principal_owned_workflow_step(
    *,
    tenant_id: str,
    principal_id: str,
    status: str = "completed",
    tool_name: str = "create_media_buy",
    request_data: dict[str, Any] | None = None,
) -> WorkflowStep:
    """Create a Context + WorkflowStep owned by ``principal_id`` in ``tenant_id``.

    ``status="requires_approval"`` uses ``step_type="approval"``; other statuses
    use ``tool_call``. Request payload defaults to ``{"budget": 1000}`` — the
    durable-task shape both test layers previously hand-rolled.
    """
    cm = ContextManager()
    context = cm.create_context(tenant_id=tenant_id, principal_id=principal_id)
    return cm.create_workflow_step(
        context_id=context.context_id,
        step_type="approval" if status == "requires_approval" else "tool_call",
        owner="principal",
        status=status,
        tool_name=tool_name,
        request_data=request_data if request_data is not None else {"budget": 1000},
    )

"""Task management MCP tools (list_tasks, get_task, complete_task).

Human-in-the-loop task queue for workflow steps that require approval
or manual completion. These tools let AI agents query and complete
pending workflow tasks.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastmcp.server.context import Context
from sqlalchemy import func, select

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, WorkflowStep
from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)


async def list_tasks(
    status: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
    context: Context | None = None,
    identity: ResolvedIdentity | None = None,
) -> dict[str, Any]:
    """List workflow tasks with filtering options.

    Args:
        status: Filter by task status ("pending", "in_progress", "completed", "failed", "requires_approval")
        object_type: Filter by object type ("media_buy", "creative", "product")
        object_id: Filter by specific object ID
        limit: Maximum number of tasks to return (default: 20)
        offset: Number of tasks to skip (default: 0)
        context: MCP context (automatically provided)
        identity: Pre-resolved identity (preferred over context)

    Returns:
        Dict containing tasks list and pagination info
    """
    if identity is None and context is not None:
        identity = await context.get_state("identity")

    if not identity or not identity.tenant:
        raise AdCPAuthenticationError("No tenant context available. Check x-adcp-auth token and host headers.")

    principal_id = identity.principal_id
    tenant = identity.tenant

    with get_db_session() as session:
        stmt = select(WorkflowStep).join(DBContext).filter(DBContext.tenant_id == tenant["tenant_id"])

        if status:
            stmt = stmt.where(WorkflowStep.status == status)

        if object_type and object_id:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type, ObjectWorkflowMapping.object_id == object_id
            )
        elif object_type:
            stmt = stmt.join(ObjectWorkflowMapping).where(ObjectWorkflowMapping.object_type == object_type)

        total = session.scalar(select(func.count()).select_from(stmt.subquery()))

        tasks = session.scalars(stmt.order_by(WorkflowStep.created_at.desc()).offset(offset).limit(limit)).all()

        formatted_tasks = []
        for task in tasks:
            mapping_stmt = select(ObjectWorkflowMapping).filter_by(step_id=task.step_id)
            mappings = session.scalars(mapping_stmt).all()

            formatted_task = {
                "task_id": task.step_id,
                "status": task.status,
                "type": task.step_type,
                "tool_name": task.tool_name,
                "owner": task.owner,
                "created_at": (
                    task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else str(task.created_at)
                ),
                "updated_at": None,
                "context_id": task.context_id,
                "associated_objects": [
                    {"type": m.object_type, "id": m.object_id, "action": m.action} for m in mappings
                ],
            }

            if task.status == "failed" and task.error_message:
                formatted_task["error_message"] = task.error_message

            if task.request_data:
                if isinstance(task.request_data, dict):
                    formatted_task["summary"] = {  # type: ignore[assignment]
                        "operation": task.request_data.get("operation"),
                        "media_buy_id": task.request_data.get("media_buy_id"),
                        "po_number": (
                            task.request_data.get("request", {}).get("po_number")
                            if task.request_data.get("request")
                            else None
                        ),
                    }

            formatted_tasks.append(formatted_task)

        return {
            "tasks": formatted_tasks,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total if total is not None else False,
        }


async def get_task(
    task_id: str, context: Context | None = None, identity: ResolvedIdentity | None = None
) -> dict[str, Any]:
    """Get detailed information about a specific task.

    Args:
        task_id: The unique task/workflow step ID
        context: MCP context (automatically provided)
        identity: Pre-resolved identity (preferred over context)

    Returns:
        Dict containing complete task details
    """
    if identity is None and context is not None:
        identity = await context.get_state("identity")

    if not identity or not identity.tenant:
        raise AdCPAuthenticationError("No tenant context available. Check x-adcp-auth token and host headers.")

    tenant = identity.tenant

    with get_db_session() as session:
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(WorkflowStep.step_id == task_id, DBContext.tenant_id == tenant["tenant_id"])
        )
        task = session.scalars(stmt).first()

        if not task:
            raise ValueError(f"Task {task_id} not found")

        mapping_stmt2 = select(ObjectWorkflowMapping).filter_by(step_id=task_id)
        mappings = session.scalars(mapping_stmt2).all()

        task_detail = {
            "task_id": task.step_id,
            "context_id": task.context_id,
            "status": task.status,
            "type": task.step_type,
            "tool_name": task.tool_name,
            "owner": task.owner,
            "created_at": (
                task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else str(task.created_at)
            ),
            "updated_at": None,
            "request_data": task.request_data,
            "response_data": task.response_data,
            "error_message": task.error_message,
            "associated_objects": [
                {
                    "type": m.object_type,
                    "id": m.object_id,
                    "action": m.action,
                    "created_at": (
                        m.created_at.isoformat() if hasattr(m.created_at, "isoformat") else str(m.created_at)
                    ),
                }
                for m in mappings
            ],
        }

        return task_detail


async def complete_task(
    task_id: str,
    status: str = "completed",
    response_data: dict[str, Any] | None = None,
    error_message: str | None = None,
    context: Context | None = None,
    identity: ResolvedIdentity | None = None,
) -> dict[str, Any]:
    """Complete a pending task (simulates human approval or async completion).

    Args:
        task_id: The unique task/workflow step ID
        status: New status ("completed" or "failed")
        response_data: Optional response data for completed tasks
        error_message: Error message if status is "failed"
        context: MCP context (automatically provided)
        identity: Pre-resolved identity (preferred over context)

    Returns:
        Dict containing task completion status
    """
    if identity is None and context is not None:
        identity = await context.get_state("identity")

    if not identity or not identity.tenant:
        raise AdCPAuthenticationError("No tenant context available. Check x-adcp-auth token and host headers.")

    principal_id = identity.principal_id
    tenant = identity.tenant

    if status not in ["completed", "failed"]:
        raise ValueError(f"Invalid status '{status}'. Must be 'completed' or 'failed'")

    with get_db_session() as session:
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(WorkflowStep.step_id == task_id, DBContext.tenant_id == tenant["tenant_id"])
        )
        task = session.scalars(stmt).first()

        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status not in ["pending", "in_progress", "requires_approval"]:
            raise ValueError(f"Task {task_id} is already {task.status} and cannot be completed")

        task.status = status
        completed_time = datetime.now(UTC)
        task.completed_at = completed_time

        if status == "completed":
            task.response_data = response_data or {"manually_completed": True, "completed_by": principal_id}
            task.error_message = None
        else:
            task.error_message = error_message or "Task marked as failed manually"
            if response_data:
                task.response_data = response_data

        session.commit()

        audit_logger = get_audit_logger("task_management", tenant["tenant_id"])
        audit_logger.log_operation(
            operation="complete_task",
            principal_name="Manual Completion",
            principal_id=principal_id or "unknown",
            adapter_id="system",
            success=True,
            details={
                "task_id": task_id,
                "new_status": status,
                "original_status": "pending",
                "task_type": task.step_type,
            },
        )

        return {
            "task_id": task_id,
            "status": status,
            "message": f"Task {task_id} marked as {status}",
            "completed_at": completed_time.isoformat(),
            "completed_by": principal_id,
        }

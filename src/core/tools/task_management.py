"""Task management MCP tools (list_tasks, get_task, complete_task).

Human-in-the-loop task queue for workflow steps that require approval
or manual completion. These tools let AI agents query and complete
pending workflow tasks.

This module follows the MCP/A2A shared implementation pattern from CLAUDE.md.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastmcp.server.context import Context

from src.core.audit_logger import get_audit_logger
from src.core.auth import require_identity, require_principal_id, require_tenant
from src.core.database.repositories.uow import WorkflowUoW
from src.core.exceptions import (
    ERROR_MESSAGE_TYPE_MESSAGE,
    VALIDATION_ERROR_SUGGESTION,
    AdCPConflictError,
    AdCPValidationError,
)
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)

# Server-owned kwargs — never buyer-supplied on the A2A skill path.
_SERVER_OWNED_TASK_PARAMS = frozenset({"context", "identity"})


def _buyer_param_names(fn: Any) -> frozenset[str]:
    """Buyer-forwardable names from an L2 tool signature (minus server-owned)."""
    names: set[str] = set()
    for name, param in inspect.signature(fn).parameters.items():
        if name in _SERVER_OWNED_TASK_PARAMS:
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        names.add(name)
    return frozenset(names)


def assert_known_task_params(parameters: Mapping[str, Any], *, allowed: frozenset[str]) -> None:
    """Reject unknown buyer keys so A2A/MCP cannot silently drop typos.

    Called from A2A skill handlers (raw skill dict) and from
    ``RequestCompatMiddleware`` for ``get_task`` / ``complete_task`` (MCP
    arguments dict) — FastMCP cannot register ``**kwargs`` tools, so the
    unknown-key gate cannot live inside the tool body. A typo'd ``statas``
    must not default ``status`` to ``completed``.
    """
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise AdCPValidationError(
            f"Unexpected parameter(s): {', '.join(unknown)}",
            suggestion=VALIDATION_ERROR_SUGGESTION,
        )


TASK_ID_REQUIRED_MESSAGE = "task_id is required"
TASK_ID_TYPE_MESSAGE = "task_id must be a string"
# Re-export for task-tool callers / tests (SSOT lives in exceptions).


def require_task_id(task_id: Any) -> str:
    """Validate presence *and* type of ``task_id`` for both durable task tools.

    A2A forwards raw skill parameters, so ``task_id`` can arrive as any JSON
    type (int, bool, list, ...), not just ``str``. A presence-only check
    (``if not task_id``) lets a non-string value reach the SQL comparison,
    where the untyped DB error (e.g. ``UndefinedFunction`` for a numeric
    literal against a varchar column) escapes as ``SERVICE_UNAVAILABLE`` and
    leaks the query. Shared by ``get_task`` / ``complete_task`` so both tools
    reject the same shapes at L2. Wire grading covers truthy non-strings on
    both A2A and MCP via TaskEnv (unit covers the in-process path).

    Absent/empty → ``TASK_ID_REQUIRED_MESSAGE``; present wrong type →
    ``TASK_ID_TYPE_MESSAGE`` (mirrors ``_require_error_message``).
    """
    if task_id is None or (isinstance(task_id, str) and not task_id):
        raise AdCPValidationError(
            TASK_ID_REQUIRED_MESSAGE,
            field="task_id",
            suggestion=VALIDATION_ERROR_SUGGESTION,
        )
    if not isinstance(task_id, str):
        raise AdCPValidationError(
            TASK_ID_TYPE_MESSAGE,
            field="task_id",
            suggestion=VALIDATION_ERROR_SUGGESTION,
        )
    return task_id


def _require_error_message(error_message: Any) -> str | None:
    """Reject non-string ``error_message`` before it reaches the ORM/DB driver."""
    if error_message is None:
        return None
    if not isinstance(error_message, str):
        raise AdCPValidationError(
            ERROR_MESSAGE_TYPE_MESSAGE,
            field="error_message",
            suggestion=VALIDATION_ERROR_SUGGESTION,
        )
    return error_message


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

    identity = require_identity(identity)
    tenant = require_tenant(identity)
    require_principal_id(identity)  # F-03: an authenticated (non-anonymous) principal is required

    with WorkflowUoW(tenant["tenant_id"]) as uow:
        assert uow.workflows is not None

        total = uow.workflows.count_by_tenant(
            status=status,
            object_type=object_type,
            object_id=object_id,
        )

        tasks = uow.workflows.list_by_tenant(
            status=status,
            object_type=object_type,
            object_id=object_id,
            offset=offset,
            limit=limit,
        )

        step_ids = [task.step_id for task in tasks]
        all_mappings = uow.workflows.get_mappings_for_steps(step_ids)

        formatted_tasks = []
        for task in tasks:
            mappings = all_mappings.get(task.step_id, [])

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
    task_id: Any,
    context: Context | None = None,
    identity: ResolvedIdentity | None = None,
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

    identity = require_identity(identity)
    tenant = require_tenant(identity)
    principal_id = require_principal_id(identity)  # F-03: authenticated principal + ownership key
    task_id = require_task_id(task_id)

    with WorkflowUoW(tenant["tenant_id"]) as uow:
        assert uow.workflows is not None

        task = uow.workflows.get_by_step_id_or_raise(task_id, principal_id=principal_id)

        mappings = uow.workflows.get_mappings_for_step(task_id)

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
    task_id: Any,
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

    identity = require_identity(identity)
    tenant = require_tenant(identity)
    principal_id = require_principal_id(identity)  # F-03: an authenticated principal is required
    task_id = require_task_id(task_id)
    error_message = _require_error_message(error_message)

    if status not in ["completed", "failed"]:
        raise AdCPValidationError(
            f"Invalid status '{status}'. Must be 'completed' or 'failed'",
            field="status",
            suggestion=VALIDATION_ERROR_SUGGESTION,
        )

    with WorkflowUoW(tenant["tenant_id"]) as uow:
        assert uow.workflows is not None

        task = uow.workflows.get_by_step_id_or_raise(task_id, principal_id=principal_id)

        if task.status not in ["pending", "in_progress", "requires_approval"]:
            raise AdCPConflictError(f"Task {task_id} is already {task.status} and cannot be completed")

        completed_time = datetime.now(UTC)

        if status == "completed":
            uow.workflows.update_status(
                task_id,
                status=status,
                completed_at=completed_time,
                response_data=response_data or {"manually_completed": True, "completed_by": principal_id},
                principal_id=principal_id,
            )
        else:
            uow.workflows.update_status(
                task_id,
                status=status,
                completed_at=completed_time,
                error_message=error_message or "Task marked as failed manually",
                response_data=response_data,
                principal_id=principal_id,
            )

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


# Derived once at import — A2A handlers + unit tests share this set (A6).
GET_TASK_BUYER_PARAMS = _buyer_param_names(get_task)
COMPLETE_TASK_BUYER_PARAMS = _buyer_param_names(complete_task)

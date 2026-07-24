"""Identity-scope gate for A2A in-memory tasks/get and tasks/cancel (#1702).

A bare ``self.tasks.get(task_id)`` served (or canceled) any caller's request
once they knew the id. These tests pin auth-first ownership against
``_task_owners`` and prove wrong-principal / unauthenticated callers get the
same ``TaskNotFoundError`` as an unknown id (no existence oracle).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Task,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
)

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.factories import PrincipalFactory

_TENANT = "tenant_a"
_OWNER = "principal_owner"
_SIBLING = "principal_sibling"
_TASK_ID = "task_owned_abc"


def _owned_handler() -> AdCPRequestHandler:
    handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
    done = Task(id=_TASK_ID, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
    handler.tasks = {_TASK_ID: done}
    handler._task_owners = {_TASK_ID: (_TENANT, _OWNER)}
    handler._task_push_configs = {}
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(GetTaskRequest, "on_get_task"), (CancelTaskRequest, "on_cancel_task")],
)
async def test_owner_can_access_owned_in_memory_task(request_cls, method_name):
    """The recorded owner authenticates and is served / can cancel."""
    handler = _owned_handler()
    identity = PrincipalFactory.make_identity(principal_id=_OWNER, tenant_id=_TENANT, protocol="a2a")

    with (
        patch.object(handler, "_get_auth_token", return_value="tok") as mock_auth,
        patch.object(handler, "_resolve_a2a_identity", return_value=identity),
    ):
        task = await getattr(handler, method_name)(request_cls(id=_TASK_ID), context=None)

    mock_auth.assert_called_once_with(None)
    assert task.id == _TASK_ID
    if method_name == "on_cancel_task":
        assert task.status.state == TaskState.TASK_STATE_CANCELED
    else:
        assert task.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(GetTaskRequest, "on_get_task"), (CancelTaskRequest, "on_cancel_task")],
)
async def test_sibling_principal_denied_same_as_unknown(request_cls, method_name):
    """Same-tenant sibling must not read or cancel — identical to unknown id."""
    handler = _owned_handler()
    sibling = PrincipalFactory.make_identity(principal_id=_SIBLING, tenant_id=_TENANT, protocol="a2a")

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=sibling),
    ):
        with pytest.raises(TaskNotFoundError) as exc:
            await getattr(handler, method_name)(request_cls(id=_TASK_ID), context=None)

    assert exc.value.data == {"task_id": _TASK_ID}
    # Sibling denial must not mutate cancel state.
    assert handler.tasks[_TASK_ID].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_cls, method_name",
    [(GetTaskRequest, "on_get_task"), (CancelTaskRequest, "on_cancel_task")],
)
async def test_unauthenticated_poller_denied_same_as_unknown(request_cls, method_name):
    """Auth failure collapses to TaskNotFoundError — no anonymous content oracle."""
    handler = _owned_handler()

    with (
        patch.object(handler, "_get_auth_token", return_value=None),
        patch.object(
            handler,
            "_resolve_a2a_identity",
            side_effect=Exception("Missing authentication token"),
        ),
    ):
        with pytest.raises(TaskNotFoundError) as exc:
            await getattr(handler, method_name)(request_cls(id=_TASK_ID), context=None)

    assert exc.value.data == {"task_id": _TASK_ID}
    assert handler.tasks[_TASK_ID].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_ownership_gate_mutation_proof():
    """If ownership equality is dropped, sibling get leaks; with it, denied.

    Plan mutation proof: temporarily serve bare ``self.tasks.get`` (pre-#1702),
    confirm the sibling leak, then restore the owned helper and confirm deny.
    """
    handler = _owned_handler()
    sibling = PrincipalFactory.make_identity(principal_id=_SIBLING, tenant_id=_TENANT, protocol="a2a")

    def bare_tasks_get(task_id: str, context):  # noqa: ARG001 — mirrors pre-fix signature
        task = handler.tasks.get(task_id)
        assert task is not None
        return task

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=sibling),
        patch.object(handler, "_get_owned_in_memory_task_or_raise", side_effect=bare_tasks_get),
    ):
        leaked = await handler.on_get_task(GetTaskRequest(id=_TASK_ID), context=None)
    assert leaked is handler.tasks[_TASK_ID]

    with (
        patch.object(handler, "_get_auth_token", return_value="tok"),
        patch.object(handler, "_resolve_a2a_identity", return_value=sibling),
    ):
        with pytest.raises(TaskNotFoundError):
            await handler.on_get_task(GetTaskRequest(id=_TASK_ID), context=None)

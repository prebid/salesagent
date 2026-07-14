"""B6 (#1544): durable A2A tasks/get correlation via the persisted outer task id.

The A2A boundary persists its outer ``task_*`` id on the create step's
``request_data.external_task_id``. These tests verify the two halves of the durable
path against a real DB:

  * ``WorkflowRepository.get_by_external_task_id`` resolves the buyer's id → step,
    tenant-scoped (a different tenant cannot resolve it).
  * ``AdCPRequestHandler.on_get_task`` rebuilds a terminal ``Task`` (with the stored
    ``response_data`` artifact) from that step when the id is NOT in the in-memory map
    — i.e. after the out-of-band approval / a restart.
"""

import asyncio
from unittest.mock import patch

import pytest
from a2a.types import GetTaskRequest, TaskState

from src.core.context_manager import ContextManager
from src.core.database.database_session import get_db_session
from src.core.database.repositories.workflow import WorkflowRepository
from tests.factories import PrincipalFactory

_EXTERNAL_TASK_ID = "task_buyer_abc123"


def _make_completed_step(context_manager: ContextManager, tenant_id: str, principal_id: str) -> str:
    """Create a completed create-media-buy workflow step carrying an external_task_id."""
    context = context_manager.create_context(tenant_id=tenant_id, principal_id=principal_id)
    step = context_manager.create_workflow_step(
        context_id=context.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="completed",
        tool_name="create_media_buy",
        request_data={"media_buy_id": "mb_1"},
        response_data={"media_buy_id": "mb_1", "revision": 2},
        # Merged into request_data by create_workflow_step — same path production uses.
        request_metadata={"protocol": "a2a", "external_task_id": _EXTERNAL_TASK_ID},
    )
    return step.step_id


@pytest.mark.requires_db
class TestA2ATaskCorrelation:
    @pytest.fixture
    def context_manager(self):
        return ContextManager()

    def test_get_by_external_task_id_resolves_within_tenant(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """The stored outer task id resolves to its step within the owning tenant."""
        tenant_id = sample_tenant["tenant_id"]
        step_id = _make_completed_step(context_manager, tenant_id, sample_principal["principal_id"])

        with get_db_session() as session:
            step = WorkflowRepository(session, tenant_id).get_by_external_task_id(_EXTERNAL_TASK_ID)
            assert step is not None
            assert step.step_id == step_id
            assert step.status == "completed"

    def test_get_by_external_task_id_is_tenant_scoped(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A different tenant cannot resolve another tenant's task id."""
        tenant_id = sample_tenant["tenant_id"]
        _make_completed_step(context_manager, tenant_id, sample_principal["principal_id"])

        with get_db_session() as session:
            assert WorkflowRepository(session, "other_tenant").get_by_external_task_id(_EXTERNAL_TASK_ID) is None

    def test_on_get_task_rebuilds_terminal_task_from_step(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """tasks/get of an id NOT in memory rebuilds a COMPLETED Task with the stored artifact."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        tenant_id = sample_tenant["tenant_id"]
        _make_completed_step(context_manager, tenant_id, sample_principal["principal_id"])

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        handler.tasks = {}  # in-memory miss → forces the durable DB fallback
        identity = PrincipalFactory.make_identity(
            principal_id=sample_principal["principal_id"], tenant_id=tenant_id, protocol="a2a"
        )

        with (
            patch.object(handler, "_get_auth_token", return_value="tok"),
            patch.object(handler, "_resolve_a2a_identity", return_value=identity),
        ):
            task = asyncio.run(handler.on_get_task(GetTaskRequest(id=_EXTERNAL_TASK_ID), context=None))

        assert task is not None
        assert task.id == _EXTERNAL_TASK_ID
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert len(task.artifacts) == 1
        assert task.artifacts[0].name == "media_buy_result"

    def test_on_get_task_prefers_in_memory_task(self, integration_db, sample_tenant, sample_principal, context_manager):
        """An id present in the in-memory map is served directly, without a DB lookup."""
        from a2a.types import Task, TaskStatus

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        live = Task(id="task_live", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        handler.tasks = {"task_live": live}

        with patch.object(handler, "_get_auth_token") as mock_auth:
            task = asyncio.run(handler.on_get_task(GetTaskRequest(id="task_live"), context=None))

        assert task is live
        mock_auth.assert_not_called()  # in-memory hit short-circuits before any auth/DB work

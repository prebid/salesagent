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
from src.core.database.repositories import WorkflowUoW
from tests.factories import PrincipalFactory
from tests.integration.conftest import make_create_media_buy_step

_EXTERNAL_TASK_ID = "task_buyer_abc123"


def _make_step(
    context_manager: ContextManager,
    tenant_id: str,
    principal_id: str,
    *,
    status: str = "completed",
    external_task_id: str = _EXTERNAL_TASK_ID,
    response_data: dict | None = None,
) -> str:
    """Create a create-media-buy workflow step carrying an external_task_id (returns step_id)."""
    step = make_create_media_buy_step(
        context_manager,
        tenant_id,
        principal_id,
        status=status,
        external_task_id=external_task_id,
        response_data=response_data if response_data is not None else {"media_buy_id": "mb_1", "revision": 2},
    )
    return step.step_id


def _make_completed_step(context_manager: ContextManager, tenant_id: str, principal_id: str) -> str:
    return _make_step(context_manager, tenant_id, principal_id, status="completed")


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

        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.get_by_external_task_id(_EXTERNAL_TASK_ID)
            assert step is not None
            assert step.step_id == step_id
            assert step.status == "completed"

    def test_get_by_external_task_id_is_tenant_scoped(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A different tenant cannot resolve another tenant's task id."""
        tenant_id = sample_tenant["tenant_id"]
        _make_completed_step(context_manager, tenant_id, sample_principal["principal_id"])

        with WorkflowUoW("other_tenant") as uow:
            assert uow.workflows is not None
            assert uow.workflows.get_by_external_task_id(_EXTERNAL_TASK_ID) is None

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

    def test_on_get_task_terminal_in_memory_task_short_circuits(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """A TERMINAL in-memory task is authoritative — served directly, without a DB lookup."""
        from a2a.types import Task, TaskStatus

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        done = Task(id="task_done", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
        handler.tasks = {"task_done": done}

        with patch.object(handler, "_get_auth_token") as mock_auth:
            task = asyncio.run(handler.on_get_task(GetTaskRequest(id="task_done"), context=None))

        assert task is done
        mock_auth.assert_not_called()  # terminal in-memory hit short-circuits before any auth/DB work

    def _poll_with_stale_in_memory(self, handler, tenant_id, principal_id, external_task_id):
        """Seed a SUBMITTED in-memory task for external_task_id, then poll on_get_task."""
        from a2a.types import Task, TaskStatus

        handler.tasks = {
            external_task_id: Task(id=external_task_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
        }
        identity = PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, protocol="a2a")
        with (
            patch.object(handler, "_get_auth_token", return_value="tok"),
            patch.object(handler, "_resolve_a2a_identity", return_value=identity),
        ):
            return asyncio.run(handler.on_get_task(GetTaskRequest(id=external_task_id), context=None))

    @pytest.mark.parametrize(
        "step_status, expected_state",
        [
            ("completed", TaskState.TASK_STATE_COMPLETED),
            ("rejected", TaskState.TASK_STATE_REJECTED),
            ("failed", TaskState.TASK_STATE_FAILED),
        ],
    )
    def test_stale_submitted_in_memory_yields_to_persisted_terminal(
        self, integration_db, sample_tenant, sample_principal, context_manager, step_status, expected_state
    ):
        """The reviewer's combined-state case: a stale in-memory SUBMITTED task must NOT mask a
        persisted terminal step — the poll returns the out-of-band decision. #1544 (P1)."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        tenant_id = sample_tenant["tenant_id"]
        _make_step(context_manager, tenant_id, sample_principal["principal_id"], status=step_status)

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        task = self._poll_with_stale_in_memory(handler, tenant_id, sample_principal["principal_id"], _EXTERNAL_TASK_ID)

        assert task is not None
        assert task.status.state == expected_state

    def test_stale_submitted_in_memory_kept_when_step_still_non_terminal(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """When neither the in-memory task NOR the persisted step is terminal, the in-flight
        in-memory SUBMITTED task is returned (still working)."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        tenant_id = sample_tenant["tenant_id"]
        _make_step(context_manager, tenant_id, sample_principal["principal_id"], status="in_progress")

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        task = self._poll_with_stale_in_memory(handler, tenant_id, sample_principal["principal_id"], _EXTERNAL_TASK_ID)

        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED

    def test_approval_adapter_failure_stores_buyer_facing_error_artifact(
        self, integration_db, sample_tenant, sample_principal, context_manager
    ):
        """P1 (#1544): an adapter failure during async approval must store a two-layer
        error envelope on the step, so a durable tasks/get poll returns a FAILED task
        WITH the failure details — not a bare FAILED task with no artifact."""
        from datetime import UTC, datetime

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.admin.services.media_buy_completion import FinalizeOutcome, finalize_media_buy_approval
        from src.core.database.repositories import MediaBuyUoW
        from tests.integration.conftest import make_media_buy

        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        external_task_id = "task_fail_xyz"

        with MediaBuyUoW(tenant_id) as uow:
            uow.media_buys.create(make_media_buy(tenant_id, principal_id, "mb_fail", status="pending_approval"))

        step = make_create_media_buy_step(
            context_manager,
            tenant_id,
            principal_id,
            media_buy_id="mb_fail",
            status="in_progress",
            external_task_id=external_task_id,
        )
        step_data = {
            "step_id": step.step_id,
            "context_id": step.context_id,
            "tool_name": "create_media_buy",
            "request_data": {"protocol": "a2a", "external_task_id": external_task_id},
        }

        with MediaBuyUoW(tenant_id) as uow:
            outcome, err = finalize_media_buy_approval(
                uow.session,
                tenant_id,
                media_buy_id="mb_fail",
                step_id=step.step_id,
                step_data=step_data,
                compute_target=lambda mb: "active",
                run_adapter=lambda: (False, "GAM order creation failed"),
                expected_status="pending_approval",
                approved_by="admin@test",
                approved_at=datetime.now(UTC),
            )

        assert outcome is FinalizeOutcome.ADAPTER_FAILED
        assert err == "GAM order creation failed"

        # The step is failed AND carries a buyer-facing two-layer error envelope.
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            failed = uow.workflows.get_by_step_id(step.step_id)
            assert failed is not None
            assert failed.status == "failed"
            assert failed.response_data is not None
            assert failed.response_data["errors"][0]["message"]  # non-empty failure detail

        # A durable poll returns FAILED *with* the error artifact (named as an error).
        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        handler.tasks = {}
        identity = PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, protocol="a2a")
        with (
            patch.object(handler, "_get_auth_token", return_value="tok"),
            patch.object(handler, "_resolve_a2a_identity", return_value=identity),
        ):
            task = asyncio.run(handler.on_get_task(GetTaskRequest(id=external_task_id), context=None))

        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_FAILED
        assert len(task.artifacts) == 1
        assert task.artifacts[0].name == "media_buy_error"

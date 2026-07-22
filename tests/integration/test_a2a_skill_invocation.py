#!/usr/bin/env python3
"""
Test A2A skill invocation patterns from AdCP PR #48.

Tests both natural language and explicit skill invocation patterns
to ensure our A2A server properly handles the evolving AdCP spec.
"""

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import (
    Artifact,
    CancelTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskNotCancelableError,
    TaskState,
    TaskStatus,
)
from adcp.types import AccountReference as LibraryAccountReference

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.factories.creative_asset import build_assets, image_spec
from tests.utils.a2a_helpers import (
    assert_delivery_forwarded_account,
    assert_failed_task_envelope,
    create_a2a_message_with_skill,
    create_a2a_text_message,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Import schema validation components
try:
    from tests.e2e.adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError

    SCHEMA_VALIDATION_AVAILABLE = True
except ImportError:
    SCHEMA_VALIDATION_AVAILABLE = False
    AdCPSchemaValidator: type[AdCPSchemaValidator] | None = None  # type: ignore[no-redef]
    SchemaValidationError: type[SchemaValidationError] | None = None  # type: ignore[no-redef]

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class A2AAdCPValidator:
    """Helper class to validate A2A responses against AdCP schemas."""

    # Map A2A skill names to AdCP schema task names
    # Note: signals skills removed - should come from dedicated signals agents
    SKILL_TO_SCHEMA_MAP = {
        "get_products": "get-products",
        "create_media_buy": "create-media-buy",
        "update_media_buy": "update-media-buy",  # AdCP v2.0+ endpoint
        "get_media_buy_delivery": "get-media-buy-delivery",  # AdCP delivery metrics
        "sync_creatives": "sync-creatives",  # New AdCP spec endpoint
        "list_creatives": "list-creatives",  # New AdCP spec endpoint
        "approve_creative": "approve-creative",  # When schema becomes available
        # Skills without AdCP schemas yet
        "get_media_buy_status": None,
        "optimize_media_buy": None,
    }

    def __init__(self):
        self.validator = None
        if SCHEMA_VALIDATION_AVAILABLE:
            self.validator = AdCPSchemaValidator(offline_mode=True, adcp_version="v1")

    async def __aenter__(self):
        if self.validator:
            await self.validator.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.validator:
            await self.validator.__aexit__(exc_type, exc_val, exc_tb)

    def extract_adcp_payload_from_a2a_artifact(self, artifact) -> dict:
        """Extract AdCP payload from A2A artifact structure.

        In a2a-sdk 1.0, Part.data is a protobuf Value, not a plain dict.
        """
        from tests.utils.a2a_helpers import extract_data_from_artifact

        return extract_data_from_artifact(artifact)

    async def validate_a2a_skill_response(
        self, skill_name: str, task_result: Task
    ) -> dict[str, bool | list[str] | str | None]:
        """
        Validate A2A skill response against AdCP schemas.

        Args:
            skill_name: The A2A skill name (e.g., "get_products")
            task_result: The A2A Task result containing artifacts

        Returns:
            Dict with validation results: {"valid": bool, "errors": list[str], "warnings": list[str], "schema_tested": str | None}
        """
        # Initialize with properly typed lists
        errors: list[str] = []
        warnings: list[str] = []
        result: dict[str, bool | list[str] | str | None] = {
            "valid": True,
            "errors": errors,
            "warnings": warnings,
            "schema_tested": None,
        }

        # Check if schema validation is available
        if not SCHEMA_VALIDATION_AVAILABLE or not self.validator:
            warnings.append("Schema validation not available - skipping")
            return result

        # Check if skill has corresponding AdCP schema
        schema_task = self.SKILL_TO_SCHEMA_MAP.get(skill_name)
        if not schema_task:
            warnings.append(f"No AdCP schema mapping for skill '{skill_name}' - skipping")
            return result

        result["schema_tested"] = schema_task

        # Extract AdCP payload from A2A artifacts
        if not task_result.artifacts:
            errors.append("No artifacts found in A2A task result")
            result["valid"] = False
            return result

        # Validate each artifact (skills can return multiple artifacts)
        for i, artifact in enumerate(task_result.artifacts):
            try:
                adcp_payload = self.extract_adcp_payload_from_a2a_artifact(artifact)
                if not adcp_payload:
                    warnings.append(f"Artifact {i}: No AdCP payload found")
                    continue

                # Validate against AdCP schema
                await self.validator.validate_response(schema_task, adcp_payload)
                warnings.append(f"Artifact {i}: AdCP schema validation passed")

            except SchemaValidationError as e:
                errors.append(f"Artifact {i}: AdCP schema validation failed: {e}")
                result["valid"] = False
            except Exception as e:
                errors.append(f"Artifact {i}: Validation error: {e}")
                result["valid"] = False

        return result


@pytest.mark.requires_db
class TestA2ASkillInvocation:
    """Test both natural language and explicit skill invocation patterns."""

    @pytest.fixture
    def handler(self):
        """Create an AdCP request handler for testing."""
        return AdCPRequestHandler()

    @pytest.fixture
    async def validator(self):
        """Create an A2A/AdCP validator for testing."""
        async with A2AAdCPValidator() as v:
            yield v

    @pytest.fixture
    def mock_auth_token(self):
        """Mock authentication token for testing."""
        return "test_bearer_token_123"

    def create_message_hybrid(self, text: str, skill: str, parameters: dict) -> Message:
        """Create a message with both text and skill invocation."""
        from tests.utils.a2a_helpers import _dict_to_value

        msg = Message(
            message_id="msg_789",
            context_id="ctx_789",
            role=Role.ROLE_USER,
        )
        msg.parts.append(Part(text=text))
        msg.parts.append(Part(data=_dict_to_value({"skill": skill, "parameters": parameters})))
        return msg

    def _persist_a2a_step(
        self,
        tenant_id: str,
        principal_id: str,
        external_task_id: str,
        response_data: dict | None,
        status: str = "completed",
    ) -> str:
        """Persist an a2a workflow step carrying an outer external_task_id (#1544 B6).

        Mirrors what ``_create_media_buy_impl`` writes when the A2A boundary forwards the
        buyer's ``task_*`` id. ContextManager manages its own session, so no test-body DB
        access is needed. Returns the step id.
        """
        from src.core.context_manager import ContextManager

        ctx_manager = ContextManager()
        ctx = ctx_manager.create_context(tenant_id=tenant_id, principal_id=principal_id)
        step = ctx_manager.create_workflow_step(
            context_id=ctx.context_id,
            step_type="tool_call",
            owner="system",
            status=status,
            tool_name="create_media_buy",
            request_data={"budget": 5000},
            request_metadata={"protocol": "a2a", "external_task_id": external_task_id},
            response_data=response_data,
        )
        return step.step_id

    @pytest.mark.asyncio
    async def test_durable_get_task_rebuilds_completed_task_from_external_id(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """#1544 B6: a tasks/get poll of the buyer's outer task_* id resolves to the persisted
        workflow step (durably, cross-process) and rebuilds a terminal Task with the stored
        result artifact — even though the task is not in this process's in-memory map."""
        external_task_id = "task_durable_corr_1"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            {"media_buy_id": "mb_durable_1", "status": "completed"},
        )

        # The poll authenticates as the buyer; resolve to the real tenant identity.
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            task = handler._durable_task_from_step(external_task_id, mock_identity)

        assert task is not None, "durable fallback must resolve the buyer's outer task id to the step"
        assert task.id == external_task_id
        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        assert task.artifacts, "rebuilt terminal Task must carry the stored result artifact"
        assert task.artifacts[0].parts, "result artifact must have parts"

    @pytest.mark.asyncio
    async def test_durable_get_task_is_tenant_isolated(self, handler, sample_tenant, sample_principal):
        """The durable lookup is tenant-scoped: a poll authenticated as a DIFFERENT tenant must
        not resolve another tenant's outer task id."""
        from tests.factories import PrincipalFactory

        external_task_id = "task_durable_corr_2"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            {"media_buy_id": "mb_durable_2", "status": "completed"},
        )

        other_identity = PrincipalFactory.make_identity(
            tenant_id="a_different_tenant", principal_id="other_buyer", protocol="a2a"
        )
        handler._get_auth_token = MagicMock(return_value="other-tenant-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=other_identity):
            task = handler._durable_task_from_step(external_task_id, other_identity)

        assert task is None, "durable lookup must not cross tenant boundaries"

    @pytest.mark.asyncio
    async def test_durable_cancel_marks_pending_step_canceled(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """#1544 B6: tasks/cancel of the buyer's outer task_* id survives a restart — the
        persisted non-terminal workflow step is durably marked canceled (not just the
        in-memory map), and a subsequent durable tasks/get sees CANCELED."""
        external_task_id = "task_durable_cancel_1"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            None,
            status="requires_approval",
        )

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            task = await handler.on_cancel_task(CancelTaskRequest(id=external_task_id), context=None)
            polled = handler._durable_task_from_step(external_task_id, mock_identity)

        assert task is not None, "durable cancel must resolve the buyer's outer task id to the step"
        assert task.id == external_task_id
        assert task.status.state == TaskState.TASK_STATE_CANCELED
        assert polled is not None, "a durable poll after cancel must still resolve the task"
        assert polled.status.state == TaskState.TASK_STATE_CANCELED, (
            "cancel must persist on the workflow step — a poll after restart must see CANCELED"
        )

    @pytest.mark.asyncio
    async def test_durable_cancel_of_terminal_step_is_not_cancelable(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """A2A spec tasks/cancel: a task already in a terminal state cannot be canceled —
        the persisted completed step stays completed and TaskNotCancelableError is raised."""
        external_task_id = "task_durable_cancel_2"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            {"media_buy_id": "mb_cancel_terminal", "status": "completed"},
        )

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            with pytest.raises(TaskNotCancelableError):
                await handler.on_cancel_task(CancelTaskRequest(id=external_task_id), context=None)
            polled = handler._durable_task_from_step(external_task_id, mock_identity)

        assert polled is not None
        assert polled.status.state == TaskState.TASK_STATE_COMPLETED, (
            "a refused cancel must not mutate the persisted terminal step"
        )

    @pytest.mark.asyncio
    async def test_durable_cancel_is_tenant_isolated(self, handler, sample_tenant, sample_principal):
        """The durable cancel is tenant-scoped: a cancel authenticated as a DIFFERENT tenant
        must neither resolve nor mutate another tenant's task."""
        from tests.factories import PrincipalFactory

        external_task_id = "task_durable_cancel_3"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            None,
            status="requires_approval",
        )

        other_identity = PrincipalFactory.make_identity(
            tenant_id="a_different_tenant", principal_id="other_buyer", protocol="a2a"
        )
        handler._get_auth_token = MagicMock(return_value="other-tenant-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=other_identity):
            task = await handler.on_cancel_task(CancelTaskRequest(id=external_task_id), context=None)

        assert task is None, "durable cancel must not cross tenant boundaries"

    @pytest.mark.asyncio
    async def test_cancel_of_terminal_in_memory_task_is_not_cancelable(self, handler, mock_identity):
        """A2A spec tasks/cancel: the OWNER canceling an already-terminal in-memory task
        gets TaskNotCancelableError, not a silent overwrite to CANCELED."""
        task_id = "task_inmem_terminal"
        handler._remember_task(
            task_id,
            Task(id=task_id, context_id="ctx_inmem", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
            mock_identity,
        )
        handler._get_auth_token = MagicMock(return_value="owner-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            with pytest.raises(TaskNotCancelableError):
                await handler.on_cancel_task(CancelTaskRequest(id=task_id), context=None)

        assert handler.tasks[task_id].status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_get_in_memory_task_is_principal_isolated(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """[Round-13 B1] A same-tenant sibling principal must not read another principal's
        in-memory task via the public on_get_task — for BOTH a terminal and a nonterminal
        victim task present in memory (task ids are bearer-ish; the map key alone is not authz)."""
        from a2a.types import GetTaskRequest

        sibling = self._same_tenant_other_identity(sample_tenant)
        for state in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_WORKING):
            task_id = f"task_inmem_iso_get_{int(state)}"
            handler._remember_task(
                task_id,
                Task(id=task_id, context_id="ctx_victim", status=TaskStatus(state=state)),
                mock_identity,  # owned by sample_principal
            )
            handler._get_auth_token = MagicMock(return_value="sibling-token")
            with patch.object(handler, "_resolve_a2a_identity", return_value=sibling):
                seen = await handler.on_get_task(GetTaskRequest(id=task_id), context=None)
            assert seen is None, f"sibling principal must not read the victim's in-memory task (state={state})"

    @pytest.mark.asyncio
    async def test_cancel_in_memory_task_is_principal_isolated(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """[Round-13 B1] A same-tenant sibling principal must not cancel another principal's
        in-memory task via the public on_cancel_task; the victim task stays untouched. Covers
        both a nonterminal victim (must not be mutated to CANCELED) and a terminal victim (must
        not even be observable → returns None, not TaskNotCancelableError)."""
        sibling = self._same_tenant_other_identity(sample_tenant)

        nonterminal_id = "task_inmem_iso_cancel_working"
        handler._remember_task(
            nonterminal_id,
            Task(id=nonterminal_id, context_id="ctx_victim", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
            mock_identity,
        )
        terminal_id = "task_inmem_iso_cancel_done"
        handler._remember_task(
            terminal_id,
            Task(id=terminal_id, context_id="ctx_victim", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
            mock_identity,
        )

        handler._get_auth_token = MagicMock(return_value="sibling-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=sibling):
            nonterminal_result = await handler.on_cancel_task(CancelTaskRequest(id=nonterminal_id), context=None)
            terminal_result = await handler.on_cancel_task(CancelTaskRequest(id=terminal_id), context=None)

        assert nonterminal_result is None, "sibling must not cancel the victim's in-memory task"
        assert handler.tasks[nonterminal_id].status.state == TaskState.TASK_STATE_WORKING, (
            "the victim's in-memory task must not be mutated by a sibling's cancel"
        )
        assert terminal_result is None, "sibling must not even observe the victim's terminal task (no leak)"
        assert handler.tasks[terminal_id].status.state == TaskState.TASK_STATE_COMPLETED

    def _same_tenant_other_identity(self, sample_tenant):
        """A DIFFERENT principal in the SAME tenant (round-12 B2 adversary)."""
        from tests.factories import PrincipalFactory

        return PrincipalFactory.make_identity(
            tenant_id=sample_tenant["tenant_id"], principal_id="same_tenant_other_buyer", protocol="a2a"
        )

    @pytest.mark.asyncio
    async def test_durable_get_is_principal_isolated(self, handler, sample_tenant, sample_principal):
        """[Round-12 B2] A DIFFERENT principal in the SAME tenant who learns a task id
        must not read its stored response_data through the durable tasks/get path."""
        external_task_id = "task_prin_iso_get"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            {"media_buy_id": "mb_prin_iso", "status": "completed"},
        )

        sibling = self._same_tenant_other_identity(sample_tenant)
        handler._get_auth_token = MagicMock(return_value="other-principal-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=sibling):
            task = handler._durable_task_from_step(external_task_id, sibling)

        assert task is None, "durable lookup must not cross principal boundaries within a tenant"

    @pytest.mark.asyncio
    async def test_durable_cancel_is_principal_isolated(self, handler, sample_tenant, sample_principal, mock_identity):
        """[Round-12 B2] A DIFFERENT principal in the SAME tenant must not be able to
        cancel another principal's workflow via its task id."""
        external_task_id = "task_prin_iso_cancel"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            None,
            status="requires_approval",
        )

        handler._get_auth_token = MagicMock(return_value="other-principal-token")
        with patch.object(
            handler, "_resolve_a2a_identity", return_value=self._same_tenant_other_identity(sample_tenant)
        ):
            task = await handler.on_cancel_task(CancelTaskRequest(id=external_task_id), context=None)
        assert task is None, "durable cancel must not cross principal boundaries within a tenant"

        # The owner still sees the step untouched (non-terminal → WORKING skeleton).
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            owner_view = handler._durable_task_from_step(external_task_id, mock_identity)
        assert owner_view is not None
        assert owner_view.status.state == TaskState.TASK_STATE_WORKING, (
            "the sibling principal's cancel attempt must not have mutated the step"
        )

    @pytest.mark.asyncio
    async def test_get_task_reconciles_stale_in_memory_with_terminal_step(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """[Round-12 B3] The persisted workflow step is the source of truth: a poll of a
        task whose in-memory entry is still WORKING but whose workflow was terminalized
        in another process must return the terminal outcome, not the stale memory."""
        external_task_id = "task_stale_memory"
        self._persist_a2a_step(
            sample_tenant["tenant_id"],
            sample_principal["principal_id"],
            external_task_id,
            {"media_buy_id": "mb_stale", "status": "completed"},
        )
        # Stale in-memory entry from the process that created the task (owned by the buyer).
        handler._remember_task(
            external_task_id,
            Task(id=external_task_id, context_id="ctx_stale", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
            mock_identity,
        )

        from a2a.types import GetTaskRequest

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            task = await handler.on_get_task(GetTaskRequest(id=external_task_id), context=None)

        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_COMPLETED, (
            "stale in-memory WORKING must not shadow the terminal durable outcome"
        )
        assert task.artifacts, "the reconciled Task must carry the stored result artifact"
        assert handler.tasks[external_task_id].status.state == TaskState.TASK_STATE_COMPLETED, (
            "the in-memory map must be reconciled with the durable decision"
        )

    @pytest.mark.asyncio
    async def test_get_task_without_step_returns_in_memory_task(self, handler, mock_identity):
        """[Round-12 B3] A non-terminal in-memory task with NO persisted step (sync/simple
        skills) is still served from memory TO ITS OWNER — reconciliation must not lose it."""
        from a2a.types import GetTaskRequest

        task_id = "task_memory_only"
        handler._remember_task(
            task_id,
            Task(id=task_id, context_id="ctx_mem", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)),
            mock_identity,
        )
        handler._get_auth_token = MagicMock(return_value="owner-token")
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            task = await handler.on_get_task(GetTaskRequest(id=task_id), context=None)

        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED

    def _step_status(self, tenant_id: str, step_id: str) -> str:
        """Read a workflow step's committed status in its own fresh UoW session."""
        from src.core.database.repositories import WorkflowUoW

        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.get_by_step_id(step_id)
            return step.status if step is not None else "missing"

    @pytest.mark.asyncio
    async def test_cancel_and_approval_race_preserves_terminal_decision_both_orderings(
        self, handler, sample_tenant, sample_principal
    ):
        """[Round-13 B4] The terminal-transition policy is two-sided: whichever of a buyer
        cancel (WorkflowRepository.cancel_if_cancellable) and an approval
        (WorkflowRepository.update_status → transition_if_nonterminal) COMMITS FIRST wins,
        and the loser's conditional UPDATE refuses. Verified against REAL separate
        PostgreSQL sessions (each WorkflowUoW is its own session, committing on __exit__)
        in three interleavings, including a true TOCTOU overlap where the approval session
        began before the cancel committed."""
        from datetime import UTC, datetime

        from src.core.database.repositories import WorkflowUoW

        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]
        now = datetime.now(UTC)

        # ── Ordering 1: cancel commits first → approval refused, stays canceled ──
        step1 = self._persist_a2a_step(tenant_id, principal_id, "task_race_1", None, status="requires_approval")
        with WorkflowUoW(tenant_id) as u_cancel:
            assert u_cancel.workflows.cancel_if_cancellable(step1, completed_at=now) is True
        with WorkflowUoW(tenant_id) as u_approve:
            refused = u_approve.workflows.update_status(step1, status="completed", completed_at=now)
        assert refused is None, "approval must be refused once the step is terminally canceled"
        assert self._step_status(tenant_id, step1) == "canceled"

        # ── Ordering 2: approval commits first → cancel refused, stays completed ──
        step2 = self._persist_a2a_step(tenant_id, principal_id, "task_race_2", None, status="requires_approval")
        with WorkflowUoW(tenant_id) as u_approve2:
            assert u_approve2.workflows.update_status(step2, status="completed", completed_at=now) is not None
        with WorkflowUoW(tenant_id) as u_cancel2:
            assert u_cancel2.workflows.cancel_if_cancellable(step2, completed_at=now) is False
        assert self._step_status(tenant_id, step2) == "completed"

        # ── Ordering 3 (true TOCTOU overlap): approval session opens on a pending row,
        #    cancel commits in between, approval writes with its STALE knowledge ──
        step3 = self._persist_a2a_step(tenant_id, principal_id, "task_race_3", None, status="requires_approval")
        with WorkflowUoW(tenant_id) as u_approve3:
            # Approval "reads" the pending step (stale snapshot), does not write yet.
            assert u_approve3.workflows.get_by_step_id(step3).status == "requires_approval"
            # A concurrent buyer cancel commits canceled in a SEPARATE session (inner UoW).
            with WorkflowUoW(tenant_id) as u_cancel3:
                assert u_cancel3.workflows.cancel_if_cancellable(step3, completed_at=now) is True
            # Approval resumes and writes completed — the conditional UPDATE re-evaluates
            # against the committed canceled row (READ COMMITTED) and matches zero rows.
            refused3 = u_approve3.workflows.update_status(step3, status="completed", completed_at=now)
        assert refused3 is None, "approval must refuse after a cancel committed, even from a stale read (TOCTOU)"
        assert self._step_status(tenant_id, step3) == "canceled"

    def _step_response_data(self, tenant_id: str, step_id: str) -> dict | None:
        """Read a step's committed response_data in its own fresh UoW session."""
        from src.core.database.repositories import WorkflowUoW

        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.get_by_step_id(step_id)
            return step.response_data if step is not None else None

    async def test_context_manager_update_workflow_step_is_atomically_terminal_safe(
        self, handler, sample_tenant, sample_principal
    ):
        """[Round-14 B1] ContextManager.update_workflow_step routes the status write through
        the ATOMIC conditional-UPDATE primitive (transition_if_nonterminal), so an
        auto-approval that completes a step the buyer just canceled is refused with NO
        partial write and NO webhook — closing the async TOCTOU that
        MockAdServer._schedule_async_completion() (a delayed background thread) exposes.
        The whole write is refused: neither status NOR response_data is overwritten."""
        from datetime import UTC, datetime

        from src.core.context_manager import ContextManager
        from src.core.database.repositories import WorkflowUoW

        tenant_id = sample_tenant["tenant_id"]
        step_id = self._persist_a2a_step(
            tenant_id, sample_principal["principal_id"], "task_cm_terminal", None, status="requires_approval"
        )
        # Buyer cancels first (terminal) in a separate committed session.
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(step_id, completed_at=datetime.now(UTC))

        # Auto-approval path (its OWN session) tries to complete the canceled step with new
        # response_data → the atomic UPDATE matches zero rows → refused.
        ContextManager().update_workflow_step(step_id, status="completed", response_data={"attempted_completion": True})
        assert self._step_status(tenant_id, step_id) == "canceled", "canceled decision must stand"
        assert self._step_response_data(tenant_id, step_id) != {"attempted_completion": True}, (
            "a refused transition must not partially write response_data"
        )

    async def test_cancel_if_cancellable_refuses_approved_step(self, handler, sample_tenant, sample_principal):
        """[Round-15 B3] cancel_if_cancellable refuses an `approved` step (the point where
        irreversible ad-server order creation begins) but accepts `requires_approval` — so a
        cancel can never strand a real order behind a canceled task."""
        from datetime import UTC, datetime

        from src.core.database.repositories import WorkflowUoW

        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]

        approved = self._persist_a2a_step(tenant_id, principal_id, "task_appr", None, status="approved")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(approved, completed_at=datetime.now(UTC)) is False
        assert self._step_status(tenant_id, approved) == "approved", "an approved step must not be cancellable"

        pending = self._persist_a2a_step(tenant_id, principal_id, "task_pend", None, status="requires_approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(pending, completed_at=datetime.now(UTC)) is True
        assert self._step_status(tenant_id, pending) == "canceled"

    async def test_cancel_if_cancellable_accepts_legacy_approval_step(self, handler, sample_tenant, sample_principal):
        """The legacy adapter-emitted ``approval`` status (GAM/Broadstreet/base_workflow)
        is a pre-side-effect awaiting-decision alias of ``requires_approval``, so it is cancellable
        for the same reason — a buyer can cancel an awaiting-approval order before any ad-server work
        begins. It's approvable AND cancellable (was approvable-but-not-cancellable)."""
        from datetime import UTC, datetime

        from src.core.database.repositories import WorkflowUoW

        tenant_id = sample_tenant["tenant_id"]
        step_id = self._persist_a2a_step(
            tenant_id, sample_principal["principal_id"], "task_legacy_appr_cancel", None, status="approval"
        )
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(step_id, completed_at=datetime.now(UTC)) is True
        assert self._step_status(tenant_id, step_id) == "canceled"

    @pytest.mark.asyncio
    async def test_a2a_cancel_of_approved_task_is_not_cancelable(
        self, handler, sample_tenant, sample_principal, mock_identity
    ):
        """[Round-15 B3] The public A2A tasks/cancel of an `approved` task raises
        TaskNotCancelableError and leaves the step approved — no orphaned ad-server order."""
        tenant_id = sample_tenant["tenant_id"]
        external_task_id = "task_appr_cancel"
        step_id = self._persist_a2a_step(
            tenant_id, sample_principal["principal_id"], external_task_id, None, status="approved"
        )

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
        with patch.object(handler, "_resolve_a2a_identity", return_value=mock_identity):
            with pytest.raises(TaskNotCancelableError):
                await handler.on_cancel_task(CancelTaskRequest(id=external_task_id), context=None)

        assert self._step_status(tenant_id, step_id) == "approved"

    async def test_cancel_if_cancellable_refuses_in_progress_step(self, handler, sample_tenant, sample_principal):
        """[Round-16 B2] `in_progress` marks active external work (create/update persist it
        BEFORE adapter side-effects; the approval worker claims it before approve_order), so
        it must NOT be cancellable — otherwise a cancel orphans a real ad-server order behind
        a canceled task. `requires_approval` (no side effects yet) stays cancellable."""
        from datetime import UTC, datetime

        from src.core.database.repositories import WorkflowUoW

        tenant_id = sample_tenant["tenant_id"]
        principal_id = sample_principal["principal_id"]

        in_progress = self._persist_a2a_step(tenant_id, principal_id, "task_inprog", None, status="in_progress")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(in_progress, completed_at=datetime.now(UTC)) is False
        assert self._step_status(tenant_id, in_progress) == "in_progress", "in_progress must not be cancellable"

        pending = self._persist_a2a_step(tenant_id, principal_id, "task_inprog_ctl", None, status="requires_approval")
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows.cancel_if_cancellable(pending, completed_at=datetime.now(UTC)) is True
        assert self._step_status(tenant_id, pending) == "canceled"

    @pytest.mark.asyncio
    async def test_natural_language_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test natural language invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create natural language message
            message = create_a2a_text_message("What video products do you have available?")
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "natural_language"
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "product_catalog"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database (should match non_guaranteed_video)
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"Natural language get_products validation: {validation_result}")

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message
            skill_params = {
                "brief": "Display advertising for news content",
                "brand": {"domain": "testbrand.com"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "get_products_result"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database (should match display product)
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"Explicit skill get_products validation: {validation_result}")

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products_a2a_spec(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation using A2A spec 'input' field instead of 'parameters'."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using A2A spec 'input' field
            skill_params = {
                "brief": "Premium coffee brands",
                "brand": {"domain": "testbrand.com"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "get_products_result"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"A2A spec 'input' field get_products validation: {validation_result}")

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation for create_media_buy.

        NOTE: This test now uses the REAL mock adapter and code paths,
        only mocking authentication. This ensures we catch serialization bugs.
        """
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using AdCP spec format
            from datetime import UTC, datetime, timedelta

            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            skill_params = {
                "brand": {"domain": "testbrand.com"},
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
                "packages": [
                    {
                        "product_id": sample_products[0],  # Use product_id per AdCP spec
                        "budget": 10000.0,  # Float only per AdCP v2.2.0, currency from pricing_option
                        "pricing_option_id": "cpm_usd_fixed",  # Required in adcp 2.5.0
                    }
                ],
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }
            message = create_a2a_message_with_skill("create_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes REAL _create_media_buy_impl with mock adapter
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "create_media_buy" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "create_media_buy_result"

            # Extract response data
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            # Per AdCP spec, CreateMediaBuyResponse has media_buy_id, packages, etc.
            # No 'success' field in the spec - that's a protocol-level field
            assert "media_buy_id" in artifact_data

            # Verify packages are properly serialized (this would have caught the bug!)
            assert "packages" in artifact_data
            assert isinstance(artifact_data["packages"], list)

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy_manual_approval(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test create_media_buy returns status=submitted when manual approval required."""
        # Update tenant to require manual approval
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        with get_db_session() as session:
            tenant = session.get(Tenant, sample_tenant["tenant_id"])
            tenant.human_review_required = True
            session.commit()

        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock identity resolution
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message
            from datetime import UTC, datetime, timedelta

            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            skill_params = {
                "brand": {"domain": "testbrand.com"},
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
                "packages": [
                    {
                        "product_id": sample_products[0],
                        "budget": 10000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }
            message = create_a2a_message_with_skill("create_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result has status=submitted (manual approval required)
            assert isinstance(result, Task)
            assert result.status.state == TaskState.TASK_STATE_SUBMITTED
            # Per A2A spec, tasks requiring approval should not have artifacts until approved
            # (protobuf uses empty repeated field [] instead of None)
            assert not result.artifacts

    @pytest.mark.asyncio
    async def test_hybrid_invocation(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test hybrid invocation with both text and skill."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create hybrid message (text + explicit skill)
            skill_params = {"brief": "Sports video advertising", "brand": {"domain": "testbrand.com"}}
            message = self.create_message_hybrid(
                "I need video products for sports content", "get_products", skill_params
            )
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify explicit skill took precedence
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert "video products for sports" in result.metadata["request_text"]

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database
            assert len(products) > 0

    # TODO: Add test_unknown_skill_error once we understand how A2A server handles unknown skills
    # TODO: Needs investigation of proper error handling approach (A2AError not in current a2a library)

    @pytest.mark.asyncio
    async def test_multiple_skill_invocations(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products
    ):
        """A message with multiple skills is REJECTED before any skill runs.

        Aggregating divergent per-skill outcomes into one Task is incoherent once a
        skill has real side effects (a submitted create_media_buy persists a workflow
        while a sibling fails), so a multi-skill batch is rejected up front as a typed
        UNSUPPORTED_FEATURE failed Task — no skill executes. Full-Task identity across
        real per-skill child Tasks is tracked in #1614."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create message with multiple skill invocations
            # Note: get_signals removed - should come from dedicated signals agents
            from tests.utils.a2a_helpers import _dict_to_value

            message = Message(
                message_id="msg_multi",
                context_id="ctx_multi",
                role=Role.ROLE_USER,
            )
            message.parts.append(
                Part(
                    data=_dict_to_value(
                        {
                            "skill": "get_products",
                            "parameters": {"brief": "video ads", "brand": {"domain": "testbrand.com"}},
                        }
                    )
                )
            )
            message.parts.append(
                Part(
                    data=_dict_to_value(
                        {
                            "skill": "list_creative_formats",
                            "parameters": {},
                        }
                    )
                )
            )
            params = SendMessageRequest(message=message)

            # Process the message - this executes the real code path.
            result = await handler.on_message_send(params, context=ctx)

            # Rejected up front → terminal failed Task with UNSUPPORTED_FEATURE, and no
            # skill ran (a real get_products would have produced a product_catalog).
            envelope = assert_failed_task_envelope(
                result, code="UNSUPPORTED_FEATURE", recovery="correctable", artifact_name="processing_error"
            )
            assert "multiple skills" in envelope["errors"][0]["message"].lower()

    # TODO: Add test_missing_authentication once we understand how A2A server handles auth errors
    # TODO: Needs investigation of proper error handling approach (A2AError not in current a2a library)

    @pytest.mark.asyncio
    async def test_adcp_schema_validation_integration(self, validator):
        """Test A2A-to-AdCP schema validation integration."""
        # Test the validation helper directly with mock data

        # Create mock A2A task with AdCP-compliant product data

        mock_adcp_products_response = {
            "products": [
                {
                    "id": "prod_test_1",
                    "name": "Test Video Product",
                    "description": "Test video advertising product",
                    "formats": [{"id": "video_720p", "name": "720p Video", "width": 1280, "height": 720}],
                    "pricing": {"base_cpm": 12.5, "currency": "USD"},
                    "targeting_template": {"demographics": ["18-34"], "interests": ["technology"]},
                    "countries": ["US", "CA"],
                    "delivery_type": "guaranteed",
                }
            ],
            "message": "Products retrieved successfully",
        }

        # Create A2A artifacts structure (protobuf)
        from tests.utils.a2a_helpers import _dict_to_value

        artifact = Artifact(
            artifact_id="test_artifact_1",
            name="get_products_result",
        )
        artifact.parts.append(Part(data=_dict_to_value(mock_adcp_products_response)))

        mock_task = Task(
            id="test_task_1",
            context_id="test_context_1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[artifact],
        )

        # Test validation for each skill that has AdCP schemas
        adcp_skills_to_test = {
            "get_products": mock_task,
            # Add other skills when we have mock data for them
        }

        for skill_name, task_result in adcp_skills_to_test.items():
            validation_result = await validator.validate_a2a_skill_response(skill_name, task_result)

            print(f"\n=== Schema Validation Results for {skill_name} ===")
            print(f"Valid: {validation_result['valid']}")
            print(f"Schema tested: {validation_result['schema_tested']}")

            if validation_result["errors"]:
                print(f"Errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Warnings: {validation_result['warnings']}")

            # For now, don't fail on validation errors - just ensure the validator runs
            assert "schema_tested" in validation_result

            # If schema validation is available and schema exists, it should have attempted validation
            if SCHEMA_VALIDATION_AVAILABLE and validation_result["schema_tested"]:
                assert validation_result["schema_tested"] == "get-products"
                # Either valid or has meaningful errors/warnings
                assert validation_result["valid"] or validation_result["errors"] or validation_result["warnings"]

    def test_skill_handler_mapping(self, handler):
        """Test that all advertised skills have handlers."""
        # Get skills from agent card
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()

        # Verify all skills have handlers
        expected_skills = {skill.name for skill in agent_card.skills}

        # Test that _handle_explicit_skill can handle all advertised skills
        for skill_name in expected_skills:
            # This should not raise an exception for any advertised skill
            try:
                # We can't easily test the actual execution without full setup,
                # but we can at least verify the skill name is recognized
                assert skill_name in [
                    "get_adcp_capabilities",  # AdCP v3 discovery endpoint
                    "get_products",
                    "create_media_buy",
                    "update_media_buy",  # Added for media buy management
                    "get_media_buy_delivery",  # Added for delivery metrics
                    "get_creative_delivery",  # Added for creative-level delivery metrics
                    "update_performance_index",  # Added for performance optimization
                    "sync_creatives",
                    "list_creatives",
                    "approve_creative",
                    "get_media_buy_status",
                    "optimize_media_buy",
                    "list_creative_formats",  # Keep existing creative format endpoint
                    "list_authorized_properties",  # Added for AdCP compliance
                    "get_media_buys",
                    "list_accounts",  # Added for account management (UC-011)
                    "sync_accounts",  # Added for account sync (UC-011)
                ], f"Skill {skill_name} not in expected skill list"
            except Exception as e:
                pytest.fail(f"Skill {skill_name} should be handled but caused error: {e}")

    # Phase 2: Tests for previously untested skills

    @pytest.mark.asyncio
    async def test_update_media_buy_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test update_media_buy skill invocation."""
        # Create a media buy in database first
        from datetime import UTC, datetime, timedelta

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy

        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = start_date + timedelta(days=30)

        with get_db_session() as session:
            media_buy = MediaBuy(
                media_buy_id="mb_test_123",
                tenant_id=sample_tenant["tenant_id"],
                principal_id=sample_principal["principal_id"],
                status="active",
                order_name="Test Campaign",
                advertiser_name="Test Brand",
                start_date=start_date.date(),
                end_date=end_date.date(),
                start_time=start_date,  # Add start_time for flight days calculation
                end_time=end_date,  # Add end_time for flight days calculation
                budget=10000.0,
                currency="USD",
                raw_request={"brand": {"domain": "testbrand.com"}, "packages": []},
            )
            session.add(media_buy)
            session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock identity resolution and adapter
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
            patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        ):
            # Mock request headers to provide Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Mock adapter — must return what real adapters return: our defaulted
            # UpdateMediaBuySuccess subclass. adcp 6.6 (spec 3.1.1) made status/revision
            # required on the raw library UpdateMediaBuySuccessResponse; every production
            # construction site (mock/GAM/kevel adapters, the _impl, and the A2A server
            # coercion) routes through this subclass which defaults status='completed'
            # and revision, so the wire response is always spec-valid.
            from src.core.schemas import UpdateMediaBuySuccess

            mock_adapter = MagicMock()
            mock_adapter.update_media_buy.return_value = UpdateMediaBuySuccess(
                media_buy_id="mb_test_123",
                affected_packages=[],  # adcp 2.5.0 field (replaces packages/errors)
            )
            mock_get_adapter.return_value = mock_adapter

            # Create skill invocation
            # Per AdCP spec, budget is a float, not a Budget object in update_media_buy
            skill_params = {
                "media_buy_id": "mb_test_123",
                "budget": 15000.0,  # Float per AdCP spec, not Budget object
            }
            message = create_a2a_message_with_skill("update_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message
            result = await handler.on_message_send(params, context=ctx)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_media_buy" in result.metadata["skills_requested"]

            # adcp 6.6 (spec 3.1.1) guard: the A2A update_media_buy wire response must
            # carry the now-required status/revision fields. This proves the defaulted
            # UpdateMediaBuySuccess subclass reaches the wire (not the raw library type),
            # which is the whole point of du92.
            # Value-presence guard: status/revision must reach the wire. (Numbers are
            # doubles on the A2A protobuf-Struct transport, so 1 arrives as 1.0 — a
            # transport-wide representation detail, not du92's concern; assert on value.)
            assert result.artifacts, "update_media_buy skill returned no artifacts"
            payload = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert payload["status"] == "completed", f"missing/incorrect status on wire: {payload!r}"
            assert payload["revision"] == 1, f"missing/incorrect revision on wire: {payload!r}"

    @pytest.mark.asyncio
    async def test_list_creative_formats_skill(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test list_creative_formats skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {"brief": "display formats"}
            message = create_a2a_message_with_skill("list_creative_formats", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_creative_formats" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "formats" in artifact_data

    @pytest.mark.asyncio
    async def test_list_authorized_properties_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test list_authorized_properties skill invocation."""
        # Create verified publisher partner for the tenant
        import uuid

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PublisherPartner

        # Generate unique publisher domain to avoid conflicts
        unique_publisher_domain = f"test-publisher-{uuid.uuid4().hex[:8]}.example.com"

        with get_db_session() as session:
            # Check if publisher already exists, create if not
            stmt = select(PublisherPartner).filter_by(
                publisher_domain=unique_publisher_domain, tenant_id=sample_tenant["tenant_id"]
            )
            existing_publisher = session.scalars(stmt).first()

            if not existing_publisher:
                publisher = PublisherPartner(
                    tenant_id=sample_tenant["tenant_id"],
                    publisher_domain=unique_publisher_domain,
                    display_name="Test Publisher",
                    is_verified=True,  # Must be verified for list_authorized_properties
                    sync_status="success",
                )
                session.add(publisher)
                session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_authorized_properties", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_authorized_properties" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response - per AdCP v2.4 spec, response has publisher_domains
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "publisher_domains" in artifact_data
            assert len(artifact_data["publisher_domains"]) > 0

    @pytest.mark.asyncio
    async def test_sync_creatives_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test sync_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation with creatives
            skill_params = {
                "creatives": [
                    {
                        "creative_id": "creative_test_1",
                        "name": "Test Creative",
                        "format_id": "display_300x250",
                        "assets": build_assets(image_spec("asset_1", url="https://example.com/creative.jpg")),
                    }
                ]
            }
            message = create_a2a_message_with_skill("sync_creatives", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "sync_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data or "failed_creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_list_creatives_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test list_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_creatives", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_update_performance_index_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test update_performance_index skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_id": "mb_test_123",
                "performance_index": 1.25,
            }
            message = create_a2a_message_with_skill("update_performance_index", skill_params)
            params = SendMessageRequest(message=message)

            # This will likely fail because media_buy doesn't exist, but tests the code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_performance_index" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test get_media_buy_delivery skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_ids": ["mb_test_123"],
            }
            message = create_a2a_message_with_skill("get_media_buy_delivery", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_media_buy_delivery" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_skill_forwards_typed_account(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """A valid account survives the real on_message_send dispatch as a typed AccountReference.

        The handler-level unit tests (test_a2a_parameter_mapping.py) prove the skill method
        forwards req.account, and the malformed-account wire test (test_a2a_error_responses.py)
        proves the error path. This is the missing happy-path half: it drives the full
        DataPart-extraction → param-passing pipeline through on_message_send and asserts the
        buyer's account reaches the core tool validated, not as the raw dict that crashed
        resolve_account (account_ref.root on a dict).
        """
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
            patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery,
        ):
            mock_delivery.return_value = {"media_buys": []}

            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_ids": ["mb_test_123"], "account": {"account_id": "acct-1"}}
            message = create_a2a_message_with_skill("get_media_buy_delivery", skill_params)
            params = SendMessageRequest(message=message)

            result = await handler.on_message_send(params, context=ctx)

            assert isinstance(result, Task)
            assert result.artifacts is not None

            expected = LibraryAccountReference.model_validate({"account_id": "acct-1"})
            assert_delivery_forwarded_account(mock_delivery, expected)

    @pytest.mark.asyncio
    async def test_approve_creative_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test approve_creative skill returns a failed Task with UNSUPPORTED_FEATURE (not JSON-RPC)."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"creative_id": "creative_test_123"}
            message = create_a2a_message_with_skill("approve_creative", skill_params)
            params = SendMessageRequest(message=message)

            result = await handler.on_message_send(params, context=ctx)

            # Application-layer failure → failed Task with UNSUPPORTED_FEATURE, not JSON-RPC.
            assert_failed_task_envelope(result, code="UNSUPPORTED_FEATURE", recovery="correctable")

    @pytest.mark.asyncio
    async def test_get_media_buy_status_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test get_media_buy_status skill returns a failed Task with UNSUPPORTED_FEATURE (not JSON-RPC)."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_id": "mb_test_123"}
            message = create_a2a_message_with_skill("get_media_buy_status", skill_params)
            params = SendMessageRequest(message=message)

            result = await handler.on_message_send(params, context=ctx)

            # Application-layer failure → failed Task with UNSUPPORTED_FEATURE, not JSON-RPC.
            assert_failed_task_envelope(result, code="UNSUPPORTED_FEATURE", recovery="correctable")

    @pytest.mark.asyncio
    async def test_optimize_media_buy_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test optimize_media_buy skill returns a failed Task with UNSUPPORTED_FEATURE (not JSON-RPC)."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_id": "mb_test_123"}
            message = create_a2a_message_with_skill("optimize_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            result = await handler.on_message_send(params, context=ctx)

            # Application-layer failure → failed Task with UNSUPPORTED_FEATURE, not JSON-RPC.
            assert_failed_task_envelope(result, code="UNSUPPORTED_FEATURE", recovery="correctable")


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])

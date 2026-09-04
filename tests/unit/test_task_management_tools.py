"""Tests for task management MCP tools (list_tasks, get_task, complete_task).

These tests verify that the task management tools work correctly.
Issue #816 revealed that list_tasks was broken but had no test coverage.
"""

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from src.core.database.models import WorkflowStep
from src.core.exceptions import VALIDATION_ERROR_SUGGESTION, AdCPTaskNotFoundError
from tests.factories.principal import PrincipalFactory


class TestListTasksTool:
    """Test the list_tasks MCP tool actually works."""

    @pytest.fixture
    def mock_workflow_repo(self):
        """Create a mock WorkflowRepository."""
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_uow(self, mock_workflow_repo):
        """Create a mock WorkflowUoW context manager."""
        uow = MagicMock()
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=None)
        uow.workflows = mock_workflow_repo
        return uow

    @pytest.fixture
    def sample_tenant(self):
        return {"tenant_id": "test_tenant", "name": "Test Tenant"}

    @pytest.fixture
    def sample_workflow_step(self):
        """Create a sample workflow step for testing."""
        step = Mock(spec=WorkflowStep)
        step.step_id = "step_123"
        step.context_id = "ctx_123"
        step.status = "requires_approval"
        step.step_type = "approval"
        step.tool_name = "create_media_buy"
        step.owner = "publisher"
        step.created_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        return step

    async def _get_list_tasks_fn(self):
        """Get the list_tasks function from MCP tool registry."""
        from src.core.main import mcp

        tool = await mcp.get_tool("list_tasks")
        assert tool is not None, "list_tasks should be registered (unified mode is default)"
        return tool.fn

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
            protocol="mcp",
        )

    async def test_list_tasks_returns_tasks(self, mock_uow, mock_workflow_repo, sample_tenant, sample_workflow_step):
        """Test that list_tasks returns workflow steps correctly."""
        list_tasks_fn = await self._get_list_tasks_fn()

        mock_workflow_repo.count_by_tenant.return_value = 1
        mock_workflow_repo.list_by_tenant.return_value = [sample_workflow_step]
        mock_workflow_repo.get_mappings_for_steps.return_value = {"step_123": []}

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await list_tasks_fn(identity=identity)

        assert "tasks" in result
        assert "total" in result
        assert result["total"] == 1

    async def test_list_tasks_filters_by_status(
        self, mock_uow, mock_workflow_repo, sample_tenant, sample_workflow_step
    ):
        """Test that list_tasks applies status filter."""
        list_tasks_fn = await self._get_list_tasks_fn()

        mock_workflow_repo.count_by_tenant.return_value = 1
        mock_workflow_repo.list_by_tenant.return_value = [sample_workflow_step]
        mock_workflow_repo.get_mappings_for_steps.return_value = {"step_123": []}

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await list_tasks_fn(status="requires_approval", identity=identity)

        assert "tasks" in result
        mock_workflow_repo.count_by_tenant.assert_called_once_with(
            status="requires_approval",
            object_type=None,
            object_id=None,
        )


class TestGetTaskTool:
    """Test the get_task MCP tool actually works."""

    @pytest.fixture
    def mock_workflow_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_uow(self, mock_workflow_repo):
        uow = MagicMock()
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=None)
        uow.workflows = mock_workflow_repo
        return uow

    @pytest.fixture
    def sample_tenant(self):
        return {"tenant_id": "test_tenant", "name": "Test Tenant"}

    @pytest.fixture
    def sample_workflow_step(self):
        step = Mock(spec=WorkflowStep)
        step.step_id = "step_123"
        step.context_id = "ctx_123"
        step.status = "requires_approval"
        step.step_type = "approval"
        step.tool_name = "create_media_buy"
        step.owner = "publisher"
        step.created_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        step.transaction_details = None
        return step

    async def _get_get_task_fn(self):
        """Get the get_task function from MCP tool registry."""
        from src.core.main import mcp

        tool = await mcp.get_tool("get_task")
        assert tool is not None, "get_task should be registered (unified mode is default)"
        return tool.fn

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
            protocol="mcp",
        )

    async def test_get_task_returns_task_details(
        self, mock_uow, mock_workflow_repo, sample_tenant, sample_workflow_step
    ):
        """Test that get_task returns task details correctly."""
        get_task_fn = await self._get_get_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.return_value = sample_workflow_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await get_task_fn(task_id="step_123", identity=identity)

        assert result["task_id"] == "step_123"
        assert result["status"] == "requires_approval"
        mock_workflow_repo.get_by_step_id_or_raise.assert_called_once_with("step_123", principal_id="principal_123")

    async def test_get_task_not_found_raises_error(self, mock_uow, mock_workflow_repo, sample_tenant):
        """Test that get_task raises ToolError when task not found.

        The MCP boundary (with_error_logging) translates ValueError to
        ToolError with VALIDATION_ERROR code. This is correct: business
        logic raises ValueError, the transport boundary translates it.
        """
        from fastmcp.exceptions import ToolError

        get_task_fn = await self._get_get_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.side_effect = AdCPTaskNotFoundError("Task nonexistent not found")

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            with pytest.raises(ToolError, match="not found"):
                await get_task_fn(task_id="nonexistent", identity=identity)

    async def test_get_task_empty_task_id_raises_validation(self, mock_uow, mock_workflow_repo, sample_tenant):
        """Empty task_id is validated in the shared tool (uniform A2A+MCP)."""
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.task_management import get_task

        identity = self._make_identity(sample_tenant)
        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            with pytest.raises(AdCPValidationError, match="task_id is required") as exc:
                await get_task(task_id="", identity=identity)
        assert exc.value.field == "task_id"
        assert exc.value.suggestion == VALIDATION_ERROR_SUGGESTION
        mock_workflow_repo.get_by_step_id_or_raise.assert_not_called()


class TestCompleteTaskTool:
    """Test the complete_task MCP tool actually works."""

    @pytest.fixture
    def mock_workflow_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_uow(self, mock_workflow_repo):
        uow = MagicMock()
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=None)
        uow.workflows = mock_workflow_repo
        return uow

    @pytest.fixture
    def sample_tenant(self):
        return {"tenant_id": "test_tenant", "name": "Test Tenant"}

    @pytest.fixture
    def sample_pending_step(self):
        step = Mock(spec=WorkflowStep)
        step.step_id = "step_123"
        step.context_id = "ctx_123"
        step.status = "requires_approval"
        step.step_type = "approval"
        step.tool_name = "create_media_buy"
        step.owner = "publisher"
        step.created_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        step.completed_at = None
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        return step

    async def _get_complete_task_fn(self):
        """Get the complete_task function from MCP tool registry."""
        from src.core.main import mcp

        tool = await mcp.get_tool("complete_task")
        assert tool is not None, "complete_task should be registered (unified mode is default)"
        return tool.fn

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
            protocol="mcp",
        )

    async def test_complete_task_updates_status(self, mock_uow, mock_workflow_repo, sample_tenant, sample_pending_step):
        """Test that complete_task updates task status."""
        complete_task_fn = await self._get_complete_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.return_value = sample_pending_step
        mock_workflow_repo.update_status.return_value = sample_pending_step

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await complete_task_fn(task_id="step_123", status="completed", identity=identity)

        assert result["status"] == "completed"
        assert result["task_id"] == "step_123"
        mock_workflow_repo.get_by_step_id_or_raise.assert_called_once_with("step_123", principal_id="principal_123")
        mock_workflow_repo.update_status.assert_called_once_with(
            "step_123",
            status="completed",
            completed_at=ANY,
            response_data={"manually_completed": True, "completed_by": "principal_123"},
            principal_id="principal_123",
        )

    async def test_complete_task_rejects_invalid_status(self, mock_uow, mock_workflow_repo, sample_tenant):
        """Test that complete_task rejects invalid status values.

        The MCP boundary (with_error_logging) translates ValueError to
        ToolError with VALIDATION_ERROR code.
        """
        from fastmcp.exceptions import ToolError

        complete_task_fn = await self._get_complete_task_fn()

        identity = self._make_identity(sample_tenant)

        with pytest.raises(ToolError, match="Invalid status"):
            await complete_task_fn(task_id="step_123", status="invalid_status", identity=identity)

    async def test_complete_task_empty_task_id_raises_validation(self, mock_uow, mock_workflow_repo, sample_tenant):
        """Empty task_id is validated in the shared tool (uniform A2A+MCP)."""
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.task_management import complete_task

        identity = self._make_identity(sample_tenant)
        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            with pytest.raises(AdCPValidationError, match="task_id is required") as exc:
                await complete_task(task_id="", identity=identity)
        assert exc.value.field == "task_id"
        assert exc.value.suggestion == VALIDATION_ERROR_SUGGESTION
        mock_workflow_repo.get_by_step_id_or_raise.assert_not_called()

    @pytest.mark.parametrize("bad_id", [123, True, ["x"], {"id": "x"}])
    async def test_require_task_id_rejects_truthy_non_string(self, mock_uow, mock_workflow_repo, sample_tenant, bad_id):
        """Type half of require_task_id — present wrong type ≠ presence (KM Aug-12)."""
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.task_management import complete_task, get_task

        identity = self._make_identity(sample_tenant)
        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            with pytest.raises(AdCPValidationError, match="task_id must be a string") as get_exc:
                await get_task(task_id=bad_id, identity=identity)
            with pytest.raises(AdCPValidationError, match="task_id must be a string") as complete_exc:
                await complete_task(task_id=bad_id, identity=identity)
        assert get_exc.value.field == complete_exc.value.field == "task_id"
        mock_workflow_repo.get_by_step_id_or_raise.assert_not_called()


class TestCompleteTaskA2AForwarder:
    """A2A complete_task rejects unknown buyer keys; forwards only L2 buyer params."""

    @pytest.mark.asyncio
    async def test_unknown_buyer_key_raises_validation(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPValidationError

        handler = object.__new__(AdCPRequestHandler)
        identity = PrincipalFactory.make_identity(
            principal_id="p1",
            tenant_id="t1",
            tenant={"tenant_id": "t1"},
            protocol="a2a",
        )
        with pytest.raises(AdCPValidationError, match="Unexpected parameter") as exc:
            await handler._handle_complete_task_skill(
                {"task_id": "step-1", "statas": "failed", "push_notification_config": {}},
                identity,
            )
        assert "statas" in str(exc.value)

    @pytest.mark.asyncio
    async def test_forwarded_kwargs_match_signature_buyer_params(self):
        from unittest.mock import AsyncMock, patch

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.tools.task_management import COMPLETE_TASK_BUYER_PARAMS

        handler = object.__new__(AdCPRequestHandler)
        identity = PrincipalFactory.make_identity(
            principal_id="p1",
            tenant_id="t1",
            tenant={"tenant_id": "t1"},
            protocol="a2a",
        )
        with patch(
            "src.a2a_server.adcp_a2a_server.core_complete_task",
            new_callable=AsyncMock,
            return_value={"task_id": "step-1", "status": "failed"},
        ) as mock_complete:
            await handler._handle_complete_task_skill(
                {
                    "task_id": "step-1",
                    "status": "failed",
                    "error_message": "boom",
                    "response_data": {"ok": True},
                },
                identity,
            )
        mock_complete.assert_awaited_once()
        kwargs = mock_complete.await_args.kwargs
        forwarded_buyer = set(kwargs) - {"identity"}
        assert forwarded_buyer == COMPLETE_TASK_BUYER_PARAMS
        assert "context" not in kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["error_message"] == "boom"
        assert kwargs["identity"] is identity

"""Tests for task management MCP tools (list_tasks, get_task, complete_task).

These tests verify that the task management tools work correctly.
Issue #816 revealed that list_tasks was broken but had no test coverage.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.database.models import WorkflowStep


class TestListTasksTool:
    """Test the list_tasks MCP tool actually works."""

    @pytest.fixture
    def mock_db_session(self):
        """Create a mock database session."""
        session = MagicMock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        return session

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

    def _get_list_tasks_fn(self):
        """Get the list_tasks function from MCP tool registry."""
        from src.core.main import mcp

        tool = mcp._tool_manager._tools.get("list_tasks")
        assert tool is not None, "list_tasks should be registered (unified mode is default)"
        return tool.fn

    def test_list_tasks_returns_tasks(self, mock_db_session, sample_tenant, sample_workflow_step):
        """Test that list_tasks returns workflow steps correctly."""
        list_tasks_fn = self._get_list_tasks_fn()

        # Mock the dependencies
        mock_db_session.scalar.return_value = 1  # total count
        mock_db_session.scalars.return_value.all.side_effect = [
            [sample_workflow_step],  # First call: workflow steps
            [],  # Second call: object mappings
        ]

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
            patch("src.core.main.get_db_session", return_value=mock_db_session),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            result = list_tasks_fn(context=Mock())

        assert "tasks" in result
        assert "total" in result
        assert result["total"] == 1

    def test_list_tasks_filters_by_status(self, mock_db_session, sample_tenant, sample_workflow_step):
        """Test that list_tasks applies status filter."""
        list_tasks_fn = self._get_list_tasks_fn()

        mock_db_session.scalar.return_value = 1
        mock_db_session.scalars.return_value.all.side_effect = [
            [sample_workflow_step],
            [],
        ]

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
            patch("src.core.main.get_db_session", return_value=mock_db_session),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            result = list_tasks_fn(status="requires_approval", context=Mock())

        assert "tasks" in result
        # The query was executed - if there was an AttributeError it would have raised


class TestGetTaskTool:
    """Test the get_task MCP tool actually works."""

    @pytest.fixture
    def mock_db_session(self):
        session = MagicMock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        return session

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

    def _get_get_task_fn(self):
        """Get the get_task function from MCP tool registry."""
        from src.core.main import mcp

        tool = mcp._tool_manager._tools.get("get_task")
        assert tool is not None, "get_task should be registered (unified mode is default)"
        return tool.fn

    def test_get_task_returns_task_details(self, mock_db_session, sample_tenant, sample_workflow_step):
        """Test that get_task returns task details correctly."""
        get_task_fn = self._get_get_task_fn()

        mock_db_session.scalars.return_value.first.return_value = sample_workflow_step
        mock_db_session.scalars.return_value.all.return_value = []  # no mappings

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
            patch("src.core.main.get_db_session", return_value=mock_db_session),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            result = get_task_fn(task_id="step_123", context=Mock())

        assert result["task_id"] == "step_123"
        assert result["status"] == "requires_approval"

    def test_get_task_not_found_raises_error(self, mock_db_session, sample_tenant):
        """Test that get_task raises error when task not found."""
        get_task_fn = self._get_get_task_fn()

        mock_db_session.scalars.return_value.first.return_value = None

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
            patch("src.core.main.get_db_session", return_value=mock_db_session),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            with pytest.raises(ValueError, match="not found"):
                get_task_fn(task_id="nonexistent", context=Mock())


class TestCompleteTaskTool:
    """Test the complete_task MCP tool actually works."""

    @pytest.fixture
    def mock_db_session(self):
        session = MagicMock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        return session

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

    def _get_complete_task_fn(self):
        """Get the complete_task function from MCP tool registry."""
        from src.core.main import mcp

        tool = mcp._tool_manager._tools.get("complete_task")
        assert tool is not None, "complete_task should be registered (unified mode is default)"
        return tool.fn

    def test_complete_task_updates_status(self, mock_db_session, sample_tenant, sample_pending_step):
        """Test that complete_task updates task status."""
        complete_task_fn = self._get_complete_task_fn()

        mock_db_session.scalars.return_value.first.return_value = sample_pending_step

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
            patch("src.core.main.get_db_session", return_value=mock_db_session),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            result = complete_task_fn(task_id="step_123", status="completed", context=Mock())

        assert result["status"] == "completed"
        assert result["task_id"] == "step_123"
        assert sample_pending_step.status == "completed"

    def test_complete_task_rejects_invalid_status(self, mock_db_session, sample_tenant):
        """Test that complete_task rejects invalid status values."""
        complete_task_fn = self._get_complete_task_fn()

        with (
            patch("src.core.main.get_principal_from_context") as mock_get_principal,
            patch("src.core.main.set_current_tenant"),
        ):

            mock_get_principal.return_value = ("principal_123", sample_tenant)

            with pytest.raises(ValueError, match="Invalid status"):
                complete_task_fn(task_id="step_123", status="invalid_status", context=Mock())

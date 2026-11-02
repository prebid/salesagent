"""Tests for task management MCP tools (list_tasks, get_task_status, complete_task).

These tests verify that the task management tools work correctly.
Issue #816 revealed that list_tasks was broken but had no test coverage.
"""

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest
from adcp.types.generated_poc.protocol.list_tasks_request import Filters as ListTasksFilters

from src.core.database.models import WorkflowStep
from src.core.exceptions import AdCPTaskNotFoundError
from tests.factories.principal import PrincipalFactory


def _impl_caller(tool_name: str):
    """Call ``tool_name``'s implementation with a request built from loose kwargs.

    These tests grade what the IMPL returns -- the spec flags that decide whether ``result``
    and ``history`` appear, and the typed shape of each -- so they call it directly and
    assert on the returned model. Going through the MCP boundary would hand back a
    ToolResult, whose structured_content is JSON: every ``entry.type.value`` and
    ``result.root.media_buy_id`` here would become dict indexing, grading serialization
    instead of the flag logic these tests are about. The tool is still reached through the
    registry, so the DTO and the impl are the registered ones.
    """
    from src.core.tools.registry import TOOLS

    spec = TOOLS[tool_name]

    async def call(identity=None, **kwargs):
        return await spec.impl(req=spec.dto(**kwargs), identity=identity)

    return call


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
        # Set explicitly because production READS it: updated_at falls back to created_at
        # when the step is unfinished. An unset Mock attribute returns a Mock, which pydantic
        # rejects -- and the MOCK is what has to be right, never a widened production type.
        step.completed_at = None
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        return step

    async def _get_list_tasks_fn(self):
        """The registered implementation for list_tasks, called with a registry-built request."""
        return _impl_caller("list_tasks")

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
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

        assert result.tasks != []
        # ATTRIBUTE access, not subscripting: list_tasks returns the pinned ListTasksResponse,
        # and the tenant-wide count lives where the pin puts it (query_summary.total_matching
        # / returned), not at the envelope root.
        assert result.query_summary.total_matching == 1
        assert result.query_summary.returned == 1

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
            # The SPEC vocabulary now: list-tasks-request.json filters on TaskStatus, and
            # "input-required" is the spec's name for what this repo stores as
            # "requires_approval". The tool translates; the buyer never sends our word.
            result = await list_tasks_fn(filters=ListTasksFilters(status="input-required"), identity=identity)

        assert result.tasks is not None
        mock_workflow_repo.count_by_tenant.assert_called_once_with(
            status="requires_approval",
            object_type=None,
            object_id=None,
            # list_tasks scopes to the caller now (#1808); it listed the
            # whole tenant, so every buyer saw every other buyer's tasks.
            principal_id="principal_123",
        )


class TestGetTaskTool:
    """Test the get_task_status MCP tool actually works."""

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
        # Set explicitly because production READS it: updated_at falls back to created_at
        # when the step is unfinished. An unset Mock attribute returns a Mock, which pydantic
        # rejects -- and the MOCK is what has to be right, never a widened production type.
        step.completed_at = None
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        step.transaction_details = None
        return step

    async def _get_get_task_fn(self):
        """The registered implementation for get_task_status, called with a registry-built request."""
        return _impl_caller("get_task_status")

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
        )

    async def test_get_task_returns_task_details(
        self, mock_uow, mock_workflow_repo, sample_tenant, sample_workflow_step
    ):
        """Test that get_task_status returns task details correctly."""
        get_task_fn = await self._get_get_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.return_value = sample_workflow_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await get_task_fn(task_id="step_123", identity=identity)

        assert result.task_id == "step_123"
        # The SPEC vocabulary, not ours. requires_approval is this repo's workflow status;
        # enums/task-status.json spells the same state "input-required".
        assert result.status.value == "input-required"
        assert result.task_type.value == "create_media_buy"
        assert result.protocol.value == "media-buy"

    async def test_get_task_not_found_raises_error(self, mock_uow, mock_workflow_repo, sample_tenant):
        """get_task_status raises the TYPED error when the task is not found.

        The typed class, not ToolError: this calls the impl, and translating
        AdCPTaskNotFoundError into a transport envelope is the boundary's job, graded at the
        boundary (tests/unit/test_error_boundary_translation.py). Asserting ToolError here
        would grade the translator through a test about the lookup.
        """
        get_task_fn = await self._get_get_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.side_effect = AdCPTaskNotFoundError()

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            with pytest.raises(AdCPTaskNotFoundError):
                await get_task_fn(task_id="nonexistent", identity=identity)


class TestGetTaskSpecFlags:
    """get_task_status honours the four fields protocol/get-task-status-status-request.json declares.

    Grounded at the PIN (AdCP 3.1.1 via adcp 6.6.0):
      * ``include_result`` is GRADED — domains/media-buy/scenarios/get_products_async.yaml,
        step ``get_products_task_status_completed``, which polls with include_result: true
        and asserts ``result.*``. The request schema says the payload is "Present when
        status is 'completed' and include_result was true in the request; absent otherwise",
        and task-lifecycle.mdx says "Send include_result: true to receive the terminal task
        payload ... once the task reaches status: completed".
      * ``include_history`` is UNGRADED — zero occurrences in the whole 3.1.1 compliance
        tree and zero in the prose. It exists only in the request schema and as the
        response's ``history`` array, whose items are {timestamp, type, data}. Nothing
        grades it, so what it must do is exactly what the response schema's item shape says
        and no more. It is implemented rather than refused because the material is real:
        a workflow step records the request that opened it and the response that closed it.
      * ``ext`` is shape only.
      * ``account`` is graded and behavioural; its own class covers it.

    These are behaviour tests, not declaration tests, and there is deliberately no
    declaration test to point at: the DTO is the SDK's model minus a declared omission, so
    what it declares is inherited rather than compared (docs/development/building-tools.md).
    Declaring a flag without honouring it is the accept-and-ignore this class exists to
    prevent, and only a behaviour test can see it.
    """

    @pytest.fixture
    def mock_workflow_repo(self):
        return MagicMock()

    @pytest.fixture
    def mock_uow(self, mock_workflow_repo):
        uow = MagicMock()
        uow.__enter__ = Mock(return_value=uow)
        uow.__exit__ = Mock(return_value=None)
        uow.workflows = mock_workflow_repo
        return uow

    @pytest.fixture
    def completed_step(self):
        step = Mock(spec=WorkflowStep)
        step.step_id = "step_done"
        step.context_id = "ctx_done"
        step.status = "completed"
        step.step_type = "tool_call"
        step.tool_name = "create_media_buy"
        step.owner = "principal"
        step.created_at = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)
        # Set explicitly because production READS it: updated_at falls back to created_at
        # when the step is unfinished. An unset Mock attribute returns a Mock, which pydantic
        # rejects -- and the MOCK is what has to be right, never a widened production type.
        step.completed_at = None
        step.completed_at = datetime(2026, 3, 1, 9, 5, 0, tzinfo=UTC)
        step.request_data = {"packages": [{"product_id": "prod_1"}]}
        step.response_data = {"media_buy_id": "mb_1", "packages": []}
        step.error_message = None
        step.comments = []
        step.transaction_details = None
        return step

    async def _call(self, mock_uow, identity, **kwargs):
        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            return await _impl_caller("get_task_status")(identity=identity, **kwargs)

    def _identity(self):
        from tests.factories.principal import PrincipalFactory

        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "name": "Test Tenant"},
        )

    async def test_result_absent_by_default(self, mock_uow, mock_workflow_repo, completed_step):
        """A status-only poll carries no terminal payload, which is what the default buys.

        The pin defaults include_result to false "for lightweight status-only polls". Before
        this, the terminal payload rode on EVERY get_task_status response, so the flag had nothing
        to switch and a status poll shipped the whole result.
        """
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done")

        assert result.status.value == "completed"
        assert result.result is None

    async def test_result_present_when_requested_and_completed(self, mock_uow, mock_workflow_repo, completed_step):
        """include_result=true on a completed task returns the terminal payload as `result`."""
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done", include_result=True)

        # Validated into the pinned async-result union, so the concrete member carries its own
        # envelope defaults; what this asserts is that the STORED payload survived into it.
        assert result.result.root.media_buy_id == "mb_1"
        assert result.result.root.packages == []

    async def test_result_withheld_while_not_completed(self, mock_uow, mock_workflow_repo, completed_step):
        """Asking for the result of an unfinished task returns none — "when status is completed".

        Not an error: the buyer is polling precisely because they do not know yet. The
        payload simply is not owed until the task reaches completed.
        """
        completed_step.status = "requires_approval"
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done", include_result=True)

        assert result.result is None

    async def test_history_absent_by_default(self, mock_uow, mock_workflow_repo, completed_step):
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done")

        assert result.history is None

    async def test_history_carries_the_request_and_response_exchanges(
        self, mock_uow, mock_workflow_repo, completed_step
    ):
        """include_history=true returns the exchanges in the response schema's item shape.

        {timestamp, type: request|response, data} per get-task-status-status-response.json. The
        request entry is stamped with the step's created_at and the response entry with its
        completed_at, because those ARE when each exchange happened.
        """
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done", include_history=True)

        assert [entry.model_dump(mode="json") for entry in result.history] == [
            {
                "timestamp": "2026-03-01T09:00:00Z",
                "type": "request",
                "data": {"packages": [{"product_id": "prod_1"}]},
            },
            {
                "timestamp": "2026-03-01T09:05:00Z",
                "type": "response",
                "data": {"media_buy_id": "mb_1", "packages": []},
            },
        ]

    async def test_history_omits_an_exchange_that_has_not_happened(self, mock_uow, mock_workflow_repo, completed_step):
        """A task still awaiting approval has a request and no response yet.

        Emitting a response entry with a null timestamp would satisfy the array while
        describing an exchange that never occurred; the item's own schema makes timestamp,
        type and data all required.
        """
        completed_step.status = "requires_approval"
        completed_step.completed_at = None
        completed_step.response_data = None
        mock_workflow_repo.get_by_step_id_or_raise.return_value = completed_step
        mock_workflow_repo.get_mappings_for_step.return_value = []

        result = await self._call(mock_uow, self._identity(), task_id="step_done", include_history=True)

        assert [entry.type.value for entry in result.history] == ["request"]


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
        # Set explicitly because production READS it: updated_at falls back to created_at
        # when the step is unfinished. An unset Mock attribute returns a Mock, which pydantic
        # rejects -- and the MOCK is what has to be right, never a widened production type.
        step.completed_at = None
        step.completed_at = None
        step.request_data = {"budget": 5000}
        step.response_data = None
        step.error_message = None
        step.comments = []
        return step

    async def _get_complete_task_fn(self):
        """The registered implementation for complete_task, called with a registry-built request."""
        return _impl_caller("complete_task")

    def _make_identity(self, sample_tenant):
        """Create a ResolvedIdentity for testing."""
        return PrincipalFactory.make_identity(
            principal_id="principal_123",
            tenant_id=sample_tenant["tenant_id"],
            tenant=sample_tenant,
        )

    async def test_complete_task_updates_status(self, mock_uow, mock_workflow_repo, sample_tenant, sample_pending_step):
        """Test that complete_task updates task status."""
        complete_task_fn = await self._get_complete_task_fn()

        mock_workflow_repo.get_by_step_id_or_raise.return_value = sample_pending_step
        mock_workflow_repo.update_status.return_value = sample_pending_step

        identity = self._make_identity(sample_tenant)

        with patch("src.core.tools.task_management.WorkflowUoW", return_value=mock_uow):
            result = await complete_task_fn(task_id="step_123", status="completed", identity=identity)

        assert result.status == "completed"
        assert result.task_id == "step_123"
        mock_workflow_repo.update_status.assert_called_once_with(
            "step_123",
            status="completed",
            completed_at=ANY,
            response_data={"manually_completed": True, "completed_by": "principal_123"},
        )

    async def test_complete_task_rejects_invalid_status(self, mock_uow, mock_workflow_repo, sample_tenant):
        """complete_task rejects an invalid status -- at the MODEL, which is where it is declared.

        CompleteTaskRequest types status as a required ``Literal["completed", "failed"]``, so
        the request cannot be built with anything else. The impl used to re-raise
        AdCPValidationError for the same values while the DTO advertised a free optional
        string; the rejection now happens where buyers are told about it, and this asserts the
        field it names.
        """
        from pydantic import ValidationError

        complete_task_fn = await self._get_complete_task_fn()

        identity = self._make_identity(sample_tenant)

        with pytest.raises(ValidationError) as exc_info:
            await complete_task_fn(task_id="step_123", status="invalid_status", identity=identity)

        assert exc_info.value.errors()[0]["loc"] == ("status",)

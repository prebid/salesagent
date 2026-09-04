"""TaskEnv + TaskManagementEnv — integration environments for task tools.

TaskEnv covers get_task / complete_task (MCP+A2A; no REST in AdCP 3.1.1).
TaskManagementEnv covers list_tasks (MCP-only).

Requires: integration_db fixture.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from tests.harness._base import IntegrationEnv
from tests.harness.transport import DeliverResult
from tests.utils.workflow_task_seed import create_principal_owned_workflow_step


class GetTaskWireResponse(BaseModel):
    """Full success-path wire shape for get_task — every returned field declared.

    ``extra="allow"`` with only 3 of ~12 fields would let a leaked or dropped
    field pass silently on the payload this PR gates access to; this model
    mirrors the full ``task_detail`` dict built in
    ``src.core.tools.task_management.get_task`` so a shape drift reddens.
    ``extra="forbid"`` (no default "ignore") so a leaked field reddens too,
    not just a dropped one. No ``= None`` defaults on nullable fields —
    production emits every key unconditionally, so absence must redden.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    context_id: str | None
    status: str
    type: str
    tool_name: str
    owner: str
    created_at: str
    updated_at: str | None
    request_data: dict[str, Any] | None
    response_data: dict[str, Any] | None
    error_message: str | None
    associated_objects: list[dict[str, Any]]


class CompleteTaskWireResponse(BaseModel):
    """Full success-path wire shape for complete_task — every returned field declared.

    ``extra="forbid"`` so a leaked field reddens too, not just a dropped one.
    No ``= None`` defaults: production emits every key unconditionally, so
    absence must redden (drop half of the forbid claim).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    message: str | None
    completed_at: str | None
    completed_by: str | None


class TaskEnv(IntegrationEnv):
    """Integration environment for AdCP get_task / complete_task tools.

    Kwargs:
        tool: ``"get_task"`` (default) or ``"complete_task"``
        task_id: workflow step id
        status / response_data / error_message: complete_task only
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    # No REST_ENDPOINT — AdCP 3.1.1 binds these ops to MCP+A2A only (no REST binding).

    def setup_owner_principal(self, *, principal_id: str, tenant_id: str | None = None):
        """Tenant + owner principal bootstrap shared by wire tests and BDD Givens.

        Creates ``TenantFactory`` + ``PrincipalFactory`` with a mock platform
        mapping and commits. Returns the tenant ORM instance for sibling seeding.
        """
        from tests.factories import PrincipalFactory, TenantFactory

        tid = tenant_id or self._tenant_id
        if tenant_id is not None and tenant_id != self._tenant_id:
            self.switch_tenant(tenant_id)
            tid = tenant_id
        tenant = TenantFactory(tenant_id=tid)
        PrincipalFactory(
            tenant=tenant,
            principal_id=principal_id,
            platform_mappings={"mock": {"id": f"{principal_id}_adv"}},
        )
        self._commit_factory_data()
        return tenant

    def seed_owner_task(self, *, principal_id: str, status: str = "completed") -> str:
        """Seed a durable workflow step owned by ``principal_id``; return step_id."""
        self._commit_factory_data()
        step = create_principal_owned_workflow_step(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            status=status,
        )
        self._commit_factory_data()
        return step.step_id

    def _pop_tool(self, kwargs: dict[str, Any]) -> str:
        tool = kwargs.pop("tool", "get_task")
        if tool not in ("get_task", "complete_task"):
            raise ValueError(f"Unsupported task tool {tool!r}; expected get_task or complete_task")
        return tool

    def _response_cls(self, tool: str) -> type[BaseModel]:
        return GetTaskWireResponse if tool == "get_task" else CompleteTaskWireResponse

    def call_impl(self, **kwargs: Any) -> dict[str, Any]:
        """Call get_task / complete_task in-process with real DB."""
        import asyncio

        from src.core.tools.task_management import complete_task, get_task

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        tool = self._pop_tool(kwargs)
        if tool == "get_task":
            return asyncio.run(get_task(identity=identity, **kwargs))
        return asyncio.run(complete_task(identity=identity, **kwargs))

    # JUSTIFIED OVERRIDE — selects get_task vs complete_task from kwargs
    # (``tool=``), so it cannot declare a single MCP_TOOL / A2A_SKILL for the
    # base's client-core delegation. Allowlisted in
    # ``_KNOWN_DELIVER_OVERRIDES``; shrink when a dual-tool declaration exists.
    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_task / complete_task through the real A2A skill pipeline."""
        tool = self._pop_tool(kwargs)
        return self._run_a2a_handler(tool, self._response_cls(tool), **kwargs)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_task / complete_task via FastMCP in-memory Client."""
        tool = self._pop_tool(kwargs)
        return self._run_mcp_client(tool, self._response_cls(tool), **kwargs)


class TaskManagementEnv(IntegrationEnv):
    """Integration test environment for list_tasks.

    No patches -- list_tasks reads real WorkflowStep rows via WorkflowUoW.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a, and
    # this env now JOINS the client core — production's list_tasks emits the
    # pinned-required query_summary + pagination, so the core's pinned parse
    # succeeds. list_tasks is MCP-only (no A2A skill, no REST route).
    MCP_TOOL = "list_tasks"
    RESPONSE_MODEL = dict

    EXTERNAL_PATCHES: dict[str, str] = {}

    def _configure_mocks(self) -> None:
        """No mocks needed -- real WorkflowUoW."""

    def call_impl(self, **kwargs: Any) -> dict[str, Any]:
        """Call list_tasks directly with real DB (no transport dispatch)."""
        import asyncio

        from src.core.tools.task_management import list_tasks

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        return asyncio.run(list_tasks(identity=identity, **kwargs))

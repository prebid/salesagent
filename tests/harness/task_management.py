"""TaskManagementEnv -- integration test environment for list_tasks.

list_tasks is an MCP-only surface: no A2A raw wrapper (the A2A task
polling handlers ``on_get_task``/``on_list_tasks`` are a separate, native
A2A task-lifecycle concept, not a caller of this module) and no REST route.
``call_a2a``/``call_rest`` are intentionally left unimplemented (base class
default raises ``NotImplementedError``).

Requires: integration_db fixture.
"""

from __future__ import annotations

from tests.harness._base import IntegrationEnv


class TaskManagementEnv(IntegrationEnv):
    """Integration test environment for list_tasks.

    No patches -- list_tasks reads real WorkflowStep rows via WorkflowUoW.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a and this env DELEGATES to the
    # client core. It kept a deliver_mcp override under FIXME(#2201) because production's
    # list_tasks wire omitted the pinned-required query_summary and pagination, so the
    # core's parse-back raised. #2201 landed; the response extends the pinned
    # ListTasksResponse now, the parse succeeds, and the override and its
    # _KNOWN_DELIVER_OVERRIDES row are both gone. list_tasks is MCP-only (no A2A skill,
    # no REST route).
    MCP_TOOL = "list_tasks"
    RESPONSE_MODEL = dict

    EXTERNAL_PATCHES: dict[str, str] = {}

    def _configure_mocks(self) -> None:
        """No mocks needed -- real WorkflowUoW."""

    # No ``call_impl``. There was one, reaching ``_list_tasks_impl`` directly, and it had
    # NO CALLER: the one live consumer (tests/integration/test_get_task_principal_scope.py)
    # dispatches through ``env.call_via(Transport.MCP, ...)``, and the BDD step that looked
    # like a caller bypassed this env entirely. #1721 deleted ``Transport.IMPL``, so an
    # impl leg on a harness env is a route to nowhere -- kept alive only by the dangling
    # ``list_tasks`` import it carried. Deleted rather than repaired.

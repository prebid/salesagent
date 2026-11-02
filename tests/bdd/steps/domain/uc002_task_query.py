"""BDD step definitions for UC-002: Task list query partition/boundary.

Given steps configure sort_field / sort_direction / domain / task_status
in ctx["task_query_params"]. When step passes ALL params to list_tasks().
If production rejects a param (TypeError), Then steps xfail with the real
error as proof of the SPEC-PRODUCTION GAP.

"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, when

from src.core.exceptions import AdCPSalesAgentError

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _ensure_task_query_params(ctx: dict) -> dict[str, Any]:
    """Initialize and return ctx['task_query_params']."""
    return ctx.setdefault("task_query_params", {})


def _parse_array_value(raw: str, separator: str = "+") -> list[str]:
    """Parse 'a+b+c' boundary config into a list."""
    return [v.strip() for v in raw.split(separator) if v.strip()]


# _xfail_on_unsupported_param is DELETED. It excused list_tasks() for raising TypeError on
# sort_field / sort_direction / domain / task_status / task_type — and the pinned 3.1
# protocol/list-tasks-request.json declares NONE of those as parameters. It nests them:
# ``sort`` is {field, direction} and filtering lives under ``filters``. So production was
# REFUSING A NON-SPEC PARAMETER SHAPE, which is correct behaviour, and the xfail recorded
# that correctness as a production gap.
#
# The `assert "error" not in ctx` that followed every call site is now the grader. If these
# scenarios are ever wired, it fails with the TypeError and says the true thing: the steps
# call list_tasks with a parameter shape the spec does not define. Fixing THAT is a scenario
# change (pass sort={"field":..,"direction":..}), not an excuse.


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — sort field / direction configuration
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the task list sort field is {partition}"))
def given_sort_field_partition(ctx: dict, partition: str) -> None:
    """Set sort_field for a partition scenario."""
    params = _ensure_task_query_params(ctx)
    if partition != "omitted":
        params["sort_field"] = partition


@given(parsers.parse("the sort direction is {partition}"))
def given_sort_direction_partition(ctx: dict, partition: str) -> None:
    """Set sort_direction for a partition scenario."""
    params = _ensure_task_query_params(ctx)
    if partition != "omitted":
        params["sort_direction"] = partition


@given(parsers.parse("the task list sort field boundary is: {config}"))
def given_sort_field_boundary(ctx: dict, config: str) -> None:
    """Set sort_field for a boundary scenario."""
    params = _ensure_task_query_params(ctx)
    if config != "omitted":
        params["sort_field"] = config


@given(parsers.parse("the sort direction boundary is: {config}"))
def given_sort_direction_boundary(ctx: dict, config: str) -> None:
    """Set sort_direction for a boundary scenario."""
    params = _ensure_task_query_params(ctx)
    if config != "omitted":
        params["sort_direction"] = config


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — domain filter configuration
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the domain filter is {partition}"))
def given_domain_filter_partition(ctx: dict, partition: str) -> None:
    """Set domain filter for a partition scenario."""
    params = _ensure_task_query_params(ctx)
    if partition == "omitted":
        return
    if partition == "domain_array":
        params["domain"] = ["media_buy", "signals"]
    elif partition == "empty_array":
        params["domain"] = []
    else:
        params["domain"] = partition


@given(parsers.parse("the domain filter boundary is: {config}"))
def given_domain_filter_boundary(ctx: dict, config: str) -> None:
    """Set domain filter for a boundary scenario."""
    params = _ensure_task_query_params(ctx)
    if config == "omitted":
        return
    if config == "empty array":
        params["domain"] = []
    elif "+" in config:
        params["domain"] = _parse_array_value(config)
    else:
        params["domain"] = config


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — task status filter configuration
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the task status filter is {partition}"))
def given_task_status_filter_partition(ctx: dict, partition: str) -> None:
    """Set task_status filter for a partition scenario."""
    params = _ensure_task_query_params(ctx)
    if partition == "omitted":
        return
    if partition == "status_array":
        params["task_status"] = ["submitted", "working"]
    elif partition == "empty_array":
        params["task_status"] = []
    else:
        params["task_status"] = partition


@given(parsers.parse("the task status filter boundary is: {config}"))
def given_task_status_filter_boundary(ctx: dict, config: str) -> None:
    """Set task_status filter for a boundary scenario."""
    params = _ensure_task_query_params(ctx)
    if config == "omitted":
        return
    if config == "empty array":
        params["task_status"] = []
    elif "+" in config:
        params["task_status"] = _parse_array_value(config)
    else:
        params["task_status"] = config


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — task type filter configuration
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the task type filter is {partition}"))
def given_task_type_filter_partition(ctx: dict, partition: str) -> None:
    """Set task_type filter for a partition scenario."""
    params = _ensure_task_query_params(ctx)
    if partition == "omitted":
        return
    if partition == "task_type_array":
        params["task_type"] = ["create_media_buy", "update_media_buy"]
    elif partition == "empty_array":
        params["task_type"] = []
    else:
        params["task_type"] = partition


@given(parsers.parse("the task type filter boundary is: {config}"))
def given_task_type_filter_boundary(ctx: dict, config: str) -> None:
    """Set task_type filter for a boundary scenario."""
    params = _ensure_task_query_params(ctx)
    if config == "omitted":
        return
    if config == "empty array":
        params["task_type"] = []
    elif "+" in config:
        params["task_type"] = _parse_array_value(config)
    else:
        params["task_type"] = config


# ═══════════════════════════════════════════════════════════════════════
# WHEN step — query task list
# ═══════════════════════════════════════════════════════════════════════


def _dispatch_list_tasks(ctx: dict, **params: Any) -> Any:
    """Dispatch list_tasks through the TRANSPORT, and return what the buyer received.

    ``dispatch_request`` -> ``env.call_via`` -> ``ctx["transport"]``, never the ``_impl``.
    It briefly did call ``_list_tasks_impl`` directly, and that is worse than the dangling
    ``list_tasks`` name it replaced: a direct impl call runs the same code whatever
    ``ctx["transport"]`` says, so every parametrized leg of these scenarios silently
    collapsed onto one and the wire contract stopped being graded at all. That is exactly
    what ``test_architecture_bdd_no_direct_call_impl`` exists to refuse, and it caught it.

    ``list_tasks`` is MCP-only (``TaskManagementEnv.MCP_TOOL``; no A2A skill, no REST
    route), so the transport set these scenarios run is narrow -- but narrow because the
    SURFACE is narrow, which the env declares, not because the step reached past it.
    """
    from tests.bdd.steps.generic._dispatch import dispatch_request

    ctx["env"]._commit_factory_data()
    dispatch_request(ctx, **params)
    if "error" in ctx:
        raise ctx["error"] if isinstance(ctx["error"], BaseException) else AssertionError(ctx["error"])
    return ctx["result"]


def _dispatch_list_tasks_e2e(ctx: dict, **params: Any) -> dict:
    """Query workflow_steps directly through the harness DB session for E2E mode.

    In E2E mode, list_tasks() would call get_db_session() which connects to
    the empty test DB, not Docker's DB. Instead, we query workflow_steps
    through the harness session (bound to Docker's DB).

    Unsupported params (sort_field, domain, task_status, task_type) raise
    TypeError to preserve the same xfail behavior as in-process mode.
    """
    # Replicate list_tasks() parameter validation: raise TypeError for
    # unsupported params (same behavior as calling list_tasks() in-process).
    _supported = {"status", "object_type", "object_id", "limit", "offset"}
    unsupported = set(params.keys()) - _supported
    if unsupported:
        raise TypeError(f"list_tasks() got an unexpected keyword argument '{next(iter(unsupported))}'")

    env = ctx["env"]
    env._commit_factory_data()

    # Use harness to get workflow steps (correct DB in both modes)
    from src.core.database.repositories.workflow import WorkflowRepository

    repo = WorkflowRepository(env._session, env.identity.tenant_id)
    steps = repo.list_by_tenant(
        status=params.get("status"),
        object_type=params.get("object_type"),
        object_id=params.get("object_id"),
        offset=params.get("offset", 0),
        limit=params.get("limit", 20),
    )
    # Return a dict matching list_tasks() response shape
    return {
        "tasks": [
            {
                "step_id": s.step_id,
                "status": s.status,
                "operation": s.operation,
                "created_at": str(s.created_at) if s.created_at else None,
                "updated_at": str(s.updated_at) if s.updated_at else None,
            }
            for s in steps
        ],
        "total": len(steps),
    }


@when("the Buyer Agent queries the task list")
def when_query_task_list(ctx: dict) -> None:
    """Call list_tasks with ALL configured params.

    Passes every param from ctx["task_query_params"] to list_tasks().
    If production doesn't accept a param (e.g. sort_field, domain),
    the resulting TypeError is stored in ctx["error"] — the real
    production gap surfaces at call time, not via pre-filtering.

    Transport-aware: in E2E mode, queries workflow_steps directly through
    the harness DB session to avoid hitting the wrong database.
    """
    from tests.bdd.steps._outcome_helpers import is_e2e

    env = ctx["env"]
    params = ctx.get("task_query_params", {})

    try:
        if is_e2e(ctx):
            result = _dispatch_list_tasks_e2e(ctx, **params)
        else:
            result = _dispatch_list_tasks(ctx, **params)
        ctx["task_list_result"] = result
    except (AdCPSalesAgentError, TypeError, Exception) as exc:
        ctx["error"] = exc


# ═══════════════════════════════════════════════════════════════════════
# THEN outcome helpers — task query assertions
# ═══════════════════════════════════════════════════════════════════════


def assert_task_query_outcome(ctx: dict, outcome: str) -> None:
    """Assert task query outcomes for sort/domain/status scenarios.

    Called from the then_result_should_be dispatcher in uc002_create_media_buy.py.
    """
    if outcome.startswith("tasks sorted by "):
        _assert_sorted_by(ctx, outcome)
    elif outcome.startswith("defaults to "):
        _assert_default_sort(ctx, outcome)
    elif outcome.startswith("results in "):
        _assert_sort_direction(ctx, outcome)
    elif outcome.startswith("tasks filtered to "):
        _assert_filtered_to(ctx, outcome)
    elif outcome in (
        "tasks from all domains returned",
        "tasks of all statuses returned",
        "tasks of all types returned",
    ):
        _assert_all_returned(ctx, outcome)
    else:
        raise ValueError(f"Unknown task query outcome: {outcome}")


def _assert_sorted_by(ctx: dict, outcome: str) -> None:
    """Assert tasks are sorted by a specific field.

    The pin nests sorting: protocol/list-tasks-request.json declares
    ``sort: {field, direction}``, not a flat ``sort_field``. A TypeError here means
    the STEP is sending a shape the spec does not define.
    """
    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_default_sort(ctx: dict, outcome: str) -> None:
    """Assert default sort behavior.

    'defaults to created_at sort' — production already does this (hardcoded).
    'defaults to desc order' — production already does this (hardcoded).
    """
    if outcome == "defaults to created_at sort":
        assert "error" not in ctx, f"Expected default sort but got error: {ctx.get('error')}"
        result = ctx.get("task_list_result")
        assert result is not None, "No task list result"
    elif outcome == "defaults to desc order":
        assert "error" not in ctx, f"Expected default order but got error: {ctx.get('error')}"
        result = ctx.get("task_list_result")
        assert result is not None, "No task list result"
    else:
        raise ValueError(f"Unknown default sort outcome: {outcome}")


def _assert_sort_direction(ctx: dict, outcome: str) -> None:
    """Assert results are in ascending or descending order.

    The pin nests direction under ``sort.direction`` (enums/sort-direction.json), not a
    flat ``sort_direction``. A TypeError here means the STEP sends a non-spec shape.
    """
    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_filtered_to(ctx: dict, outcome: str) -> None:
    """Assert tasks are filtered to a specific domain, status, or type.

    The pin nests these under ``filters`` (protocol/list-tasks-request.json); there are no
    flat ``domain`` / ``task_status`` / ``task_type`` parameters. A TypeError here means the
    STEP sends a shape the spec does not define.
    """
    assert "error" not in ctx, f"Expected filtered results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_all_returned(ctx: dict, outcome: str) -> None:
    """Assert all tasks returned when filter is omitted.

    When no filter is specified, list_tasks() returns all tasks for the tenant — this is
    the production default behavior, and the pin agrees (``filters`` is optional).
    """
    assert "error" not in ctx, f"Expected all tasks but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"

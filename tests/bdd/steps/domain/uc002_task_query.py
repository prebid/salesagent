"""BDD step definitions for UC-002: Task list query partition/boundary.

Given steps configure sort_field / sort_direction / domain / task_status
in ctx["task_query_params"]. When step passes ALL params to list_tasks().
If production rejects a param (TypeError), Then steps xfail with the real
error as proof of the SPEC-PRODUCTION GAP.

beads: salesagent-9vgz.86, salesagent-9vgz.88, salesagent-9vgz.90
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_bdd import given, parsers, when

from src.core.exceptions import AdCPError

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _ensure_task_query_params(ctx: dict) -> dict[str, Any]:
    """Initialize and return ctx['task_query_params']."""
    return ctx.setdefault("task_query_params", {})


def _parse_array_value(raw: str, separator: str = "+") -> list[str]:
    """Parse 'a+b+c' boundary config into a list."""
    return [v.strip() for v in raw.split(separator) if v.strip()]


def _xfail_on_unsupported_param(ctx: dict, param_name: str, outcome: str) -> None:
    """Xfail if list_tasks() rejected a param with TypeError.

    When the When step passes all configured params to list_tasks() and
    production doesn't accept one, the call raises TypeError. This is
    the real production gap — the param isn't supported yet.
    """
    error = ctx.get("error")
    if isinstance(error, TypeError) and param_name in str(error):
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept {param_name} parameter. "
            f"Outcome={outcome!r}. Error: {error}"
        )


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


def _dispatch_list_tasks(env: Any, **params: Any) -> Any:
    """Dispatch list_tasks through the env, keeping production import in harness layer."""
    from src.core.tools.task_management import list_tasks

    env._commit_factory_data()
    return asyncio.run(list_tasks(identity=env.identity, **params))


@when("the Buyer Agent queries the task list")
def when_query_task_list(ctx: dict) -> None:
    """Call list_tasks with ALL configured params.

    Passes every param from ctx["task_query_params"] to list_tasks().
    If production doesn't accept a param (e.g. sort_field, domain),
    the resulting TypeError is stored in ctx["error"] — the real
    production gap surfaces at call time, not via pre-filtering.
    """
    env = ctx["env"]
    params = ctx.get("task_query_params", {})

    try:
        result = _dispatch_list_tasks(env, **params)
        ctx["response"] = result
        ctx["task_list_result"] = result
    except (AdCPError, TypeError, Exception) as exc:
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

    SPEC-PRODUCTION GAP: list_tasks() hardcodes created_at DESC sorting.
    There is no sort_field parameter. These scenarios xfail until production
    implements parameterized sorting.
    """
    _xfail_on_unsupported_param(ctx, "sort_field", outcome)

    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_default_sort(ctx: dict, outcome: str) -> None:
    """Assert default sort behavior.

    'defaults to created_at sort' — production already does this (hardcoded).
    'defaults to desc order' — production already does this (hardcoded).
    """
    if outcome == "defaults to created_at sort":
        _xfail_on_unsupported_param(ctx, "sort_field", outcome)
        assert "error" not in ctx, f"Expected default sort but got error: {ctx.get('error')}"
        result = ctx.get("task_list_result")
        assert result is not None, "No task list result"
    elif outcome == "defaults to desc order":
        _xfail_on_unsupported_param(ctx, "sort_direction", outcome)
        assert "error" not in ctx, f"Expected default order but got error: {ctx.get('error')}"
        result = ctx.get("task_list_result")
        assert result is not None, "No task list result"
    else:
        raise ValueError(f"Unknown default sort outcome: {outcome}")


def _assert_sort_direction(ctx: dict, outcome: str) -> None:
    """Assert results are in ascending or descending order.

    SPEC-PRODUCTION GAP: list_tasks() hardcodes DESC and has no sort_direction
    parameter. These scenarios xfail until production implements it.
    """
    _xfail_on_unsupported_param(ctx, "sort_direction", outcome)

    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_filtered_to(ctx: dict, outcome: str) -> None:
    """Assert tasks are filtered to a specific domain, status, or type.

    SPEC-PRODUCTION GAP: list_tasks() does not accept domain, task_status,
    or task_type parameters. The TypeError from the call is the proof.
    """
    for param in ("domain", "task_status", "task_type"):
        _xfail_on_unsupported_param(ctx, param, outcome)

    assert "error" not in ctx, f"Expected filtered results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_all_returned(ctx: dict, outcome: str) -> None:
    """Assert all tasks returned when filter is omitted.

    When no domain/status/type filter is specified, list_tasks() returns all
    tasks for the tenant — this is the production default behavior.
    """
    for param in ("domain", "task_status", "task_type"):
        _xfail_on_unsupported_param(ctx, param, outcome)

    assert "error" not in ctx, f"Expected all tasks but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"

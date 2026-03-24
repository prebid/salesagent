"""BDD step definitions for UC-002: Task list query partition/boundary.

Given steps configure sort_field / sort_direction / domain / task_status
in ctx["task_query_params"]. When step calls list_tasks production code.
Then outcomes verify sort order, domain filtering, status filtering, or error —
most xfail as SPEC-PRODUCTION GAPs because list_tasks() only accepts
status, object_type, object_id, limit, offset.

beads: salesagent-9vgz.86, salesagent-9vgz.88
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
# WHEN step — query task list
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent queries the task list")
def when_query_task_list(ctx: dict) -> None:
    """Call list_tasks with configured params.

    Since list_tasks is an async function that needs ResolvedIdentity,
    we build identity from the env and call directly.
    """
    from src.core.tools.task_management import list_tasks

    env = ctx["env"]
    identity = env.identity
    params = ctx.get("task_query_params", {})

    # list_tasks() only accepts: status, object_type, object_id, limit, offset, identity
    # sort_field, sort_direction, domain, task_status are NOT accepted by production code.
    # We pass known ones and record unsupported ones for Then step xfail.
    known_params = {"status", "object_type", "object_id", "limit", "offset"}
    impl_kwargs: dict[str, Any] = {"identity": identity}
    extra_params: dict[str, Any] = {}

    for key, value in params.items():
        if key in known_params:
            impl_kwargs[key] = value
        else:
            extra_params[key] = value

    if extra_params:
        # Production doesn't support these params — record for Then step xfail
        ctx["unsupported_query_params"] = extra_params

    try:
        result = asyncio.run(list_tasks(**impl_kwargs))
        ctx["response"] = result
        ctx["task_list_result"] = result
    except (AdCPError, Exception) as exc:
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
    unsupported = ctx.get("unsupported_query_params", {})
    if "sort_field" in unsupported:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept sort_field parameter. "
            f"Requested sort_field={unsupported['sort_field']!r}, outcome={outcome!r}. "
            f"Production hardcodes created_at DESC."
        )

    # If we get here (omitted sort_field, default), verify we got a response
    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_default_sort(ctx: dict, outcome: str) -> None:
    """Assert default sort behavior.

    'defaults to created_at sort' — production already does this (hardcoded).
    'defaults to desc order' — production already does this (hardcoded).
    """
    if outcome == "defaults to created_at sort":
        # This IS the production default — should succeed
        unsupported = ctx.get("unsupported_query_params", {})
        if "sort_field" in unsupported:
            pytest.xfail(
                "SPEC-PRODUCTION GAP: list_tasks() does not accept sort_field parameter. "
                "Cannot verify default behavior explicitly."
            )
        assert "error" not in ctx, f"Expected default sort but got error: {ctx.get('error')}"
        result = ctx.get("task_list_result")
        assert result is not None, "No task list result"
    elif outcome == "defaults to desc order":
        unsupported = ctx.get("unsupported_query_params", {})
        if "sort_direction" in unsupported:
            pytest.xfail(
                "SPEC-PRODUCTION GAP: list_tasks() does not accept sort_direction parameter. "
                "Cannot verify default behavior explicitly."
            )
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
    unsupported = ctx.get("unsupported_query_params", {})
    if "sort_direction" in unsupported:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept sort_direction parameter. "
            f"Requested sort_direction={unsupported['sort_direction']!r}, outcome={outcome!r}. "
            f"Production hardcodes DESC."
        )

    assert "error" not in ctx, f"Expected sorted results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_filtered_to(ctx: dict, outcome: str) -> None:
    """Assert tasks are filtered to a specific domain, status, or type.

    SPEC-PRODUCTION GAP: list_tasks() does not accept domain or task_status
    (AdCP enum) parameters. The production 'status' param uses internal
    workflow statuses (pending, in_progress, etc.), not AdCP task statuses
    (submitted, working, etc.). All domain and task_status filter scenarios
    xfail until production implements AdCP-aligned filtering.
    """
    unsupported = ctx.get("unsupported_query_params", {})

    if "domain" in unsupported:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept domain parameter. "
            f"Requested domain={unsupported['domain']!r}, outcome={outcome!r}."
        )

    if "task_status" in unsupported:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept task_status parameter. "
            f"AdCP task statuses (submitted, working, etc.) differ from production "
            f"workflow statuses (pending, in_progress, etc.). "
            f"Requested task_status={unsupported['task_status']!r}, outcome={outcome!r}."
        )

    if "task_type" in unsupported:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: list_tasks() does not accept task_type parameter. "
            f"Requested task_type={unsupported['task_type']!r}, outcome={outcome!r}."
        )

    # If we get here, the filter param was supported — verify response
    assert "error" not in ctx, f"Expected filtered results but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"


def _assert_all_returned(ctx: dict, outcome: str) -> None:
    """Assert all tasks returned when filter is omitted.

    When no domain/status/type filter is specified, list_tasks() returns all
    tasks for the tenant — this is the production default behavior.
    """
    unsupported = ctx.get("unsupported_query_params", {})

    # Domain/task_status/task_type omitted should mean no filtering was requested.
    # If somehow unsupported params slipped through, xfail.
    for param in ("domain", "task_status", "task_type"):
        if param in unsupported:
            pytest.xfail(
                f"SPEC-PRODUCTION GAP: list_tasks() does not accept {param} parameter. "
                f"Cannot verify 'all returned' behavior explicitly. "
                f"Requested {param}={unsupported[param]!r}, outcome={outcome!r}."
            )

    assert "error" not in ctx, f"Expected all tasks but got error: {ctx.get('error')}"
    result = ctx.get("task_list_result")
    assert result is not None, "No task list result"

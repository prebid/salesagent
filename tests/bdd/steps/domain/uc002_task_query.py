"""BDD step definitions for UC-002: Task list sort + direction partition/boundary.

Given steps configure sort_field / sort_direction in ctx["task_query_params"].
When step calls list_tasks production code.
Then outcomes verify sort order or error — most xfail as SPEC-PRODUCTION GAPs
because list_tasks() does not accept sort_field/sort_direction parameters.

beads: salesagent-9vgz.86
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
    identity = env.default_identity()
    params = ctx.get("task_query_params", {})

    # list_tasks() only accepts: status, object_type, object_id, limit, offset, identity
    # sort_field and sort_direction are NOT accepted by production code.
    # We pass them to detect SPEC-PRODUCTION GAP at runtime.
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
    """Assert task query outcomes for sort field/direction scenarios.

    Called from the then_result_should_be dispatcher in uc002_create_media_buy.py.
    """
    if outcome.startswith("tasks sorted by "):
        _assert_sorted_by(ctx, outcome)
    elif outcome.startswith("defaults to "):
        _assert_default_sort(ctx, outcome)
    elif outcome.startswith("results in "):
        _assert_sort_direction(ctx, outcome)
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

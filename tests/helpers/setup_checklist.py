"""Read the setup checklist the two ways production builds it, and hold them to one answer.

``SetupChecklistService`` builds the checklist on two paths: ``get_setup_status()`` for one
tenant with live queries, and ``get_bulk_setup_status()`` for the dashboard, from counts
pre-fetched for many tenants. They assemble their task lists separately, so "the bulk path
grades a task the way the per-tenant path does" is an invariant a test has to check.

This module is the one place that check lives. The bulk path caches what it builds for five
minutes, so a check that skips ``clear_cache()`` can read a stale bulk status and pass
without comparing anything; one copy of the sequence means one place to get that right.
"""

from __future__ import annotations

from typing import Any

from src.services.setup_checklist_service import SetupChecklistService


def checklist_on_both_paths(tenant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The tenant's setup status from the per-tenant path and from the bulk path, uncached.

    Returns ``(single, bulk)`` for a caller that compares more than one task.
    """
    SetupChecklistService.clear_cache()
    single = SetupChecklistService(tenant_id).get_setup_status()
    bulk = SetupChecklistService.get_bulk_setup_status([tenant_id])[tenant_id]
    return single, bulk


def _task(status: dict[str, Any], section: str, key: str, path: str) -> dict[str, Any]:
    tasks = {task["key"]: task for task in status[section]}
    assert key in tasks, f"the {path} path has no {section!r} task {key!r}; it has {sorted(tasks)}"
    return tasks[key]


def checklist_task_on_both_paths(tenant_id: str, section: str, key: str) -> dict[str, Any]:
    """One checklist task, read from both paths; fails unless the two are equal.

    *section* is ``"critical"``, ``"recommended"`` or ``"optional"``. Returns the task as the
    per-tenant path reports it.
    """
    single, bulk = checklist_on_both_paths(tenant_id)
    single_task = _task(single, section, key, "per-tenant")
    bulk_task = _task(bulk, section, key, "bulk")
    assert bulk_task == single_task, f"the dashboard's bulk path grades the {section} task {key!r} differently"
    return single_task

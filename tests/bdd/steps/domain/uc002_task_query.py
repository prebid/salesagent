"""BDD step definitions for UC-002: list_tasks filtering and sorting.

The contract is protocol/list-tasks-request.json at the pin: filtering is nested under
``filters`` (``task_type``/``task_types``, ``status``/``statuses``, ``protocol``/``protocols``,
each plural with ``minItems: 1``) and sorting under ``sort: {field, direction}``, ``field`` in
created_at/updated_at/status/task_type/protocol (default created_at), ``direction`` in
enums/sort-direction.json (default desc).

list_tasks is registered on every transport (``src/core/tools/registry.py``), so the When
dispatches through the transport-generic client, which takes the tool by name: the scenarios
sit on the UC-002 catch-all row, whose env's own verb is create_media_buy. The request goes
as a RAW payload because the refusal rows send values the pinned model cannot hold.

The Given seeds six tasks of the caller's own, through the factories, with distinct
statuses, three task types and creation and completion times arranged so that every sort
field orders them differently. Each seeded task states, as literals, the task_type, status
and protocol the pin says a buyer reads for it; the Thens compare the wire against those, so
production's own vocabulary maps are graded rather than reused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pytest_bdd import given, parsers, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_via_client
from tests.factories import ContextFactory, WorkflowStepFactory


@dataclass(frozen=True)
class _SeededTask:
    """One seeded task: what is stored, and what the pin says a buyer reads for it."""

    tool_name: str
    stored_status: str
    created_at: datetime
    completed_at: datetime | None
    task_type: str
    status: str
    protocol: str

    @property
    def updated_at(self) -> datetime:
        """The last instant the task changed: its completion, else its creation."""
        return self.completed_at or self.created_at

    def sort_key(self, field: str) -> Any:
        """The value ``sort.field`` orders this task by."""
        return getattr(self, field)


def _seed_table(now: datetime) -> list[_SeededTask]:
    """The six tasks. Every status a stored step can carry, three task types, all media-buy.

    By creation the newest is the rejected one; by update it is the completed one; by
    status the first is ``completed``. So a sort that ignores ``sort.field`` puts the wrong
    task first for every field but created_at, and one that ignores ``sort.direction``
    reverses all of them. The statuses and task types differ in their first letter, so their
    order cannot depend on the database collation.
    """

    def ago(minutes: int) -> datetime:
        return now - timedelta(minutes=minutes)

    return [
        _SeededTask("create_media_buy", "pending", ago(600), None, "create_media_buy", "submitted", "media-buy"),
        _SeededTask("update_media_buy", "in_progress", ago(500), None, "update_media_buy", "working", "media-buy"),
        _SeededTask(
            "sync_creatives", "requires_approval", ago(400), None, "sync_creatives", "input-required", "media-buy"
        ),
        _SeededTask("sync_creatives", "completed", ago(300), ago(10), "sync_creatives", "completed", "media-buy"),
        _SeededTask("update_media_buy", "failed", ago(200), ago(150), "update_media_buy", "failed", "media-buy"),
        # No completion time: the admin's reject (operations.py) sets the status and nothing else.
        _SeededTask("create_media_buy", "rejected", ago(100), None, "create_media_buy", "rejected", "media-buy"),
    ]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer Agent owns tasks of several types, statuses and update times")
def given_seeded_tasks(ctx: dict) -> None:
    """Seed the six tasks under the caller's own principal, keyed by their step id.

    Through the env-bound factories, so on e2e_rest the rows land in the live server's
    database. One context owns them all: list_tasks scopes by the context's principal.
    """
    context = ContextFactory(principal=ctx["principal"])
    seeded: dict[str, _SeededTask] = {}
    for task in _seed_table(datetime.now(UTC)):
        step = WorkflowStepFactory(
            context=context,
            tool_name=task.tool_name,
            status=task.stored_status,
            created_at=task.created_at,
            completed_at=task.completed_at,
        )
        seeded[str(step.step_id)] = task
    ctx["seeded_tasks"] = seeded


#: The scenario's name for a filter -> its singular and plural spellings in ``filters``.
_FILTER_FIELDS = {
    "task type": ("task_type", "task_types"),
    "task status": ("status", "statuses"),
    "protocol": ("protocol", "protocols"),
}


@given(parsers.re(r"the (?P<dimension>task type|task status|protocol) filter is (?P<value>.+)"))
def given_task_filter(ctx: dict, dimension: str, value: str) -> None:
    """Put one filter on the query, in the spelling the Examples value names.

    ``omitted`` sends no filter, ``empty array`` the plural with no members, ``a+b`` the
    plural with those members, and anything else the singular -- including values the
    enum does not hold, which is what the refusal rows send.
    """
    if value == "omitted":
        return
    singular, plural = _FILTER_FIELDS[dimension]
    filters = ctx.setdefault("task_query", {}).setdefault("filters", {})
    if value == "empty array":
        filters[plural] = []
    elif "+" in value:
        filters[plural] = value.split("+")
    else:
        filters[singular] = value


@given(parsers.re(r"the task list sort (?P<member>field|direction) is (?P<value>.+)"))
def given_task_sort(ctx: dict, member: str, value: str) -> None:
    """Put ``sort.field`` or ``sort.direction`` on the query; ``omitted`` sends neither."""
    if value != "omitted":
        ctx.setdefault("task_query", {}).setdefault("sort", {})[member] = value


# ═══════════════════════════════════════════════════════════════════════
# WHEN step
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent queries the task list")
def when_query_task_list(ctx: dict) -> None:
    """Send list_tasks with the query the Givens built, on the scenario's transport."""
    dispatch_via_client(ctx, "list_tasks", dict(ctx.get("task_query", {})))


# ═══════════════════════════════════════════════════════════════════════
# THEN outcomes -- reached through then_result_should_be (uc002_create_media_buy.py)
# ═══════════════════════════════════════════════════════════════════════

#: The wire field a ``filters`` dimension is read back from. The pin names the filter
#: ``protocol`` and the task item ``domain``; both are the AdCP protocol.
_WIRE_FIELD = {"task_type": "task_type", "status": "status", "protocol": "domain"}


def is_task_query_outcome(outcome: str) -> bool:
    """Whether *outcome* is one of the list_tasks outcomes this module grades.

    A refusal row is not: it is written ``error <CODE> <recovery> on <field>`` and graded by
    the shared structured-error grammar like every other UC-002 refusal.
    """
    return outcome.startswith("tasks ")


def assert_task_query_outcome(ctx: dict, outcome: str) -> None:
    """Grade a list_tasks outcome. The grammar, each against the seeded tasks:

    * ``tasks with <dimension> <v1>[, <v2>...] only`` -- exactly the seeded tasks whose
      <dimension> is one of the values, no others;
    * ``tasks of every <dimension>`` -- every seeded task, the filter having narrowed nothing;
    * ``tasks sorted by <field> <asc|desc>`` -- every seeded task, ordered by <field>.

    An outcome outside it raises rather than passing ungraded.
    """
    words = outcome.split()
    seeded: dict[str, _SeededTask] = ctx["seeded_tasks"]
    if outcome.startswith("tasks with ") and outcome.endswith(" only"):
        dimension = words[2]
        wanted = {value.strip() for value in " ".join(words[3:-1]).split(",")}
        _assert_listed(ctx, {step_id for step_id, task in seeded.items() if getattr(task, dimension) in wanted})
    elif outcome.startswith("tasks of every "):
        _assert_listed(ctx, set(seeded))
    elif outcome.startswith("tasks sorted by "):
        field, direction = words[3], words[4]
        listed = _assert_listed(ctx, set(seeded))
        keys = [seeded[step_id].sort_key(field) for step_id in listed]
        assert keys == sorted(keys, reverse=direction == "desc"), (
            f"tasks are not in {field} {direction} order: {list(zip(listed, keys, strict=True))}"
        )
    else:
        raise ValueError(f"Unknown task query outcome: {outcome!r}")


def _assert_listed(ctx: dict, expected_ids: set[str]) -> list[str]:
    """Assert the wire lists exactly *expected_ids*, each as seeded; return the wire order.

    Each listed task's task_type, status and domain must be the pin's value for the seeded
    task, and ``query_summary.total_matching`` must count the same set.
    """
    seeded: dict[str, _SeededTask] = ctx["seeded_tasks"]
    tasks = wire_field(ctx, "tasks")
    listed = [task["task_id"] for task in tasks]
    assert len(listed) == len(set(listed)), f"a task is listed twice: {listed}"
    assert set(listed) == expected_ids, (
        f"listed {sorted(listed)}, expected {sorted(expected_ids)}; "
        f"unexpected {sorted(set(listed) - expected_ids)}, missing {sorted(expected_ids - set(listed))}"
    )
    for task in tasks:
        expected = seeded[task["task_id"]]
        actual = {dimension: task.get(field) for dimension, field in _WIRE_FIELD.items()}
        assert actual == {dimension: getattr(expected, dimension) for dimension in _WIRE_FIELD}, (
            f"task {task['task_id']} reads {actual} on the wire, seeded as {expected}"
        )
    total = wire_field(ctx, "query_summary.total_matching")
    assert total == len(expected_ids), f"query_summary.total_matching is {total}, expected {len(expected_ids)}"
    return listed

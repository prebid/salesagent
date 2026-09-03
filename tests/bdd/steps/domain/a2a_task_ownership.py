"""BDD steps for the A2A in-memory task ownership gate (local feature, #1780).

Grades the protocol surface of ``tasks/get`` / ``tasks/cancel``, not an AdCP
skill: a denial has no artifact and no two-layer envelope. The oracle is
``assert_wire_task_not_found`` on the **live** v0.3 JSON-RPC body captured
through ``create_jsonrpc_routes(..., enable_v0_3_compat=True)``. Live adapter
wire serialization is also proven in ``tests/unit/test_a2a_task_identity_wire.py``
(#1720). Never use ``assert_envelope_shape`` / ``wire_error_envelope`` here.

Owner, same-tenant sibling and other-tenant caller are seeded by
``A2ATaskOwnershipEnv`` with real access tokens, so every dispatch runs the
production auth chain before the ownership gate decides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

from tests.a2a_helpers import assert_wire_auth_failure, assert_wire_task_not_found, assert_wire_task_served

if TYPE_CHECKING:
    from a2a.compat.v0_3.types import TaskState as WireTaskState
    from a2a.types import TaskState

# Single Gherkin key set — protobuf / wire values are pure functions of the key.
_OWNED_TASK_STATE_NAMES = ("WORKING", "CANCELED")


def _task_state(name: str) -> TaskState:
    """Resolve a Gherkin state word to the protobuf ``TaskState`` enum member."""
    from a2a.types import TaskState

    if name not in _OWNED_TASK_STATE_NAMES:
        raise KeyError(name)
    return getattr(TaskState, f"TASK_STATE_{name}")


def _wire_task_state(name: str) -> WireTaskState:
    """Resolve a Gherkin state word to the v0.3 wire ``TaskState`` enum member."""
    from a2a.compat.v0_3.types import TaskState as WireTaskState

    if name not in _OWNED_TASK_STATE_NAMES:
        raise KeyError(name)
    return WireTaskState(name.lower())


@given(parsers.parse('an in-memory A2A task "{task_id}" owned by the owning principal'))
def given_owned_in_memory_task(ctx: dict, task_id: str) -> None:
    """Seed a WORKING task owned by the owner principal on the shared handler."""
    env = ctx["env"]
    env.seed_a2a_task(task_id, identity=env.identity_for_role("owner"))


@given(parsers.parse('an in-memory A2A task "{task_id}" with no owner record'))
def given_orphan_in_memory_task(ctx: dict, task_id: str) -> None:
    """Seed a task in ``handler.tasks`` without recording ``_task_owners``.

    Grades the fail-closed path when a task exists but has no owner record
    (legacy tasks / future create paths that forget to record ownership).
    """
    env = ctx["env"]
    env.seed_a2a_task(task_id, identity=env.identity_for_role("owner"), record_owner=False)


@when(parsers.parse('the "{role}" calls {method} for task "{task_id}"'))
def when_role_calls_task_method(ctx: dict, role: str, method: str, task_id: str) -> None:
    """Dispatch ``tasks/get`` / ``tasks/cancel`` as *role* against the shared handler."""
    env = ctx["env"]
    env.run_a2a_task_method(method, task_id, identity=env.identity_for_role(role))


@when(parsers.parse('an unauthenticated caller calls {method} for task "{task_id}"'))
def when_unauth_calls_task_method(ctx: dict, method: str, task_id: str) -> None:
    """Dispatch with ``identity=None`` — auth failure must not collapse to not-found."""
    env = ctx["env"]
    env.run_a2a_task_method(method, task_id, identity=None)


@then(parsers.parse('the A2A task response should carry task "{task_id}" in state {state}'))
def then_task_served(ctx: dict, task_id: str, state: str) -> None:
    """Assert the caller was served the Task from the live wire result.

    ``assert_wire_task_served`` reads the typed wire slot
    (v0.3 compat serialization) — not the handler's in-memory store — so a
    success-path adapter regression reddens owner-get and owner-cancel. Store
    mutation is graded separately by the stored-task Then.
    """
    env = ctx["env"]
    assert env.last_a2a_task_error is None, (
        f"Expected task {task_id} to be served, got error: {env.last_a2a_task_error}"
    )
    assert_wire_task_served(env.last_a2a_wire_task, task_id, _wire_task_state(state))


@then(parsers.parse('the A2A task response should be a JSON-RPC task-not-found error for "{task_id}"'))
def then_task_not_found(ctx: dict, task_id: str) -> None:
    """Assert the live not-found body — the same for denial and unknown id.

    ``assert_wire_task_not_found`` pins code/message/data by exact-dict equality
    on the captured JSON-RPC error (independent of ``_task_not_found_message``).
    An ownership denial that leaked "you may not touch this" — or any owner/
    tenant identity substring — would fail that equality first; a separate
    forbidden-substring scan after it can never fire (any needle breaks
    equality before the scan runs), so exact equality already subsumes
    non-disclosure here. The unit-altitude oracle
    (``assert_task_not_found_nondisclosure``) is the one that still needs the
    scan, since its message is a template rather than a full literal.
    """
    env = ctx["env"]
    error = env.last_a2a_task_error
    assert error is not None, f"Expected a task-not-found error for {task_id}, got none"
    assert_wire_task_not_found(error, task_id)


@then("the A2A task response should be an authentication failure, not task-not-found")
def then_auth_failure_not_task_not_found(ctx: dict) -> None:
    """Unauthenticated callers must not receive the ownership-denial not-found shape."""
    env = ctx["env"]
    assert env.last_a2a_task is None, f"Unauth call still returned a Task: {env.last_a2a_task}"
    error = env.last_a2a_task_error
    assert error is not None, "Expected an authentication failure, got none"
    assert_wire_auth_failure(error)


@then(parsers.parse('the stored task "{task_id}" should be in state {state}'))
def then_stored_task_state(ctx: dict, task_id: str, state: str) -> None:
    """Grade in-memory store state after a fail-closed deny.

    Only the no-owner-record cancel scenario uses this Then. Missing
    ``_task_owners`` denies everyone (including the seed identity), so a
    follow-up ``tasks/get`` as "owner" is itself not-found — that wire path
    is already graded by ``no-owner-get``. Non-mutation is not visible on
    the buyer wire; it is graded through ``a2a_task_state`` (harness public
    accessor). Owned denials that stay servable to the recorded owner use
    an explicit ``When the "owner" calls tasks/get`` + ``then_task_served``.
    """
    env = ctx["env"]
    stored = env.a2a_task_state(task_id)
    expected = _task_state(state)
    assert stored is not None, f"Expected stored task {task_id} to still exist"
    assert stored == expected, f"Expected stored task {task_id} in state {state}, got {stored}"

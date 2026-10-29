"""Ownership scenarios grade native A2A task responses; authority is in the feature."""

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request


@given(parsers.parse('a "{state}" task created by "{creator}"'))
def given_owned_task(ctx: dict, state: str, creator: str) -> None:
    ctx["env"].setup_task(state, creator)


@when(parsers.parse('"{caller}" calls "{operation}" for the "{target}" task'))
def when_task_call(ctx: dict, caller: str, operation: str, target: str) -> None:
    dispatch_request(ctx, caller=caller, operation=operation, target=target)


@then(parsers.parse('the task state is "{state}"'))
def then_task_state(ctx: dict, state: str) -> None:
    assert wire_field(ctx, "status")["state"] == f"TASK_STATE_{state.upper()}"


@then("the task is not found")
def then_task_not_found(ctx: dict) -> None:
    ctx["env"].assert_not_found(ctx["result"])
    ctx["denied_result"] = ctx["result"]


@then("the refusal matches the inaccessible task")
def then_same_refusal(ctx: dict) -> None:
    ctx["env"].assert_same_refusal(ctx["denied_result"], ctx["result"])


@then("the owner's task is unchanged")
def then_owner_unchanged(ctx: dict) -> None:
    ctx["env"].assert_unchanged()


@then(parsers.parse('task access fails with "{code}"'))
def then_task_auth_failure(ctx: dict, code: str) -> None:
    ctx["result"].assert_wire_error(code)
    assert ctx["result"].envelope["status_code"] == 401

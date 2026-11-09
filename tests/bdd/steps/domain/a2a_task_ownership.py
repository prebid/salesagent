"""Native receipt lifetime and durable AdCP polling, graded on real HTTP."""

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request


@given(parsers.parse('an A2A invocation producing "{state}"'))
def given_task_invocation(ctx: dict, state: str) -> None:
    ctx["env"].prepare_task(state)


@when(parsers.parse('"{caller}" invokes the prepared AdCP skill'))
def when_invoke_task(ctx: dict, caller: str) -> None:
    dispatch_request(ctx, action="invoke", caller=caller)


@when(parsers.parse('"{caller}" calls "{operation}" for the "{target}" task'))
def when_task_call(ctx: dict, caller: str, operation: str, target: str) -> None:
    dispatch_request(ctx, caller=caller, operation=operation, target=target)


@when("the owner polls the durable AdCP task")
def when_poll_adcp_task(ctx: dict) -> None:
    dispatch_request(ctx, action="poll", caller="owner")


@then(parsers.parse('the native receipt state is "{state}"'))
def then_task_state(ctx: dict, state: str) -> None:
    assert wire_field(ctx, "task")["status"]["state"] == f"TASK_STATE_{state.upper()}"


@then(parsers.parse('the receipt contains the AdCP "{status}" result'))
def then_adcp_result(ctx: dict, status: str) -> None:
    assert ctx["env"].adcp_result(ctx["result"])["status"] == status


@then("the AdCP task handle is distinct from the native receipt")
def then_distinct_handles(ctx: dict) -> None:
    task_id = ctx["env"].adcp_result(ctx["result"])["task_id"]
    assert isinstance(task_id, str) and task_id
    assert task_id != wire_field(ctx, "task")["id"]
    assert ctx["env"].adcp_result(ctx["result"])["context"] == {"trace": "native-receipt"}


@then("the durable AdCP task still has the submitted handle")
def then_durable_handle(ctx: dict) -> None:
    assert ctx["env"].adcp_result(ctx["result"])["task_id"] == ctx["env"].adcp_task_id


@then("the task is not found")
def then_task_not_found(ctx: dict) -> None:
    ctx["env"].assert_not_found(ctx["result"])
    ctx["denied_result"] = ctx["result"]


@then("the refusal matches the inaccessible task")
def then_same_refusal(ctx: dict) -> None:
    ctx["env"].assert_same_refusal(ctx["denied_result"], ctx["result"])

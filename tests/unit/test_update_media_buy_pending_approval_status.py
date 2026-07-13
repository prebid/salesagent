"""A pending-approval update_media_buy reports the SUBMITTED envelope, not completed.

Regression for salesagent-5dxc (PR #1567, adcp 5.7->6.6 bump). adcp 6.6 gave
UpdateMediaBuySuccess a default status="completed" (the same _base.py mechanism
as CreateMediaBuySuccess). The manual-approval branch of _update_media_buy_impl
(src/core/tools/media_buy_update.py) constructs UpdateMediaBuySuccess directly for
an update that is PENDING human approval and NOT yet applied, so the wire envelope
serializes status="completed" — asserting the update completed when it has not.

Spec 3.1.1 models a not-yet-applied update as UpdateMediaBuySubmitted
(status="submitted" + task_id). create_media_buy already does this: its pending
path returns CreateMediaBuyResult(status="submitted") whose _serialize overrides
the envelope status. update_media_buy has no such wrapper.

Wire faithfulness, per transport: on REST the model_dump IS the HTTP body; on
MCP the wrapper does ToolResult(structured_content=response)
(media_buy_update.py:1421), serialized via pydantic to_jsonable_python — NOT
model_dump, though for this model the two agree (no model_dump override); on
A2A a submitted result is conveyed by the Task object itself (state=SUBMITTED,
no artifacts — the early-return in on_message_send), so no serialized response
body crosses the A2A wire at all. This unit test asserts the shared _impl's
serialized envelope; the per-transport wire is graded by the wired BR-UC-003
manual-approval BDD scenarios (1b2f03bc9).
"""

from __future__ import annotations

from tests.harness.media_buy_update import MediaBuyUpdateEnv


def _pending_approval_env() -> MediaBuyUpdateEnv:
    env = MediaBuyUpdateEnv(tenant_id="t1", principal_id="p1")
    return env


def test_pending_approval_update_reports_submitted_not_completed():
    """update_media_buy on the manual-approval branch must not claim completion."""
    with _pending_approval_env() as env:
        adapter = env.mock["adapter"].return_value
        adapter.manual_approval_required = True
        adapter.manual_approval_operations = ["update_media_buy"]
        env.set_media_buy(media_buy_id="mb-001", status="active")

        result = env.call_impl(media_buy_id="mb-001", budget=5000.0)

    envelope = result.model_dump(mode="json")

    # Spec 3.1.1: a not-yet-applied (pending human approval) update is SUBMITTED.
    assert envelope["status"] == "submitted", (
        f"pending-approval update must report submitted, got {envelope['status']!r} (the buy has NOT been updated yet)"
    )
    # UpdateMediaBuySubmitted requires a task_id the buyer polls for the outcome.
    assert envelope.get("task_id"), "pending-approval update must carry a task_id for the buyer to track approval"

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

Wire faithfulness: the update transport wrappers put the _impl-returned model
straight onto the wire — the MCP wrapper does ToolResult(structured_content=response)
(media_buy_update.py:1421) and the A2A/REST wrappers serialize the same model — so
the success-envelope `status` is transport-invariant and the serialized model dump
IS the wire shape on every transport. This test therefore asserts on the serialized
envelope produced by the shared _impl.
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

"""Regression tests: task_type wire-fallback must not leak into the internal label.

 (latent bug). ``ContextManager._send_push_notifications`` builds
the SDK webhook payload via ``create_mcp_webhook_payload``, which validates
``task_type`` against the closed ``adcp.types.TaskType`` enum and coerces
non-members to a fallback (``update_media_buy``). The pre-fix code rewrote the
single ``task_type_str`` variable in place, so the coerced wire value leaked
into the typed context's ``task_type`` — the label consumed by
``protocol_webhook_service`` for the audit log, the delivery-webhook guards,
and the ``WebhookDeliveryLog.task_type`` DB column.

The fix keeps two distinct values:
  - the typed context's ``task_type`` = the ORIGINAL action label (untouched), and
  - the SDK payload's ``task_type`` = a validated COPY (coerced when invalid).

Mutation coverage (verified during authoring):
  #1 task.task_type = wire_task_type (reuse coerced copy)
     -> test_task_context_keeps_original_label_for_non_tasktype FAILS
  #2 pass task_type_str (uncoerced) to create_mcp_webhook_payload
     -> the builder raises on the invalid enum value; _drive surfaces no
        send_notification call -> test_*_for_non_tasktype FAILS
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core import context_manager
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.unit._push_notification_helpers import make_push_step, session_returning


def _capture_send(tool_name: str):
    """Drive the REAL _send_push_notifications and capture the send_notification call.

    Returns the (payload, task) the webhook service was invoked with, or
    ``None`` if it was never called (e.g. the payload builder raised).
    """
    webhook = SimpleNamespace(id="pnc_1")
    session = session_returning([webhook])

    captured: dict[str, object] = {}

    async def record(**kwargs):
        captured["payload"] = kwargs.get("payload")
        captured["task"] = kwargs.get("task")

    # A REAL service with only the wire call stubbed. _send_push_notifications now
    # goes through notify(), which is where the dialect is selected and the payload
    # built (GH #1802) -- mocking the whole service would mock away the
    # code these tests exist to grade. With the real object, notify() runs for real
    # and record() still captures exactly what reaches the wire.
    fake_service = ProtocolWebhookService()
    fake_service.send_notification = AsyncMock(side_effect=record)  # type: ignore[method-assign]

    cm = context_manager.ContextManager()
    with patch.object(context_manager, "get_protocol_webhook_service", return_value=fake_service):
        cm._send_push_notifications(make_push_step(tool_name), "completed", session)

    if "task" not in captured:
        return None
    return captured["payload"], captured["task"]


def test_task_context_keeps_original_label_for_non_tasktype():
    """A non-TaskType tool_name must survive verbatim in metadata['task_type'].

    The wire payload is coerced to the fallback, but the internal label
    (audit/guards/DB column) must remain the original action.
    """
    result = _capture_send("delivery_report")
    assert result is not None, "send_notification was never called (payload builder raised?)"
    payload, task = result

    # Internal label: the ORIGINAL action, untouched by the SDK fallback.
    assert task.task_type == "delivery_report"

    # Wire value: coerced to the SDK-accepted fallback (not the original).
    # The SDK normalizes task_type to the TaskType enum, so compare by value.
    assert str(payload.task_type.value) == "update_media_buy"
    assert str(payload.task_type.value) != task.task_type


def test_valid_tasktype_label_passes_through_unchanged():
    """A valid TaskType tool_name is identical on both the wire and in the typed context."""
    result = _capture_send("create_media_buy")
    assert result is not None
    payload, task = result

    assert task.task_type == "create_media_buy"
    assert str(payload.task_type.value) == "create_media_buy"


@pytest.mark.parametrize("tool_name", ["delivery_report", "totally_made_up"])
def test_invalid_labels_never_break_the_payload(tool_name):
    """Any non-TaskType label still produces a valid, sendable payload."""
    result = _capture_send(tool_name)
    assert result is not None, f"non-TaskType {tool_name!r} broke the payload builder"
    payload, task = result

    # metadata preserves the original; wire is the safe fallback.
    assert task.task_type == tool_name
    assert str(payload.task_type.value) == "update_media_buy"

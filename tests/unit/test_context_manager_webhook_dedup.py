"""Regression pin: one webhook per workflow-step status change.

``ContextManager._send_push_notifications`` used to send inside
``for mapping in mappings: for _webhook_config in webhooks:`` while building the
delivery target from the STEP's own ``request_data.push_notification_config`` —
the loop variables were never used as targets. Every extra ObjectWorkflowMapping
(create_media_buy links the media_buy to its step at two call sites) and every
extra active PushNotificationConfig row multiplied IDENTICAL sends, so a buyer's
auto-approved create_media_buy received duplicate ``completed`` webhooks
(caught live by the E2E single-completed-webhook assertion in
``test_a2a_webhook_payload_types::test_completed_status_sends_task_payload``).

The fix treats ``mappings`` and ``webhooks`` as opt-in gates and sends exactly
once per status change.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.core import context_manager
from tests.unit._push_notification_helpers import make_push_step, session_returning


def _drive_with(mappings, webhooks):
    """Drive the REAL _send_push_notifications; return the mocked webhook service."""
    context = SimpleNamespace(tenant_id="tenant_1", principal_id="principal_1")
    session = session_returning(mappings, context, webhooks)

    fake_service = MagicMock()
    fake_service.send_notification = AsyncMock()

    cm = context_manager.ContextManager()
    with patch.object(context_manager, "get_protocol_webhook_service", return_value=fake_service):
        cm._send_push_notifications(make_push_step(), "completed", session)
    return fake_service


def _mapping(object_id: str) -> SimpleNamespace:
    return SimpleNamespace(object_type="media_buy", object_id=object_id, action="create")


def test_multiple_mappings_and_configs_send_exactly_one_webhook():
    """2 mappings x 2 active configs must still deliver ONE webhook, not four.

    The payload depends only on (step, new_status); the rows are gates. Any
    count > 1 is the duplicate-webhook regression the E2E pin caught live.
    """
    service = _drive_with(
        mappings=[_mapping("mb_1"), _mapping("mb_1")],
        webhooks=[SimpleNamespace(id="pnc_1"), SimpleNamespace(id="pnc_2")],
    )
    assert service.send_notification.await_count == 1, (
        f"expected exactly one webhook per status change, got {service.send_notification.await_count} "
        "(mappings x configs multiplied identical sends)"
    )


def test_no_active_configs_sends_no_webhook():
    """The registered-config gate is preserved: zero active configs -> zero sends."""
    service = _drive_with(mappings=[_mapping("mb_1")], webhooks=[])
    assert service.send_notification.await_count == 0


def test_no_mappings_sends_no_webhook():
    """The mapping gate is preserved: an unlinked step sends nothing."""
    service = _drive_with(mappings=[], webhooks=[SimpleNamespace(id="pnc_1")])
    assert service.send_notification.await_count == 0

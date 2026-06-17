"""Shared fixtures for driving ContextManager._send_push_notifications in tests.

Both ``test_context_manager_task_pinning`` (webhook task GC pinning) and
``test_context_manager_task_type_label`` (task_type wire/label split,
salesagent-yi3s) drive the REAL ``_send_push_notifications`` path, which issues
exactly three ``session.scalars()`` queries in order:
ObjectWorkflowMapping (.all()), Context (.first()), PushNotificationConfig
(.all()). These helpers build the step and the ordered session mock so the two
suites don't hand-roll the same setup (DRY — enforced by check_code_duplication).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def make_push_step(tool_name: str = "create_media_buy") -> SimpleNamespace:
    """A WorkflowStep-like object carrying the push_notification_config request data."""
    return SimpleNamespace(
        step_id="step_1",
        context_id="ctx_1",
        tool_name=tool_name,
        response_data={"ok": True},
        request_data={
            "protocol": "mcp",
            "push_notification_config": {"url": "https://buyer.example/webhook"},
        },
        context=SimpleNamespace(tenant_id="tenant_1", principal_id="principal_1"),
    )


def session_returning(mappings, context, webhooks) -> MagicMock:
    """A session whose scalars() returns mappings, then context, then webhooks.

    _send_push_notifications issues exactly three scalars() queries in order:
    ObjectWorkflowMapping (.all()), Context (.first()), PushNotificationConfig
    (.all()).
    """
    scalars_mapping = MagicMock()
    scalars_mapping.all.return_value = mappings
    scalars_context = MagicMock()
    scalars_context.first.return_value = context
    scalars_webhooks = MagicMock()
    scalars_webhooks.all.return_value = webhooks

    session = MagicMock()
    session.scalars.side_effect = [scalars_mapping, scalars_context, scalars_webhooks]
    return session

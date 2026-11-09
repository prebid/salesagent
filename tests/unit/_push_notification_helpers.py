"""Shared fixtures for driving ContextManager._send_push_notifications in tests.

Both ``test_context_manager_task_pinning`` (webhook task GC pinning) and
``test_context_manager_task_type_label`` (task_type wire/label split)
drive the REAL ``_send_push_notifications`` path. These helpers
build the step and the session mock so the two suites don't hand-roll the same
setup (DRY -- enforced by check_code_duplication).

The shape here tracks production, and production got SIMPLER: that path used to
issue three hand-written ``session.scalars()`` queries in a fixed order
(ObjectWorkflowMapping, Context, PushNotificationConfig), so the session mock was
an ordered side_effect list and any reordering of the production code silently
mis-fed it. It now reads ``step.object_mappings`` and ``step.context`` -- two
relationships that already existed on ``WorkflowStep`` -- and asks
``PushNotificationConfigRepository`` for the configs. One query, no ordering
contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def make_push_step(tool_name: str = "create_media_buy") -> SimpleNamespace:
    """A WorkflowStep-like object carrying the push_notification_config request data.

    Carries ``object_mappings`` and ``context`` because a real ``WorkflowStep``
    does: both are relationships on the model, and the production path reads them
    off the step rather than re-querying for them.
    """
    return SimpleNamespace(
        step_id="step_1",
        context_id="ctx_1",
        tool_name=tool_name,
        response_data={"ok": True},
        request_data={
            "protocol": "mcp",
            # ``operation_id`` is present because a registration a conformant seller can
            # serve MUST carry one: core/mcp-webhook-payload.json makes it required on the
            # envelope and ``compliance/universal/webhook-emission.yaml`` req. 1 grades the
            # echo, explicitly forbidding recovery from the receiver URL. Omitting it here
            # made the fixture a registration no delivery can answer -- adcp 6.6.0 built a
            # schema-invalid body from it anyway, adcp 9 refuses. Matches what
            # PushNotificationConfigFactory has always set.
            "push_notification_config": {
                "url": "https://buyer.example/webhook",
                "operation_id": "op_push_helper",
            },
        },
        object_mappings=[SimpleNamespace(object_type="media_buy", object_id="mb_1", action="create")],
        context=SimpleNamespace(tenant_id="tenant_1", principal_id="principal_1"),
    )


def session_returning(webhooks) -> MagicMock:
    """A session whose one scalars() call yields the active push-notification configs.

    That single call is ``PushNotificationConfigRepository
    .list_active_by_principal``. The repository is constructed against this
    session, so stubbing ``scalars`` is stubbing the one query it makes.
    """
    scalars_webhooks = MagicMock()
    scalars_webhooks.all.return_value = webhooks

    session = MagicMock()
    session.scalars.return_value = scalars_webhooks
    return session

"""Integration regression: admin reject of a media buy must still fire the buyer webhook.

Bug: salesagent-ihxu (adcp 6.6 / spec 3.1.1).
src/admin/blueprints/operations.py approve_media_buy() used to construct the RAW
library adcp.types.CreateMediaBuySuccessResponse in the reject webhook branch. Under
adcp 6.6 that raw type requires ``confirmed_at`` AND ``revision``; constructing it with
only media_buy_id/packages/context raises a pydantic ValidationError. The handler's
outer try/except swallows that error (flash "Error processing approval", 302), so the
buyer webhook SILENTLY never fired. The fix routes construction through our defaulted
subclass src.core.schemas.CreateMediaBuySuccess (status/confirmed_at/revision defaulted).

Behavioral guard: drive the admin reject route and assert the webhook service's
send_notification was awaited exactly once. Pre-fix the raw construction raises BEFORE
send, so send_notification is never called — that is what this test detects.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.context_manager import ContextManager

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WEBHOOK_URL = "https://buyer.example.com/adcp-webhook"


@pytest.fixture
def pending_reject_media_buy(integration_db):
    """A pending-approval media buy wired for the admin reject webhook path.

    Builds (via factories + ContextManager production APIs — no session.add in the
    test body) a tenant + principal, a pending_approval media buy, an active
    PushNotificationConfig at WEBHOOK_URL, and a tenant-scoped approval workflow step
    whose ObjectWorkflowMapping ties it to the media buy with action "reject". All rows
    are committed (factories persist on commit; ContextManager commits its own writes)
    so the Flask route's separate get_db_session() sees them.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import (
        ALL_FACTORIES,
        MediaBuyFactory,
        PrincipalFactory,
        PropertyTagFactory,
        PushNotificationConfigFactory,
        TenantFactory,
    )

    engine = get_engine()
    session = SASession(bind=engine)
    try:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = session

        tenant = TenantFactory(tenant_id="reject_wh_tenant")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="reject_wh_principal",
            platform_mappings={"mock": {"id": "reject_wh_advertiser"}},
        )
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_reject_wh",
            status="pending_approval",
        )
        PushNotificationConfigFactory(
            tenant=tenant,
            principal=principal,
            url=WEBHOOK_URL,
            is_active=True,
        )

        # Tenant-scoped approval workflow step + object mapping (production API).
        cm = ContextManager()
        context = cm.create_context(
            tenant_id=tenant.tenant_id,
            principal_id=principal.principal_id,
        )
        cm.create_workflow_step(
            context_id=context.context_id,
            step_type="approval",
            owner="publisher",
            status="requires_approval",
            tool_name="create_media_buy",
            request_data={
                "push_notification_config": {"url": WEBHOOK_URL},
                "protocol": "mcp",
            },
            object_mappings=[
                {
                    "object_type": "media_buy",
                    "object_id": media_buy.media_buy_id,
                    "action": "reject",
                }
            ],
        )

        yield {
            "tenant_id": tenant.tenant_id,
            "media_buy_id": media_buy.media_buy_id,
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


class TestAdminMediaBuyRejectWebhook:
    """Rejecting a pending media buy from the admin UI must fire the buyer webhook."""

    def test_reject_fires_buyer_webhook(self, authenticated_admin_session, pending_reject_media_buy):
        """POST reject -> 302 and the webhook service's send_notification is awaited once.

        Regression for salesagent-ihxu: before the fix, the raw CreateMediaBuySuccessResponse
        construction ValidationErrors before send_notification is reached, and the swallowing
        try/except hides it — so send_notification is never called.
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        mock_service = MagicMock()
        mock_service.send_notification = AsyncMock(return_value=None)

        with patch(
            "src.admin.blueprints.operations.get_protocol_webhook_service",
            return_value=mock_service,
        ):
            resp = authenticated_admin_session.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "reject", "reason": "test"},
            )

        assert resp.status_code == 302, f"expected redirect, got {resp.status_code}"
        # The real guard: the webhook actually fired with the rejected media buy's envelope.
        # Pre-fix, the raw-type construction raises before this call, so it is never made.
        # metadata.task_type echoes the workflow step's tool_name ("create_media_buy").
        mock_service.send_notification.assert_called_once_with(
            push_notification_config=ANY,
            payload=ANY,
            metadata={"task_type": "create_media_buy"},
        )

    def test_reject_webhook_does_not_embed_completed_success(
        self, authenticated_admin_session, pending_reject_media_buy
    ):
        """The rejected media buy webhook body must not embed a completed success result.

        Regression for salesagent-88e2 (adcp 6.6 / spec 3.1.1): the reject branch built the
        embedded ``result`` as CreateMediaBuySuccess, which now defaults status="completed",
        confirmed_at=now, revision=1. So the outbound body had a correct OUTER status="rejected"
        but an embedded result asserting the buy COMPLETED — a Success envelope cannot represent
        a rejection. Assert the embedded result does not claim completion.
        """
        tenant_id = pending_reject_media_buy["tenant_id"]
        media_buy_id = pending_reject_media_buy["media_buy_id"]

        # Capture the outbound payload via side_effect (atomic — avoids the weak
        # split-assertion antipattern of assert_called_once() + call_args).
        captured: dict = {}

        async def _capture(*, push_notification_config=None, payload=None, metadata=None):
            captured["payload"] = payload

        mock_service = MagicMock()
        mock_service.send_notification = AsyncMock(side_effect=_capture)

        with patch(
            "src.admin.blueprints.operations.get_protocol_webhook_service",
            return_value=mock_service,
        ):
            resp = authenticated_admin_session.post(
                f"/tenant/{tenant_id}/media-buy/{media_buy_id}/approve",
                data={"action": "reject", "reason": "test"},
            )

        assert resp.status_code == 302, f"expected redirect, got {resp.status_code}"
        assert "payload" in captured, "reject route did not send a webhook payload"
        payload = captured["payload"]
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload

        # Outer envelope correctly reports the rejection.
        assert body["status"] == "rejected", f"outer status should be rejected, got {body.get('status')!r}"

        # The embedded result must NOT assert completion inside a rejection payload.
        embedded = body.get("result") or {}
        assert embedded.get("status") != "completed", (
            f"rejected webhook embeds a result claiming status={embedded.get('status')!r}; "
            "a rejection must not carry a completed-success envelope"
        )
        assert not embedded.get("confirmed_at"), (
            "rejected webhook embeds confirmed_at — the buy was rejected, not confirmed/completed"
        )

"""The sender receives a GATED value, never a config-shaped object built to fit.

Covers GH #1802.

``ProtocolWebhookService.send_notification`` takes ``DeliverableWebhookTarget``, a
structural Protocol over three fields. Its docstring records why: the annotation
used to name the ORM class, so the A2A path fabricated a detached
``PushNotificationConfig(tenant_id="", principal_id="")`` purely to type-check --
"a config-shaped object with empty scope ids, which is exactly how an unreceipted
config reached a sender". Lane fo99.2 deleted that one.

Two callers still fabricate. This module grades the scheduler, which builds a
``temp_``-id row when no stored config matches. The object it hands down carries
no receipt from any gate, so nothing guarantees the registration was ever valid --
and because the caller destructures ``authentication`` by hand, it also accepts a
document the pinned schema forbids:

    schemes = auth_config.get("schemes", [])
    auth_type = schemes[0] if schemes else None

``AuthenticationScheme`` permits AT MOST ONE scheme. Given two, the gate raises
``AdCPValidationError``; ``schemes[0]`` instead delivers under whichever happens to
be listed first. That is silent wrong-scheme delivery, not a dropped field.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.schemas import GetMediaBuyDeliveryResponse
from src.core.webhooks.registration import ValidatedWebhookRegistration
from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

SECRET = "s" * 40

SIGNED_WEBHOOK = {
    "url": "https://buyer.example.com/webhook",
    "frequency": "daily",
    "authentication": {"schemes": ["HMAC-SHA256"], "credentials": SECRET},
}

UNSIGNED_WEBHOOK = {
    "url": "https://buyer.example.com/webhook",
    "frequency": "daily",
    # No authentication block at all — the common case, and the one that must keep
    # delivering unsigned. Routing the carrier through the gate must not turn a
    # delivered webhook into a never-delivered one.
}

EMPTY_AUTH_WEBHOOK = {
    "url": "https://buyer.example.com/webhook",
    "frequency": "daily",
    # Falsy today (`if auth_config:`), so it behaves exactly like an absent block
    # and delivers unsigned. The gate REFUSES an empty block, so the caller keeps
    # the truthiness guard rather than silently making this a non-delivery —
    # that transition needs an owner sign-off (fo99.4 / BasicCredentials precedent).
    "authentication": {},
}

TWO_SCHEME_WEBHOOK = {
    "url": "https://buyer.example.com/webhook",
    "frequency": "daily",
    # The pinned schema allows at most one. A document the spec forbids.
    "authentication": {"schemes": ["Bearer", "HMAC-SHA256"], "credentials": SECRET},
}


def _delivery_response() -> GetMediaBuyDeliveryResponse:
    return GetMediaBuyDeliveryResponse(
        reporting_period={"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        currency="USD",
        media_buy_deliveries=[
            {
                "media_buy_id": "mb_carrier",  # only echoed in the mocked delivery payload
                "status": "active",
                "totals": {"impressions": 1000, "spend": 10.0, "clicks": 5},
                "by_package": [],
            }
        ],
        aggregated_totals={"impressions": 1000, "spend": 10.0, "media_buy_count": 1},
    )


async def _run_scheduler_for(reporting_webhook: dict):
    """Drive the scheduler's per-media-buy path and capture the sender's argument.

    No stored PushNotificationConfig exists for these media buys, so the
    fabrication branch is the one under test.
    """
    from tests.harness._base import IntegrationEnv

    # Unique ids per call: the agent-db persists between runs, so factory
    # sequences (which reset per process) collide with rows an earlier run left.
    suffix = uuid4().hex[:10]

    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=f"t_{suffix}")
        principal = PrincipalFactory(tenant=tenant, principal_id=f"p_{suffix}", access_token=f"tok_{suffix}")
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=f"mb_{suffix}",
            status="active",
            raw_request={"packages": [], "reporting_webhook": reporting_webhook},
        )
        scheduler = DeliveryWebhookScheduler()
        with (
            patch.object(
                scheduler.webhook_service, "send_notification", new_callable=AsyncMock, return_value=True
            ) as mock_send,
            patch(
                "src.services.delivery_webhook_scheduler._get_media_buy_delivery_impl",
                return_value=_delivery_response(),
            ),
        ):
            await scheduler._send_report_for_media_buy(media_buy, reporting_webhook, env.get_session(), force=True)
        return mock_send


async def test_scheduler_hands_the_sender_a_gated_value_not_a_fabricated_row():
    """The carrier must be a ValidatedWebhookRegistration, not an ORM instance.

    Asserting the type is the point, not a tautology: the defect IS the type. A
    config-shaped object that no gate produced carries no evidence that the
    registration was ever valid, which is what 'the type is the receipt' means.
    """
    mock_send = await _run_scheduler_for(SIGNED_WEBHOOK)

    assert mock_send.await_count == 1, "the signed webhook should have been delivered"
    carrier = mock_send.await_args.kwargs["push_notification_config"]

    assert not isinstance(carrier, DBPushNotificationConfig), (
        "the scheduler fabricated an ORM row to satisfy the sender's type — "
        "the same shape fo99.2 deleted from the A2A path"
    )
    assert isinstance(carrier, ValidatedWebhookRegistration)
    assert carrier.url == SIGNED_WEBHOOK["url"]
    assert carrier.authentication_type == "HMAC-SHA256"
    assert carrier.authentication_token == SECRET


@pytest.mark.parametrize(
    ("label", "webhook"),
    [("no authentication block", UNSIGNED_WEBHOOK), ("empty authentication block", EMPTY_AUTH_WEBHOOK)],
)
async def test_unsigned_delivery_is_preserved(label, webhook):
    """Routing the carrier through the gate must not stop unsigned delivery.

    Both shapes deliver unsigned today. A refactor that made either of them refuse
    would be a delivered -> never-delivered change, which this epic treats as the
    cardinal sin and which fo99.4 required an owner sign-off to make.
    """
    mock_send = await _run_scheduler_for(webhook)

    assert mock_send.await_count == 1, f"{label}: unsigned delivery must still happen"
    carrier = mock_send.await_args.kwargs["push_notification_config"]
    assert carrier.authentication_type is None
    assert carrier.authentication_token is None


async def test_scheduler_refuses_a_registration_the_pinned_schema_forbids():
    """Two schemes must be REFUSED, not silently reduced to the first one.

    ``schemes[0]`` accepts the document and delivers under Bearer. The buyer asked
    for something the spec does not allow and gets delivery under a scheme they
    did not solely request, with no error anywhere.
    """
    mock_send = await _run_scheduler_for(TWO_SCHEME_WEBHOOK)

    assert mock_send.await_count == 0, (
        "a two-scheme registration is invalid under the pinned AuthenticationScheme "
        "(at most 1 item) and must not be delivered at all"
    )

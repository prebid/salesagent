"""_send_report_for_media_buy reuses the caller's session (#1088).

The scheduler passes a live ``session`` into ``_send_report_for_media_buy``;
the #1088 principal load must use it. Opening a nested ``get_db_session()``
inside the same operation acquires a SECOND pool connection per report
(pool-exhaustion exposure under the scheduler's fan-out) and reads the
principal in a separate transaction from the media_buy row it belongs to.

The test patches ``get_db_session`` to blow up: with the eager principal
loaded through the caller's session, the webhook must still send.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.harness._base import BareIntegrationEnv
from tests.helpers.delivery_response import make_delivery_response

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_WEBHOOK = {
    "url": "https://buyer.example.com/webhooks/delivery",
    "frequency": "daily",
}


@pytest.mark.asyncio
async def test_send_report_does_not_open_nested_db_session(integration_db):
    with BareIntegrationEnv() as env:
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
        session = env.get_session()

        scheduler = DeliveryWebhookScheduler()
        with (
            patch.object(
                scheduler.webhook_service, "send_notification", new_callable=AsyncMock, return_value=True
            ) as mock_send,
            patch(
                "src.services.delivery_webhook_scheduler._get_media_buy_delivery_impl",
                return_value=make_delivery_response(media_buy.media_buy_id),
            ),
            patch(
                "src.core.database.database_session.get_db_session",
                side_effect=AssertionError("scheduler must reuse the caller's session, not open a nested one"),
            ),
        ):
            await scheduler._send_report_for_media_buy(media_buy, _WEBHOOK, session, force=True)

        assert mock_send.await_count == 1, (
            "webhook must send using the caller's session — a nested get_db_session() "
            "call aborts the report (second pool connection per report)"
        )

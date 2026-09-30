"""Integration tests for manual/forced delivery webhook triggering."""

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import WebhookDeliveryLog
from src.core.schemas import GetMediaBuyDeliveryResponse
from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from tests.integration.test_delivery_webhooks_integration import (
    _create_basic_media_buy_with_webhook,
    _create_test_tenant_and_principal,
)


@pytest.mark.requires_db
def test_mock_response_validation():
    """Verify mock response passes validation checks."""
    mock_response = GetMediaBuyDeliveryResponse(
        reporting_period={"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        currency="USD",
        media_buy_deliveries=[
            {
                "media_buy_id": "mb_1",
                "status": "active",  # Required field per AdCP spec
                "totals": {"impressions": 1000, "spend": 10.0, "clicks": 5},
                "by_package": [],
            }
        ],
        aggregated_totals={  # Required field per AdCP spec
            "spend": 10.0,
            "impressions": 1000,
            "clicks": 5,
            "media_buy_count": 1,
        },
    )
    assert isinstance(mock_response, GetMediaBuyDeliveryResponse)
    assert mock_response.errors is None


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_force_trigger_delivery_webhook_bypasses_duplicate_check(integration_db):
    """Test that force=True sends a webhook even if one was already sent today."""

    # 1. Setup
    tenant_id, principal_id = _create_test_tenant_and_principal()
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id)

    scheduler = DeliveryWebhookScheduler()

    # Mock delivery response to ensure success (avoiding actual adapter calls)
    mock_response = GetMediaBuyDeliveryResponse(
        reporting_period={"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        currency="USD",
        media_buy_deliveries=[
            {
                "media_buy_id": media_buy_id,
                "status": "active",  # Required field per AdCP spec
                "totals": {"impressions": 1000, "spend": 10.0, "clicks": 5},
                "by_package": [],
            }
        ],
        aggregated_totals={  # Required field per AdCP spec
            "spend": 10.0,
            "impressions": 1000,
            "clicks": 5,
            "media_buy_count": 1,
        },
    )

    # Mock webhook sending to avoid network calls
    async def fake_send_notification(*args, **kwargs):
        return True

    with (
        patch.object(
            scheduler.webhook_service,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ) as mock_send,
        patch("src.services.delivery_webhook_scheduler.delivery_for_media_buy", return_value=mock_response),
    ):
        # 2. Insert a fake log entry simulating a report sent today
        with get_db_session() as session:
            # Use the same logic as scheduler to calculate "today" for reporting

            # So we need a log entry created today
            log = WebhookDeliveryLog(
                # id is auto-generated default=uuid4
                task_type="media_buy_delivery",
                notification_type="scheduled",
                status="success",
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                webhook_url="http://example.com",
                http_status_code=200,
                created_at=datetime.now(UTC),
            )
            session.add(log)
            session.commit()

            # reload media buy to attach to session
            from src.core.database.models import MediaBuy

            media_buy = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()

            # 3. Try sending WITHOUT force - should skip
            await scheduler._send_report_for_media_buy(
                media_buy, media_buy.raw_request["reporting_webhook"], session, force=False
            )
            assert mock_send.call_count == 0

            # 4. Try sending WITH force - should send
            await scheduler._send_report_for_media_buy(
                media_buy, media_buy.raw_request["reporting_webhook"], session, force=True
            )
            assert mock_send.call_count == 1

            # Verify call args
            args, kwargs = mock_send.await_args
            payload = kwargs.get("payload")
            assert payload is not None
            # Extract result from the McpWebhookPayload
            assert payload.result["media_buy_deliveries"][0]["media_buy_id"] == media_buy_id


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_trigger_report_for_media_buy_public_method(integration_db):
    """Test the public wrapper method trigger_report_for_media_buy."""

    # 1. Setup
    tenant_id, principal_id = _create_test_tenant_and_principal()
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id)

    scheduler = DeliveryWebhookScheduler()

    # Mock _send_report_for_media_buy to verify it receives force=True. The return value
    # is STATED, not left to AsyncMock's default: the wrapper now returns the sender's
    # verdict instead of a hardcoded True, so an unstated return makes the assertion
    # below compare a MagicMock. That it used to pass without one is the point — the old
    # wrapper answered True whatever the sender did, so this test could not have caught
    # a delivery reported as sent when it was refused.
    with patch.object(scheduler, "_send_report_for_media_buy", new_callable=AsyncMock) as mock_send_internal:
        mock_send_internal.return_value = True
        with get_db_session() as session:
            from src.core.database.models import MediaBuy

            media_buy = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()

            # 2. Call public method
            result = await scheduler.trigger_report_for_media_buy_by_id(media_buy_id, tenant_id)

            # 3. Verify result and call
            assert result is True
            mock_send_internal.assert_called_once()
            args, kwargs = mock_send_internal.call_args
            assert kwargs.get("force") is True


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_trigger_report_propagates_a_refused_delivery_as_false(integration_db):
    """A delivery the sender did NOT make must not be reported as triggered.

    The other half of the wrapper's contract, and the half that was unrepresentable
    until it stopped returning a hardcoded ``True``: a destination refused by the
    egress policy before any connection, or an unusable stored registration, walks off
    the end of ``_send_report_for_media_buy`` exactly like a success did. The operator
    surface that reads this bool (the admin trigger's flash) then said "Sent" for a
    webhook the buyer never received — a quiet failure on the one control an operator
    has for "did this go out".
    """
    tenant_id, principal_id = _create_test_tenant_and_principal()
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id)

    scheduler = DeliveryWebhookScheduler()

    with patch.object(scheduler, "_send_report_for_media_buy", new_callable=AsyncMock) as mock_send_internal:
        mock_send_internal.return_value = False

        result = await scheduler.trigger_report_for_media_buy_by_id(media_buy_id, tenant_id)

        assert result is False, (
            "the trigger reported success for a delivery its own sender declined to make; "
            "an operator reading this cannot tell a sent webhook from a blocked one"
        )
        # This assertion does real work: the wrapper's OTHER way to answer False is the
        # "no reporting_webhook configured" early return, which never reaches the sender.
        # Pinning that the sender WAS called (once, forced) is what makes this test grade
        # propagation rather than that early return. ANY on the three positionals — the
        # media buy, its reporting_webhook dict and the session are all fetched by the
        # wrapper itself, so the test holds no handle to them; `force` is the argument
        # this call is about.
        mock_send_internal.assert_called_once_with(ANY, ANY, ANY, force=True)


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_trigger_report_fails_gracefully_no_webhook(integration_db):
    """Test triggering report for media buy without webhook config."""

    tenant_id, principal_id = _create_test_tenant_and_principal()

    # Create media buy WITHOUT webhook
    with get_db_session() as session:
        from src.core.database.models import MediaBuy

        media_buy = MediaBuy(
            media_buy_id="mb_no_webhook",
            tenant_id=tenant_id,
            principal_id=principal_id,
            order_name="Test Order No Webhook",
            advertiser_name="Test Advertiser",
            start_date=datetime.now(UTC),
            end_date=datetime.now(UTC) + timedelta(days=7),
            status="active",
            raw_request={},  # No webhook config
        )
        session.add(media_buy)
        session.commit()

        # Refresh to attach to session
        media_buy = session.scalars(select(MediaBuy).filter_by(media_buy_id="mb_no_webhook")).first()

        scheduler = DeliveryWebhookScheduler()

        # Call public method
        result = await scheduler.trigger_report_for_media_buy_by_id(media_buy.media_buy_id, tenant_id)

        assert result is False

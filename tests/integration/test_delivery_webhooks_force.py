"""Integration tests for manual/forced delivery webhook triggering."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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
        patch("src.services.delivery_webhook_scheduler._get_media_buy_delivery_impl", return_value=mock_response),
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


class _MediaBuyIdMatcher:
    """Matcher pinning the media buy the wrapper passed on, by row identity not object identity.

    ``trigger_report_for_media_buy_by_id`` documents that it "manages its own
    database session to avoid detached instance errors" — so the ``MediaBuy``
    it hands to ``_send_report_for_media_buy`` is never the same Python object
    this test loaded in its own session, even though it's the same row.
    Comparing by ``media_buy_id`` is what actually catches a wrong-media-buy
    wiring bug; a bare identity/``ANY`` comparison can't.
    """

    def __init__(self, media_buy_id: str) -> None:
        self._media_buy_id = media_buy_id

    def __eq__(self, other: object) -> bool:
        from src.core.database.models import MediaBuy

        return isinstance(other, MediaBuy) and other.media_buy_id == self._media_buy_id

    def __repr__(self) -> str:
        return f"MediaBuy(media_buy_id={self._media_buy_id!r})"


class _SessionMatcher:
    """Matcher confirming a real Session was passed, without pinning which one.

    The wrapper opens a fresh session per call by design (see the docstring
    above) — asserting a specific session's identity would be asserting an
    implementation detail that is supposed to vary, not a wiring bug.
    """

    def __eq__(self, other: object) -> bool:
        from sqlalchemy.orm import Session

        return isinstance(other, Session)

    def __repr__(self) -> str:
        return "Session(...)"


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_trigger_report_for_media_buy_public_method(integration_db):
    """Test the public wrapper method trigger_report_for_media_buy."""

    # 1. Setup
    tenant_id, principal_id = _create_test_tenant_and_principal()
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id)

    scheduler = DeliveryWebhookScheduler()

    # Mock _send_report_for_media_buy to verify it receives force=True.
    # The public method now returns the send result, so the mock must
    # report a successful delivery.
    with patch.object(
        scheduler, "_send_report_for_media_buy", new_callable=AsyncMock, return_value=True
    ) as mock_send_internal:
        with get_db_session() as session:
            from src.core.database.models import MediaBuy

            media_buy = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()

            # 2. Call public method
            result = await scheduler.trigger_report_for_media_buy_by_id(media_buy_id, tenant_id)

            # 3. Verify result and call
            assert result is True
            mock_send_internal.assert_awaited_once_with(
                _MediaBuyIdMatcher(media_buy_id),
                media_buy.raw_request["reporting_webhook"],
                _SessionMatcher(),
                force=True,
            )


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

"""Integration-style tests for delivery webhook scheduler end-to-end behavior.

These tests:
- Use a real PostgreSQL database via the integration_db fixture
- Exercise DeliveryWebhookScheduler end-to-end for a single media buy
- Mock only the GAM reporting layer (get_media_buy_delivery + freshness) and outbound HTTP
"""

import pytz
from datetime import UTC, datetime, timedelta, time, timezone
from unittest.mock import AsyncMock, patch
from freezegun import freeze_time
import pytest

from src.adapters.gam_reporting_service import ReportingData
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, Tenant, PushNotificationConfig, PricingOption, Product, AdapterConfig
from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler

def _create_test_tenant_and_principal(
    ad_server: str | None = None
) -> tuple[str, str]:
    tenant_id = "tenant_integration"
    principal_id = "principal_integration"

    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Integration Tenant",
            subdomain="gam-pricing-test",
            ad_server="ad_server"
        )
        principal = Principal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Integration Principal",
            platform_mappings={"mock": {"advertiser_id": "adv_123"}},
            access_token="test-token"
        )

        if ad_server == "google_ad_manager":
            adapter_config = AdapterConfig(
                tenant_id=tenant_id,
                adapter_type="google_ad_manager",
                gam_network_code="123456",
                gam_trafficker_id="gam_traffic_456",
                gam_refresh_token="test_refresh_token"
            )
            session.add(adapter_config)
        
        session.add(tenant)
        session.add(principal)
        session.commit()
    
    return tenant_id, principal_id


def _create_basic_media_buy_with_webhook(
    tenant_id: str,
    principal_id: str,
    start_date=datetime.now(UTC).date() - timedelta(days=7),
    end_date=datetime.now(UTC).date() + timedelta(days=7)
) -> str:
    """Create a minimal tenant/principal/media_buy with a daily reporting_webhook.

    Returns:
        (tenant_id, principal_id, media_buy_id)
    """
    product_id = "sample_product_id"
    media_buy_id = "mb_integration"

    with get_db_session() as session:
        product = Product(
            tenant_id=tenant_id,
            product_id=product_id,
            name="My demo product",
            description="This is demo product for testing",
            format_ids=[],
            targeting_template={},
            delivery_type=""
        )

        pricing_option = PricingOption(
            tenant_id=tenant_id,
            pricing_model="cpm",
            rate=15.0,
            currency="EUR",
            is_fixed=False,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
            product_id=product.product_id
        )

        media_buy = MediaBuy(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            buyer_ref="buyer_ref_123",
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            start_date=start_date,
            end_date=end_date,
            status="active",
            raw_request={
                "packages": [{
                    "buyer_ref": "nike_web",
                    "product_id": product.product_id,
                    "pricing_option_id": pricing_option.id
                }],
                "reporting_webhook": {
                    "url": "https://example.com/webhook",  # outbound HTTP will be mocked
                    "frequency": "daily",
                }
            },
        )

        # session.add(product)
        # session.add(pricing_option)
        session.add(media_buy)
        session.commit()

    return media_buy_id


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_delivery_webhook_sends_for_fresh_data(integration_db):
    """Scheduler should call get_media_buy_delivery for the correct period and send webhook when data is fresh."""

    tenant_id, principal_id, = _create_test_tenant_and_principal()
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id)

    scheduler = DeliveryWebhookScheduler()

    async def fake_send_notification(*args, **kwargs):
        # Simulate successful webhook send without doing network I/O
        return True

    # Patch only webhook sending
    with (
        patch.object(
            scheduler.webhook_service,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ) as mock_send_notification
    ):
        # Run a single batch (no need to run the full hourly loop)
        await scheduler._send_reports()
        
        args, kwargs = mock_send_notification.await_args

        task_type = kwargs.get("task_type")
        task_id = kwargs.get("task_id")
        status = kwargs.get("status")
        push_notification_config = kwargs.get("push_notification_config")
        result = kwargs.get("result")
        error = kwargs.get("error")
        tenant_id = kwargs.get("tenant_id")
        principal_id = kwargs.get("principal_id")

        # Webhook should have been sent exactly once
        assert mock_send_notification.await_count == 1
        assert task_type == "media_buy_delivery"
        assert error is None
        assert tenant_id == tenant_id
        assert principal_id == principal_id
        assert media_buy_id == media_buy_id
        assert result is not None
        assert result.get("notification_type") == "scheduled"
        assert result.get("sequence_number") == 1
        assert result.get("next_expected_at") is not None
        assert result.get("frequency") == "daily"
        assert result.get("partial_data") is False
        assert result.get("unavailable_count") == 0
        assert result.get("reporting_period") is not None
        assert result.get("errors") is None

        yesterday = datetime.now(UTC).date() - timedelta(days=1)

        expected_start_date=(datetime.combine(yesterday, time.min)).isoformat()
        expected_end_date=(datetime.combine(yesterday, time.max)).isoformat()

        assert len(result.get('media_buy_deliveries')) == 1


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_delivery_webhook_sends_gam_based_reporting_data_only_on_gam_available_time(integration_db):
    """
    Scheduler should call webhook with fresh data every day at 4 AM PST but scheduler it self should keep checking to run every hour.
    """
    tenant_id, principal_id = _create_test_tenant_and_principal("google_ad_manager")
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id, start_date=datetime(2024, 12, 28, 1, 0, 0, tzinfo=timezone.utc), end_date=datetime(2026, 1, 1, 15, 0, 5, tzinfo=timezone.utc))

    scheduler = DeliveryWebhookScheduler()

    async def fake_send_notification(*args, **kwargs):
        # Simulate successful webhook send without doing network I/O
        return True

    # Create mocked GAM reporting data so we don't hit real GAM APIs
    now_utc = datetime.now(UTC)
    mocked_reporting_data = ReportingData(
        data=[
            {
                "timestamp": now_utc.isoformat(),
                "advertiser_id": "adv_123",
                "advertiser_name": "Test Advertiser",
                "order_id": "order_1",
                "order_name": "Test Order",
                "line_item_id": "line_1",
                "line_item_name": "Test Line Item",
                "country": "",
                "ad_unit_id": "",
                "ad_unit_name": "",
                "impressions": 1000,
                "clicks": 10,
                "ctr": 1.0,
                "spend": 100.0,
                "cpm": 100.0,
                "aggregated_rows": 1,

            }
        ],
        start_date=now_utc - timedelta(days=1),
        end_date=now_utc,
        requested_timezone="America/New_York",
        data_timezone="America/New_York",
        data_valid_until=now_utc + timedelta(hours=1),
        query_type="today",
        dimensions=["DATE"],
        metrics={
            "total_impressions": 1000,
            "total_clicks": 10,
            "total_spend": 100.0,
            "average_ctr": 1.0,
            "average_ecpm": 100.0,
            "unique_advertisers": 1,
            "unique_orders": 1,
            "unique_line_items": 1,
        },
    )

    with (
        patch.object(
            scheduler.webhook_service,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ) as mock_send_notification,
        patch("src.adapters.gam_reporting_service.GAMReportingService") as mock_reporting_service_class,
    ):
        # Ensure GoogleAdManager.get_media_buy_delivery uses mocked GAM reporting data
        mock_reporting_instance = mock_reporting_service_class.return_value
        mock_reporting_instance.get_reporting_data.return_value = mocked_reporting_data

        # Set time to 2 AM
        with freeze_time("2025-1-1 02:00:00"):
            await scheduler._send_reports()

            # Expect there's no webhook has been called
            assert mock_send_notification.await_count == 0

        # Set time to 3 AM
        with freeze_time("2025-1-1 03:00:00"):
            await scheduler._send_reports()
            
            # Expect there's no webhook has been called
            assert mock_send_notification.await_count == 0

        # Set time to 4 AM
        with freeze_time("2025-1-1 04:00:00"):
            await scheduler._send_reports()

            # Expect one webhook has been called
            assert mock_send_notification.await_count == 1

            # Check payload of the delivery
            args, kwargs = mock_send_notification.await_args
            
            result = kwargs.get("result")
            errors = result.get("errors")

            assert errors is None

        # Set time to 5 AM
        with freeze_time("2025-1-1 05:00:00"):
            await scheduler._send_reports()

            # Expect no webhook has been called
            assert mock_send_notification.await_count == 0

        # Set time to 4 AM next day
        with freeze_time("2025-1-2 04:00:00"):
            await scheduler._send_reports()

            # Expect one webhook has been called
            assert mock_send_notification.await_count == 1


# TODO: @yusuf - Test we don't call get_media_buy_delivery tool unless media_buy start date + frequency time has been passed
 
# TODO: @yusuf - Test we call get_media_buy_delivery tool one more time when media_buy is ended and we won't call anymore no matter if we reach the frequency

# TODO: @yusuf - also tests we pick up simulated path when context is in testing mode
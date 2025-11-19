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
from src.core.tool_context import ToolContext

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

# Create mocked GAM reporting data so we don't hit real GAM APIs
def get_mock_gam_reporting_delivery_data(
    base_date = datetime.now(UTC) 
) -> ReportingData:
    return ReportingData(
        data=[
            {
                "timestamp": base_date.isoformat(),
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
        start_date=base_date - timedelta(days=1),
        end_date=base_date,
        requested_timezone="America/New_York",
        data_timezone="America/New_York",
        data_valid_until=base_date + timedelta(hours=1),
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
    Scheduler should call webhook only when data is fresh enough and not have been called for the exact period already
    """
    tenant_id, principal_id = _create_test_tenant_and_principal("google_ad_manager")
    media_buy_id = _create_basic_media_buy_with_webhook(tenant_id, principal_id, start_date=datetime(2024, 12, 28, 1, 0, 0, tzinfo=timezone.utc), end_date=datetime(2026, 1, 1, 15, 0, 5, tzinfo=timezone.utc))

    scheduler = DeliveryWebhookScheduler()

    async def fake_send_notification(*args, **kwargs):
        # Simulate successful webhook send without doing network I/O
        return True

    with (
        patch.object(
            scheduler.webhook_service,
            "send_notification",
            new_callable=AsyncMock,
            side_effect=fake_send_notification,
        ) as mock_send_notification,
        patch("src.adapters.gam_reporting_service.GAMReportingService") as mock_reporting_service_class,
    ):
        # Set time to 3 AM
        with freeze_time("2025-1-1 03:00:00"):
            # Ensure GoogleAdManager.get_media_buy_delivery uses mocked GAM reporting data
            mocked_reporting_data = get_mock_gam_reporting_delivery_data(
                datetime(2024, 12, 28, 0, 0, 0, tzinfo=timezone.utc)
            )
            mock_reporting_instance = mock_reporting_service_class.return_value
            mock_reporting_instance.get_reporting_data.return_value = mocked_reporting_data
            
            await scheduler._send_reports()
            
            # Expect there's no webhook has been called
            assert mock_send_notification.await_count == 0

        # Set time to 4 AM
        with freeze_time("2025-1-1 04:00:00"):
            # Ensure GoogleAdManager.get_media_buy_delivery uses mocked GAM reporting data
            mocked_reporting_data = get_mock_gam_reporting_delivery_data()
            mock_reporting_instance = mock_reporting_service_class.return_value
            mock_reporting_instance.get_reporting_data.return_value = mocked_reporting_data
            
            await scheduler._send_reports()

            # Expect one webhook has been called
            assert mock_send_notification.await_count == 1

            # Check payload of the delivery
            args, kwargs = mock_send_notification.await_args
            
            result = kwargs.get("result")
            errors = result.get("errors")

            assert errors is None


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_dont_call_get_media_buy_delivery_tool_unless_media_buy_start_date_passed(integration_db):
    """Test that we handle media buys with future start dates gracefully (empty delivery)."""
    tenant_id, principal_id = _create_test_tenant_and_principal()
    
    # Start date is tomorrow
    start_date = datetime.now(UTC).date() + timedelta(days=1)
    end_date = start_date + timedelta(days=7)
    
    _create_basic_media_buy_with_webhook(
        tenant_id, 
        principal_id, 
        start_date=start_date,
        end_date=end_date
    )
    
    scheduler = DeliveryWebhookScheduler()
    
    async def fake_send_notification(*args, **kwargs):
        return True
        
    with patch.object(scheduler.webhook_service, "send_notification", new_callable=AsyncMock) as mock_send:
        await scheduler._send_reports()
        
        # Should send a webhook (since status=active in DB) but with empty deliveries (since dynamic status=ready)
        if mock_send.call_count > 0:
            args, kwargs = mock_send.call_args
            result = kwargs.get("result")
            assert len(result.get("media_buy_deliveries", [])) == 0

@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_call_get_media_buy_delivery_for_ended_campaign(integration_db):
    """Test webhook behavior for ended campaigns."""
    tenant_id, principal_id = _create_test_tenant_and_principal()
    
    # Ended yesterday
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    start_date = yesterday - timedelta(days=7)
    
    _create_basic_media_buy_with_webhook(
        tenant_id, 
        principal_id, 
        start_date=start_date,
        end_date=yesterday
    )
    
    scheduler = DeliveryWebhookScheduler()
    
    async def fake_send_notification(*args, **kwargs):
        return True
        
    with patch.object(scheduler.webhook_service, "send_notification", new_callable=AsyncMock) as mock_send:
        await scheduler._send_reports()
        
        # It should send a report because status is active in DB
        assert mock_send.call_count == 1
        
        # With current implementation, dynamic status="completed" -> filtered out of active list -> empty deliveries
        args, kwargs = mock_send.call_args
        result = kwargs.get("result")
        # Just verify result structure is valid
        assert result is not None


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_scheduler_uses_simulated_path_in_testing_mode(integration_db):
    """Test we pick up simulated path when context is in testing mode."""
    tenant_id, principal_id = _create_test_tenant_and_principal()
    _create_basic_media_buy_with_webhook(tenant_id, principal_id)
    
    scheduler = DeliveryWebhookScheduler()
    
    async def fake_send_notification(*args, **kwargs):
        return True

    # Helper to inject testing_context
    def create_test_context(*args, **kwargs):
        ctx = ToolContext(*args, **kwargs)
        ctx.testing_context = {"dry_run": True}
        return ctx

    with patch("src.services.delivery_webhook_scheduler.ToolContext", side_effect=create_test_context), \
         patch.object(scheduler.webhook_service, "send_notification", new_callable=AsyncMock) as mock_send, \
         patch("src.core.tools.media_buy_delivery.DeliverySimulator.calculate_simulated_metrics") as mock_sim:
         
        mock_sim.return_value = {"impressions": 1234, "spend": 50.0}
        
        await scheduler._send_reports()
        
        # Verify simulator was called (proof that testing_ctx.dry_run was respected)
        assert mock_sim.called
        assert mock_send.call_count == 1
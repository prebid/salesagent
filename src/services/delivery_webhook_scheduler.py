"""
Delivery Webhook Scheduler

Sends daily delivery reports via webhooks for media buys that have configured reporting_webhook.
This runs as a background task and sends reports when GAM data is fresh (after 4 AM PT daily).
"""
import asyncio
import logging
import os
import uuid
from datetime import UTC, date, datetime, timedelta, time
from typing import Any

import pytz
from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, WebhookDeliveryLog, PushNotificationConfig as DBPushNotificationConfig
from src.core.schemas import GetMediaBuyDeliveryRequest
from src.core.schema_adapters import GetMediaBuyDeliveryResponse
from src.core.tool_context import ToolContext
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)

# 1 hour because AdCP protocol has frequency options hourly, daily and monthly
# Configurable via env var for testing
SLEEP_INTERVAL_SECONDS = int(os.getenv("DELIVERY_WEBHOOK_INTERVAL", "3600"))


class DeliveryWebhookScheduler:
    """Scheduler for sending delivery reports via webhooks."""

    def __init__(self) -> None:
        self.webhook_service = get_protocol_webhook_service()
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the scheduler background task."""
        async with self._lock:
            if self.is_running:
                logger.warning("Delivery webhook scheduler is already running")
                return

            self.is_running = True
            self._task = asyncio.create_task(self._run_scheduler())
            logger.info("Delivery webhook scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler background task."""
        async with self._lock:
            if not self.is_running:
                return

            self.is_running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("Delivery webhook scheduler stopped")

    async def _run_scheduler(self) -> None:
        """Main scheduler loop - runs on a fixed hourly cadence."""
        while self.is_running:
            try:
                await self._send_reports()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in delivery webhook scheduler: {e}", exc_info=True)
            finally:
                # Wait 1 hour before next batch, even if there was an error
                await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def _send_reports(self) -> None:
        """Send reports for all active media buys with configured webhooks."""
        logger.info("Starting scheduled delivery report webhook batch")

        try:
            with get_db_session() as session:
                # Find all active media buys
                stmt = select(MediaBuy).where(MediaBuy.status.in_(["active", "approved"]))
                media_buys = session.scalars(stmt).all()

                reports_sent = 0
                errors = 0

                for media_buy in media_buys:
                    try:
                        # Check if this media buy has a reporting webhook configured
                        raw_request = media_buy.raw_request or {}
                        reporting_webhook = raw_request.get("reporting_webhook")

                        if not reporting_webhook:
                            continue

                        # Send delivery report
                        await self._send_report_for_media_buy(media_buy, reporting_webhook, session)
                        reports_sent += 1

                    except Exception as e:
                        logger.error(f"Error sending report for media buy {media_buy.media_buy_id}: {e}", exc_info=True)
                        errors += 1

                logger.info(f"Daily delivery report batch complete: {reports_sent} sent, {errors} errors")

        except Exception as e:
            logger.error(f"Error in daily delivery report batch: {e}", exc_info=True)

    async def _send_report_for_media_buy(self, media_buy: Any, reporting_webhook: dict, session: Any) -> None:
        """Send a delivery report for a single media buy.

        Args:
            media_buy: MediaBuy database model
            reporting_webhook: Webhook configuration dict
            session: Database session
        """
        try:
            # Determine reporting frequency from AdCP config (hourly, daily, monthly)
            raw_freq = str(reporting_webhook.get("frequency") or "daily").lower()

            if raw_freq != "daily":
                logger.warning(
                    "Skipping reporting webhook with frequency '%s' for media buy %s – "
                    "only 'daily' frequency is supported for delivery webhooks at this time",
                    raw_freq,
                    media_buy.media_buy_id,
                )
                return

            # Calculate reporting period for daily frequency: yesterday (full day)
            start_date_obj = datetime.now(UTC).date() - timedelta(days=1)
            end_date_obj = datetime.now(UTC)

            # Check if we've already sent a scheduled delivery_report webhook for this media buy
            # and reporting date. We use created_at::date as the period key.
            existing_stmt = (
                select(WebhookDeliveryLog)
                .where(
                    WebhookDeliveryLog.media_buy_id == media_buy.media_buy_id,
                    WebhookDeliveryLog.task_type == "delivery_report",
                    WebhookDeliveryLog.notification_type == "scheduled",
                    WebhookDeliveryLog.status == "success",
                    func.date(WebhookDeliveryLog.created_at) == end_date_obj,
                )
            )
            existing_log = session.scalars(existing_stmt).first()
            if existing_log:
                logger.info(
                    "Skipping daily delivery webhook for media buy %s and date %s – already sent (log id %s)",
                    media_buy.media_buy_id,
                    end_date_obj,
                    existing_log.id,
                )
                return

            # Fetch delivery metrics
            # Create a minimal context object for the delivery call

            context = ToolContext(
                context_id=str(uuid.uuid4()),
                tenant_id=media_buy.tenant_id,
                principal_id=media_buy.principal_id,
                tool_name="get_media_buy_delivery",
                request_timestamp=datetime.now(UTC),
            )

            req = GetMediaBuyDeliveryRequest(  # type: ignore[call-arg]
                media_buy_ids=[media_buy.media_buy_id],
                start_date=start_date_obj.strftime("%Y-%m-%d"),
                end_date=end_date_obj.strftime("%Y-%m-%d"),
            )

            delivery_response = _get_media_buy_delivery_impl(req, context)

            if not isinstance(delivery_response, GetMediaBuyDeliveryResponse):
                logger.warning(f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. Result is {delivery_response.model_dump()}")
                return

            if delivery_response.errors is not None:
                logger.warning(f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. We have recieved error in the result. Result is {delivery_response.model_dump()}")
                return

            # Get sequence number for this webhook (get max sequence + 1)
            sequence_number = 1
            try:
                stmt = select(func.coalesce(func.max(WebhookDeliveryLog.sequence_number), 0)).where(
                    WebhookDeliveryLog.media_buy_id == media_buy.media_buy_id,
                    WebhookDeliveryLog.task_type == "media_buy_delivery",
                )
                max_seq = session.scalar(stmt)
                sequence_number = (max_seq or 0) + 1
            except Exception as e:
                logger.warning(f"Could not get sequence number for media buy {media_buy.media_buy_id}: {e}")

            # Calculate next_expected_at for daily frequency: start of next day (UTC)
            next_day = datetime.now(UTC).date() + timedelta(days=1)
            next_expected_at = datetime.combine(next_day, datetime.min.time(), tzinfo=UTC).isoformat()

            # Add webhook-specific metadata to the response
            response_dict = delivery_response.model_dump()
            response_dict.update(
                {
                    "notification_type": "scheduled",
                    "sequence_number": sequence_number,
                    "next_expected_at": next_expected_at,
                    "frequency": raw_freq,
                    "partial_data": False,  # TODO: Check for reporting_delayed status in media_buy_deliveries
                    "unavailable_count": 0,  # TODO: Count reporting_delayed/failed deliveries
                }
            )

            # Extract webhook URL and authentication
            webhook_url = reporting_webhook.get("url")
            if not webhook_url:
                logger.warning(f"No webhook URL configured for media buy {media_buy.media_buy_id}")
                return

            # Try to find existing push notification config or create a temporary one
            auth_config = reporting_webhook.get("authentication", {})
            auth_type = None
            auth_token = None

            if auth_config:
                schemes = auth_config.get("schemes", [])
                auth_type = schemes[0] if schemes else None
                auth_token = auth_config.get("credentials")

            # Query for existing push notification config for this media buy
            config_stmt = select(DBPushNotificationConfig).where(
                DBPushNotificationConfig.principal_id == media_buy.principal_id,
                DBPushNotificationConfig.tenant_id == media_buy.tenant_id,
                DBPushNotificationConfig.url == webhook_url,
                DBPushNotificationConfig.is_active,
            )
            push_config = session.scalars(config_stmt).first()

            # Extract webhook config data before session closes
            if push_config:
                # Detach from session and extract data
                session.expunge(push_config)
                webhook_config = push_config
            else:
                # Create a detached temporary config (not attached to session)
                webhook_config = DBPushNotificationConfig(
                    id=f"temp_{media_buy.media_buy_id}",
                    tenant_id=media_buy.tenant_id,
                    principal_id=media_buy.principal_id,
                    url=webhook_url,
                    authentication_type=auth_type,
                    authentication_token=auth_token,
                    is_active=True,
                )

            # Send webhook notification OUTSIDE the session context
            # This ensures the session is closed before async webhook call
            await self.webhook_service.send_notification(
                task_type="media_buy_delivery",
                task_id=media_buy.media_buy_id,
                status="completed",
                push_notification_config=webhook_config,
                result=response_dict,  # Use modified dict with webhook metadata
                tenant_id=media_buy.tenant_id,
                principal_id=media_buy.principal_id,
                media_buy_id=media_buy.media_buy_id
            )

            logger.info(f"Sent delivery report webhook for media buy {media_buy.media_buy_id}")

        except Exception as e:
            logger.error(f"Error sending delivery report for media buy {media_buy.media_buy_id}: {e}", exc_info=True)
            raise


# Global scheduler instance
_scheduler: DeliveryWebhookScheduler | None = None


def get_delivery_webhook_scheduler() -> DeliveryWebhookScheduler:
    """Get or create global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = DeliveryWebhookScheduler()
    return _scheduler


async def start_delivery_webhook_scheduler():
    """Start the delivery webhook scheduler (called at application startup)."""
    scheduler = get_delivery_webhook_scheduler()
    await scheduler.start()


async def stop_delivery_webhook_scheduler():
    """Stop the delivery webhook scheduler (called at application shutdown)."""
    scheduler = get_delivery_webhook_scheduler()
    await scheduler.stop()

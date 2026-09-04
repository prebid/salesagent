"""
Delivery Webhook Scheduler

Sends daily delivery reports via webhooks for media buys that have configured reporting_webhook.
This runs as a background task and sends reports when GAM data is fresh (after 4 AM PT daily).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    NotificationType,
)  # TODO: no stable alias — response-level NotificationType differs from top-level
from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import PersistedMediaBuyStatus, WebhookDeliveryLog
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.database.repositories import MediaBuyRepository
from src.core.exceptions import AdCPValidationError
from src.core.schemas import GetMediaBuyDeliveryRequest, GetMediaBuyDeliveryResponse
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.utils import utc_flight_start
from src.core.webhooks.delivery import WebhookTaskContext
from src.core.webhooks.registration import accept_push_notification_config
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)

# 1 hour because AdCP protocol has frequency options hourly, daily and monthly
# Configurable via env var for testing
SLEEP_INTERVAL_SECONDS = int(os.getenv("DELIVERY_WEBHOOK_INTERVAL") or "3600")


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
        """Main scheduler loop - runs on a fixed hourly cadence.

        Sends immediately on startup (duplicate check prevents re-sending if
        already sent in last 24 hours), then continues on hourly cadence.
        """
        while self.is_running:
            try:
                await self._send_reports()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in delivery webhook scheduler: {e}", exc_info=True)
            finally:
                # Wait before next batch
                await asyncio.sleep(SLEEP_INTERVAL_SECONDS)

    async def _send_reports(self) -> None:
        """Send reports for all active media buys with configured webhooks."""
        logger.info("Starting scheduled delivery report webhook batch")

        try:
            with get_db_session() as session:
                # Find all active media buys (cross-tenant scheduler query)
                media_buys = MediaBuyRepository.get_all_by_statuses(
                    session, {PersistedMediaBuyStatus.ACTIVE, PersistedMediaBuyStatus.APPROVED}
                )

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

    async def trigger_report_for_media_buy_by_id(self, media_buy_id: str, tenant_id: str) -> bool:
        """Manually trigger a delivery report for a single media buy by ID.

        This method manages its own database session to avoid detached instance errors.

        Args:
            media_buy_id: The media buy ID
            tenant_id: The tenant ID

        Returns:
            bool: True if report was triggered successfully, False otherwise
        """
        try:
            with get_db_session() as session:
                repo = MediaBuyRepository(session, tenant_id)
                media_buy = repo.get_by_id(media_buy_id)

                if not media_buy:
                    logger.warning(f"Cannot trigger report: Media buy {media_buy_id} not found")
                    return False

                raw_request = media_buy.raw_request or {}
                reporting_webhook = raw_request.get("reporting_webhook")

                if not reporting_webhook:
                    logger.warning(f"Cannot trigger report: No reporting_webhook configured for {media_buy_id}")
                    return False

                # Force sending even if already sent today (for testing)
                await self._send_report_for_media_buy(media_buy, reporting_webhook, session, force=True)
                return True
        except Exception as e:
            logger.error(f"Error manually triggering report for {media_buy_id}: {e}", exc_info=True)
            return False

    async def _send_report_for_media_buy(
        self, media_buy: Any, reporting_webhook: dict, session: Any, force: bool = False
    ) -> None:
        """Send a delivery report for a single media buy.

        Args:
            media_buy: MediaBuy database model
            reporting_webhook: Webhook configuration dict
            session: Database session
            force: If True, bypass frequency checks and duplicate checks
        """
        try:
            # Determine reporting frequency from AdCP config (hourly, daily, monthly)
            raw_freq = str(reporting_webhook.get("frequency") or "daily").lower()

            if not force and raw_freq != "daily":
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
            if not force:
                # Look back 24 hours to find recent successful webhooks
                one_day_ago = datetime.now(UTC) - timedelta(hours=24)
                existing_stmt = select(WebhookDeliveryLog).where(
                    WebhookDeliveryLog.media_buy_id == media_buy.media_buy_id,
                    WebhookDeliveryLog.task_type == "media_buy_delivery",
                    WebhookDeliveryLog.notification_type == "scheduled",
                    WebhookDeliveryLog.status == "success",
                    WebhookDeliveryLog.created_at > one_day_ago,
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
            # Create a ResolvedIdentity for the delivery call
            from src.core.resolved_identity import ResolvedIdentity

            identity = ResolvedIdentity(
                principal_id=media_buy.principal_id,
                tenant_id=media_buy.tenant_id,
                tenant={"tenant_id": media_buy.tenant_id},
                protocol="rest",
            )

            # Include active + completed statuses: the scheduler already filters
            # by DB status (active/approved) at query time, so the delivery impl
            # should include ended campaigns (dynamic status=completed) rather
            # than filtering them out and reporting "not found" errors.
            # We exclude "pending_start" (ready) to avoid returning delivery
            # data for future-dated campaigns that haven't started yet.
            from adcp.types import MediaBuyStatus

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=[media_buy.media_buy_id],
                status_filter=[MediaBuyStatus.active, MediaBuyStatus.completed],
                start_date=start_date_obj.strftime("%Y-%m-%d"),
                end_date=end_date_obj.strftime("%Y-%m-%d"),
                context=None,
            )

            delivery_response = _get_media_buy_delivery_impl(req, identity)

            if not isinstance(delivery_response, GetMediaBuyDeliveryResponse):
                logger.warning(
                    f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. Result is {delivery_response.model_dump()}"
                )
                return

            if delivery_response.errors is not None:
                logger.warning(
                    f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. We have recieved error in the result. Result is {delivery_response.model_dump()}"
                )
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
            next_expected_at = utc_flight_start(next_day)

            # Set webhook-specific metadata directly on the response model
            # These fields are defined on the library's GetMediaBuyDeliveryResponse
            delivery_response.notification_type = NotificationType.scheduled
            delivery_response.next_expected_at = next_expected_at
            delivery_response.partial_data = False  # TODO: Check for reporting_delayed status
            delivery_response.unavailable_count = 0  # TODO: Count reporting_delayed/failed deliveries

            # Extract webhook URL and authentication
            webhook_url = reporting_webhook.get("url")
            if not webhook_url:
                logger.warning(f"No webhook URL configured for media buy {media_buy.media_buy_id}")
                return

            # A stored row still wins: a real registration outranks whatever the
            # request carried inline.
            config_stmt = select(DBPushNotificationConfig).where(
                DBPushNotificationConfig.principal_id == media_buy.principal_id,
                DBPushNotificationConfig.tenant_id == media_buy.tenant_id,
                DBPushNotificationConfig.url == webhook_url,
                DBPushNotificationConfig.is_active,
            )
            push_notification_config = session.scalars(config_stmt).first()

            if push_notification_config:
                # Detach from session and extract data
                session.expunge(push_notification_config)
            else:
                # No stored row: the sender is handed the GATE'S VALUE, not a
                # config-shaped object built here. send_notification takes
                # DeliverableWebhookTarget, which ValidatedWebhookRegistration
                # satisfies — the same migration made on the A2A path (#1802) when it
                # deleted the detached row fabricated "purely to type-check".
                #
                # This also retires a schemes[0] read. The pinned AuthenticationScheme
                # permits AT MOST ONE scheme, so a two-scheme document must be REFUSED;
                # picking the first silently delivered under a scheme the buyer did not
                # solely request. The gate owns that rule — re-checking len(schemes)
                # here would be a second vocabulary for it.
                #
                # The authentication block is forwarded only when TRUTHY, preserving
                # the previous `if auth_config:` exactly: an empty dict keeps meaning
                # "no authentication" and keeps delivering unsigned. The gate refuses
                # an empty block, and turning today's unsigned delivery into a refusal
                # is a delivered -> never-delivered change that needs an owner sign-off
                # (#1802 made one for a legacy scheme); it is not a refactor's to make.
                registration_config = {"url": webhook_url}
                if reporting_webhook.get("authentication"):
                    registration_config["authentication"] = reporting_webhook["authentication"]

                try:
                    push_notification_config = accept_push_notification_config(
                        registration_config, field_prefix="reporting_webhook"
                    )
                except AdCPValidationError as exc:
                    # Outside any request context: a refusal is a logged non-delivery,
                    # never an exception that kills the scheduler loop.
                    logger.warning(
                        f"Refusing to send delivery report for media buy {media_buy.media_buy_id}: "
                        f"its reporting_webhook registration is invalid ({exc})"
                    )
                    return

            # Wire vs internal task_type distinction:
            # - metadata["task_type"] = "media_buy_delivery" -- internal logging/dedup label
            #   used by protocol_webhook_service guards and WebhookDeliveryLog queries.
            # - SDK task_type = "update_media_buy" -- AdCP spec TaskType enum value
            #   for the wire payload (delivery reports are status updates on media buys).
            # These are intentionally different: the internal label predates the SDK enum
            # and is used for DB filtering, while the wire value must be spec-compliant.
            # Renaming the metadata key is not safe without migrating DB records and
            # updating all 6 protocol_webhook_service guard checks.
            # sequence_number and notification_type are NAMED here, from the values
            # this function computed above (:251-259 and :269), not left at 1/None.
            # They used to be hardcoded, and it did not show: the sender flattened
            # this context to a four-key dict and then re-derived both from the
            # PAYLOAD, which carries the same two values. Now that the typed context
            # travels the whole way to `webhook_delivery_log`, a hardcoded value here
            # would BE the persisted value -- so the caller that knows them states
            # them, which is the point of taking a typed context at all.
            webhook_task = WebhookTaskContext(
                task_id=media_buy.media_buy_id,
                task_type="media_buy_delivery",
                tenant_id=media_buy.tenant_id,
                principal_id=media_buy.principal_id,
                media_buy_id=media_buy.media_buy_id,
                sequence_number=sequence_number,
                notification_type=delivery_response.notification_type.value
                if delivery_response.notification_type is not None
                else None,
            )

            # The dialect comes from the REGISTRATION, not from a hardcoded builder.
            # This job used to call create_mcp_webhook_payload unconditionally, so a
            # buyer that registered over A2A received an MCP-shaped delivery report.
            # It had no way to do better until push_notification_configs recorded the
            # protocol: this job fires long after the request and carries no identity
            # (#1802).
            #
            # NULL means a row written before that column existed. Falling back to
            # "mcp" reproduces exactly the previous behaviour for those rows rather
            # than guessing a dialect the data never stated.
            protocol = getattr(push_notification_config, "protocol", None) or "mcp"

            # Send webhook notification OUTSIDE the session context
            # This ensures the session is closed before async webhook call
            await self.webhook_service.notify(
                push_notification_config,
                task=webhook_task,
                # Delivery reports are status updates on existing media buys, so the
                # wire task_type resolves to update_media_buy; the internal label on
                # the context stays "media_buy_delivery" for the guards and the
                # delivery-log column.
                status=AdcpTaskStatus.completed,
                result=delivery_response,
                protocol=protocol,
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

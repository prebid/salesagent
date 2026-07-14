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

from adcp import create_mcp_webhook_payload
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    NotificationType,
)  # TODO: no stable alias — response-level NotificationType differs from top-level
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.helpers import enum_value
from src.core.schemas import GetMediaBuyDeliveryRequest, GetMediaBuyDeliveryResponse
from src.core.tools._media_buy_status import (
    REPORTABLE_CANONICAL_STATUSES,
    SERVING_PERSISTED_STATUSES,
    derive_notification_type,
    resolve_canonical_status,
)
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.utils import utc_flight_start
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
                # Find all serving media buys (cross-tenant scheduler query).
                # Uses the derived serving set so legacy aliases ("ready" /
                # "scheduled") are included — a hardcoded partial list stranded
                # them without webhooks (#1556).
                media_buys = MediaBuyRepository.get_all_by_statuses(session, sorted(SERVING_PERSISTED_STATUSES))

                reports_sent = 0
                errors = 0

                for media_buy in media_buys:
                    try:
                        # Check if this media buy has a reporting webhook configured
                        raw_request = media_buy.raw_request or {}
                        reporting_webhook = raw_request.get("reporting_webhook")

                        if not reporting_webhook:
                            continue

                        # The status-only selection also matches pre-flight and
                        # paused rows the impl cannot report on. Resolve the
                        # same canonical status the impl would and skip them
                        # here, instead of invoking the full delivery impl
                        # every hour only to misread its MEDIA_BUY_NOT_FOUND
                        # advisory as a warning-worthy failure.
                        canonical = resolve_canonical_status(media_buy, datetime.now(UTC).date())
                        if canonical not in REPORTABLE_CANONICAL_STATUSES:
                            continue

                        # Send delivery report; only count it when a webhook
                        # actually went out (dedup/frequency skips return False).
                        if await self._send_report_for_media_buy(media_buy, reporting_webhook, session):
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
                return await self._send_report_for_media_buy(media_buy, reporting_webhook, session, force=True)
        except Exception as e:
            logger.error(f"Error manually triggering report for {media_buy_id}: {e}", exc_info=True)
            return False

    async def _send_report_for_media_buy(
        self, media_buy: Any, reporting_webhook: dict, session: Any, force: bool = False
    ) -> bool:
        """Send a delivery report for a single media buy.

        Args:
            media_buy: MediaBuy database model
            reporting_webhook: Webhook configuration dict
            session: Database session
            force: If True, bypass frequency checks and duplicate checks

        Returns:
            True when a webhook was actually delivered; False when the buy was
            legitimately skipped (unsupported frequency, dedup, no data, no
            URL). A failed delivery RAISES so the caller counts it as an
            error instead of a send.
        """
        try:
            delivery_repo = DeliveryRepository(session, media_buy.tenant_id)

            # Determine reporting frequency from AdCP config (hourly, daily, monthly)
            raw_freq = str(reporting_webhook.get("frequency") or "daily").lower()

            if not force and raw_freq != "daily":
                logger.warning(
                    "Skipping reporting webhook with frequency '%s' for media buy %s – "
                    "only 'daily' frequency is supported for delivery webhooks at this time",
                    raw_freq,
                    media_buy.media_buy_id,
                )
                return False

            # Calculate reporting period for daily frequency: yesterday (full day)
            start_date_obj = datetime.now(UTC).date() - timedelta(days=1)
            end_date_obj = datetime.now(UTC)

            # Check if we've already sent a delivery report webhook for this
            # media buy within the last 24 hours (rolling window on created_at,
            # success rows only). Any notification_type counts (#1570): a sent
            # "final" must also dedup within the window — the durable stopper
            # is the status scheduler flipping the buy out of the serving
            # selection, not this check.
            if not force:
                # Look back 24 hours to find recent successful webhooks (any
                # notification_type — the broadened #1570 dedup). Tenant-scoped
                # via the repository.
                one_day_ago = datetime.now(UTC) - timedelta(hours=24)
                existing_log = delivery_repo.get_recent_successful_log(
                    media_buy.media_buy_id, task_type="media_buy_delivery", since=one_day_ago
                )
                if existing_log:
                    logger.info(
                        "Skipping daily delivery webhook for media buy %s and date %s – already sent (log id %s)",
                        media_buy.media_buy_id,
                        end_date_obj,
                        existing_log.id,
                    )
                    return False

            # Fetch delivery metrics
            # Create a ResolvedIdentity for the delivery call
            from src.core.resolved_identity import ResolvedIdentity

            identity = ResolvedIdentity(
                principal_id=media_buy.principal_id,
                tenant_id=media_buy.tenant_id,
                tenant={"tenant_id": media_buy.tenant_id},
                protocol="rest",
            )

            # The impl reports on exactly REPORTABLE_CANONICAL_STATUSES: the
            # scheduler already filters by persisted DB status
            # (SERVING_PERSISTED_STATUSES) at query time and skips buys that
            # resolve outside the reportable set, so ended campaigns (dynamic
            # status=completed) are included rather than filtered out and
            # reported as "not found" errors.
            from adcp.types import MediaBuyStatus

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=[media_buy.media_buy_id],
                status_filter=[MediaBuyStatus(s) for s in sorted(REPORTABLE_CANONICAL_STATUSES)],
                start_date=start_date_obj.strftime("%Y-%m-%d"),
                end_date=end_date_obj.strftime("%Y-%m-%d"),
                context=None,
            )

            delivery_response = _get_media_buy_delivery_impl(req, identity)

            if not isinstance(delivery_response, GetMediaBuyDeliveryResponse):
                logger.warning(
                    f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. Result is {delivery_response.model_dump()}"
                )
                return False

            if delivery_response.errors is not None:
                logger.warning(
                    f"`Couldn't get media_delivery` for {media_buy.media_buy_id}. We have recieved error in the result. Result is {delivery_response.model_dump()}"
                )
                return False

            # Sequence number for this webhook: max SUCCESSFULLY DELIVERED
            # sequence + 1 (spec: "Sequential notification number ... starts at
            # 1"). Failed/retrying sends also log the sequence they attempted;
            # counting them — while the dedup above counts only successes —
            # would burn numbers the buyer never received, so a buyer's
            # first-ever webhook could start above 1. A query failure
            # propagates and aborts this send loudly: a quiet fallback to 1
            # would put an already-consumed sequence on the wire.
            sequence_number = (
                delivery_repo.get_max_sequence_number(media_buy.media_buy_id, task_type="media_buy_delivery") + 1
            )

            # Set webhook-specific metadata directly on the response model (#1570).
            # These fields are webhook-only ("only present in webhook deliveries" —
            # get-media-buy-delivery-response.json @ v3.1-04f59d2d5), so the polling
            # impl never sets them; this webhook path is the single place they are
            # attached to the wire.
            #
            # notification_type: derived from the reported statuses — "final" when
            # every buy will never produce more data ("one final notification when
            # the campaign completes", optimization-reporting.mdx §Publisher
            # Commitment), "scheduled" otherwise.
            derived = derive_notification_type(
                enum_value(d.status) for d in delivery_response.media_buy_deliveries or []
            )
            delivery_response.notification_type = NotificationType(derived) if derived else None

            # next_expected_at: only present when notification_type is not "final"
            # (spec, same schema — a non-nullable date-time, so a final webhook
            # must OMIT the field; leaving it None lets the response's
            # exclude-None serialization drop it from the wire). Daily
            # frequency -> start of next day (UTC).
            if derived == "final":
                delivery_response.next_expected_at = None
            elif derived == "scheduled":
                next_day = datetime.now(UTC).date() + timedelta(days=1)
                delivery_response.next_expected_at = utc_flight_start(next_day)
            # derived is None (zero deliveries) -> leave next_expected_at unset;
            # notification_type is None too, so the pair stays consistent.

            delivery_response.sequence_number = sequence_number
            delivery_response.partial_data = False  # TODO: Check for reporting_delayed status
            delivery_response.unavailable_count = 0  # TODO: Count reporting_delayed/failed deliveries

            # Extract webhook URL and authentication
            webhook_url = reporting_webhook.get("url")
            if not webhook_url:
                logger.warning(f"No webhook URL configured for media buy {media_buy.media_buy_id}")
                return False

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
            push_notification_config = session.scalars(config_stmt).first()

            # Extract webhook config data before session closes
            if push_notification_config:
                # Detach from session and extract data
                session.expunge(push_notification_config)
            else:
                # Create a detached temporary config (not attached to session)
                push_notification_config = DBPushNotificationConfig(
                    id=f"temp_{media_buy.media_buy_id}",
                    tenant_id=media_buy.tenant_id,
                    principal_id=media_buy.principal_id,
                    url=webhook_url,
                    authentication_type=auth_type,
                    authentication_token=auth_token,
                    is_active=True,
                )

            # Wire vs internal task_type distinction:
            # - metadata["task_type"] = "media_buy_delivery" -- internal logging/dedup label
            #   used by protocol_webhook_service guards and WebhookDeliveryLog queries.
            # - SDK task_type = "update_media_buy" -- AdCP spec TaskType enum value
            #   for the wire payload (delivery reports are status updates on media buys).
            # These are intentionally different: the internal label predates the SDK enum
            # and is used for DB filtering, while the wire value must be spec-compliant.
            # Renaming the metadata key is not safe without migrating DB records and
            # updating all 6 protocol_webhook_service guard checks.
            metadata = {
                "task_type": "media_buy_delivery",
                "tenant_id": media_buy.tenant_id,
                "principal_id": media_buy.principal_id,
                "media_buy_id": media_buy.media_buy_id,
            }

            # SDK 5.7: returns McpWebhookPayload directly; 3rd arg is task_type.
            # Delivery reports are status updates on existing media buys,
            # so we use update_media_buy as the canonical task type.
            media_buy_delivery_payload = create_mcp_webhook_payload(
                task_id=media_buy.media_buy_id,
                task_type="update_media_buy",
                result=delivery_response,
                status=AdcpTaskStatus.completed,
            )

            # Send webhook notification OUTSIDE the session context
            # This ensures the session is closed before async webhook call
            delivered = await self.webhook_service.send_notification(
                push_notification_config=push_notification_config, payload=media_buy_delivery_payload, metadata=metadata
            )

            if not delivered:
                # send_notification returns False (never raises) on permanent
                # 4xx / exhausted retries and has already written the failed
                # WebhookDeliveryLog row. Raise so the batch counts an error
                # instead of logging "Sent" for a webhook the buyer never got.
                raise RuntimeError(
                    f"Delivery report webhook send failed for media buy {media_buy.media_buy_id} "
                    "(see webhook service logs for the HTTP failure detail)"
                )

            logger.info(f"Sent delivery report webhook for media buy {media_buy.media_buy_id}")
            return True

        except Exception as e:
            # Re-raise for the caller (batch loop / manual trigger) to own the
            # single ERROR line. Log at DEBUG here to avoid a duplicate full
            # traceback on the common send_notification -> False path.
            logger.debug(
                "Error sending delivery report for media buy %s: %s", media_buy.media_buy_id, e, exc_info=True
            )
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

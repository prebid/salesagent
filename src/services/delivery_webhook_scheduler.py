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
from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
    NotificationType,
)  # TODO: no stable alias — response-level NotificationType differs from top-level
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.helpers import enum_value
from src.core.schemas import GetMediaBuyDeliveryRequest, GetMediaBuyDeliveryResponse
from src.core.tools._media_buy_status import (
    CANONICAL_COMPLETED,
    COMPLETED_PERSISTED_STATUSES,
    REPORTABLE_CANONICAL_STATUSES,
    SERVING_PERSISTED_STATUSES,
    derive_notification_type,
    resolve_canonical_status,
)
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.utils import utc_flight_start
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)

# 1 hour because AdCP protocol has frequency options hourly, daily and monthly.
# The shipped default is a named constant so invariants about the production
# cadence (e.g. the completed-selection horizon test) can bind to it rather than
# to the env-resolved value — test runs export DELIVERY_WEBHOOK_INTERVAL=5,
# which would otherwise collapse a derived bound to seconds.
DEFAULT_SLEEP_INTERVAL_SECONDS = 3600
# Configurable via env var for testing
SLEEP_INTERVAL_SECONDS = int(os.getenv("DELIVERY_WEBHOOK_INTERVAL") or str(DEFAULT_SLEEP_INTERVAL_SECONDS))

# Lease for the best-effort atomic "final webhook" claim (#1575). A claim older
# than this is treated as stale (crashed/failed worker) and can be re-claimed, so
# a stuck claim never strands the final. Comfortably longer than a real send
# (seconds) so an in-flight send is never reclaimed, and shorter than the hourly
# batch so a failed/crashed final is retried on the next batch.
FINAL_WEBHOOK_CLAIM_LEASE = timedelta(minutes=15)

# Recency horizon bounding which persisted-"completed" buys the batch selects
# (see MediaBuyRepository.get_reportable_for_delivery). "completed" is permanent,
# so an unbounded selection would scan every completed buy that ever existed on
# every hourly batch. INVARIANT (pinned by a unit test): the horizon must be much
# longer than both FINAL_WEBHOOK_CLAIM_LEASE (so stale-lease recovery always
# happens on a still-selected buy) and the batch interval (so the ~60s status
# flip is always caught) — 2 days gives ~48x margin over the hourly batch.
FINAL_WEBHOOK_COMPLETED_HORIZON = timedelta(days=2)


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
                # Find all reportable media buys (cross-tenant scheduler query):
                # the serving set (incl. legacy aliases "ready"/"scheduled" —
                # #1556) PLUS terminal "completed". Completed is REQUIRED: the
                # status scheduler flips an ended buy to persisted "completed"
                # within ~60s, long before this hourly batch, so a serving-only
                # selection would drop it and the buy's spec-required FINAL webhook
                # would never be sent. The per-buy final gate below de-dups it on a
                # best-effort basis (true exactly-once is #1606). The completed arm
                # is bounded by a recency horizon on updated_at so the hourly scan
                # doesn't grow forever (see get_reportable_for_delivery).
                media_buys = MediaBuyRepository.get_reportable_for_delivery(
                    session,
                    serving_statuses=sorted(SERVING_PERSISTED_STATUSES),
                    completed_statuses=sorted(COMPLETED_PERSISTED_STATUSES),
                    completed_horizon=FINAL_WEBHOOK_COMPLETED_HORIZON,
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
                        logger.error(
                            "Error sending report for media buy %s (tenant %s, principal %s): %s",
                            media_buy.media_buy_id,
                            media_buy.tenant_id,
                            media_buy.principal_id,
                            e,
                            exc_info=True,
                        )
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

                # force bypasses the frequency + 24h "scheduled" dedup so an
                # operator can re-send a fresh periodic report. It does NOT bypass
                # the final gate: a completed buy whose final was already delivered
                # is still skipped, so a manual trigger won't duplicate the final on
                # the read-check path (best-effort; #1606 for true exactly-once).
                return await self._send_report_for_media_buy(media_buy, reporting_webhook, session, force=True)
        except Exception as e:
            logger.error(f"Error manually triggering report for {media_buy_id}: {e}", exc_info=True)
            return False

    def _should_skip_send(
        self, delivery_repo: DeliveryRepository, media_buy: MediaBuy, *, is_final: bool, force: bool
    ) -> bool:
        """BEST-EFFORT read-only de-dup — True if this delivery webhook should NOT be sent.

        NOT a hard exactly-once guarantee. This is a pure read decision; the atomic
        concurrency CLAIM is taken later, just before the POST (see _deliver_report),
        so definitive no-send paths before the POST never hold a claim.
          - final: skip if a SUCCESSFUL "final" was already logged for this buy.
            Applies EVEN under ``force`` — a manual re-trigger must never duplicate a
            delivered final. Keys on a *successful* final, so a retry after a FAILED
            final still goes through, and it fires regardless of the 24h window (so
            the status scheduler flipping the buy to persisted "completed" before this
            hourly batch can't leave the spec-required final unsent).
          - scheduled: 24h rolling dedup, bypassed by ``force`` so an operator can
            re-send a fresh periodic report on demand.
        """
        if is_final:
            if delivery_repo.has_successful_final(media_buy.media_buy_id, task_type="media_buy_delivery"):
                logger.info("Final delivery webhook already sent for media buy %s – skipping", media_buy.media_buy_id)
                return True
            return False
        if force:
            return False
        one_day_ago = datetime.now(UTC) - timedelta(hours=24)
        existing_log = delivery_repo.get_recent_successful_log(
            media_buy.media_buy_id, task_type="media_buy_delivery", since=one_day_ago
        )
        if existing_log:
            logger.info(
                "Skipping daily delivery webhook for media buy %s – already sent (log id %s)",
                media_buy.media_buy_id,
                existing_log.id,
            )
            return True
        return False

    def _claim_final_webhook(self, session: Session, media_buy: MediaBuy) -> datetime | None:
        """Atomically claim the buy's ONE final webhook. Returns the claim token
        (the exact ``claimed_at`` written) if THIS worker won, else None.

        Best-effort concurrency guard (#1575): a conditional UPDATE that wins only
        when the claim is unset or stale (older than FINAL_WEBHOOK_CLAIM_LEASE, so a
        crashed worker's claim self-heals). Runs on the caller's ``session`` and
        COMMITS it so the claim is immediately visible to a racing worker (whose
        UPDATE then matches 0 rows and loses). The returned token is passed to
        _release_final_webhook_claim on a definitive failure/no-send so the claim doesn't
        block an immediate retry for the whole lease. Does NOT close the
        crash-after-POST window — #1606.
        """
        now = datetime.now(UTC)
        won = MediaBuyRepository(session, media_buy.tenant_id).try_claim_final_webhook(
            media_buy.media_buy_id, now=now, stale_before=now - FINAL_WEBHOOK_CLAIM_LEASE
        )
        session.commit()
        return now if won else None

    def _release_final_webhook_claim(self, session: Session, media_buy: MediaBuy, claimed_at: datetime) -> None:
        """Best-effort release of THIS worker's final claim after a definitive
        failure/no-send, so an immediate retry isn't blocked for the whole lease.

        Token-guarded by ``claimed_at`` (see release_final_webhook_claim) so it can
        never clear a newer owner's claim. Swallows its own errors — the lease is the
        real guarantee, so a failed release just falls back to lease recovery.
        """
        try:
            MediaBuyRepository(session, media_buy.tenant_id).release_final_webhook_claim(
                media_buy.media_buy_id, claimed_at=claimed_at
            )
            session.commit()
        except Exception:  # best-effort; lease recovery is the guarantee
            logger.debug(
                "Failed to release final claim for media buy %s (lease will recover)",
                media_buy.media_buy_id,
                exc_info=True,
            )

    async def _send_report_for_media_buy(
        self, media_buy: MediaBuy, reporting_webhook: dict[str, Any], session: Session, force: bool = False
    ) -> bool:
        """Send a delivery report for a single media buy.

        Args:
            media_buy: MediaBuy database model
            reporting_webhook: Webhook configuration dict
            session: Database session
            force: If True, bypass frequency + the 24h "scheduled" dedup. Does
                NOT bypass the final gate, so a manual re-trigger won't emit a
                duplicate final on the read-check path (best-effort; a crash /
                concurrency window remains — see #1606).

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

            is_final = resolve_canonical_status(media_buy, datetime.now(UTC).date()) == CANONICAL_COMPLETED

            # Best-effort read-only de-dup (no claim here — the atomic concurrency
            # claim is taken inside _deliver_report, just before the POST).
            if self._should_skip_send(delivery_repo, media_buy, is_final=is_final, force=force):
                return False

            return await self._deliver_report(session, delivery_repo, media_buy, reporting_webhook, is_final=is_final)

        except Exception as e:
            # Re-raise for the caller (batch loop / manual trigger) to own the
            # single ERROR line. Log at DEBUG here to avoid a duplicate full
            # traceback on the common send_notification -> False path.
            logger.debug("Error sending delivery report for media buy %s: %s", media_buy.media_buy_id, e, exc_info=True)
            raise

    async def _deliver_report(
        self,
        session: Session,
        delivery_repo: DeliveryRepository,
        media_buy: MediaBuy,
        reporting_webhook: dict[str, Any],
        *,
        is_final: bool,
    ) -> bool:
        """Build the delivery report and POST it.

        Returns True when a webhook was delivered, False on a definitive no-send
        (no delivery data, no URL, or the final claim was lost to a concurrent
        worker); RAISES on a failed send so the batch counts an error.

        The atomic final CLAIM is taken here, immediately before the POST — so the
        no-send checks above it never hold a claim — and is RELEASED (token-guarded)
        if the send fails or the claim is lost, so an immediate retry isn't blocked
        for the whole lease. A successful POST keeps the claim; the crash-after-POST
        duplicate window is the best-effort residual tracked in #1606.
        """
        # Reporting period for daily frequency: yesterday (full day).
        start_date_obj = datetime.now(UTC).date() - timedelta(days=1)
        end_date_obj = datetime.now(UTC)

        # Create a ResolvedIdentity for the delivery call. Imported lazily ON
        # PURPOSE: tests inject a testing_context by patching
        # src.core.resolved_identity.ResolvedIdentity, which only intercepts a
        # call-time import — hoisting this to module scope breaks that seam
        # (test_scheduler_uses_simulated_path_in_testing_mode).
        from src.core.resolved_identity import ResolvedIdentity

        identity = ResolvedIdentity(
            principal_id=media_buy.principal_id,
            tenant_id=media_buy.tenant_id,
            tenant={"tenant_id": media_buy.tenant_id},
            protocol="rest",
        )

        # The impl reports on exactly REPORTABLE_CANONICAL_STATUSES: the
        # scheduler already filters by persisted DB status
        # (the serving set plus recent persisted "completed") at query time
        # and skips buys that resolve outside the reportable set, so both
        # still-serving and ended (persisted "completed") campaigns are
        # included rather than filtered out and reported as "not found" errors.
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=[media_buy.media_buy_id],
            status_filter=[MediaBuyStatus(s) for s in sorted(REPORTABLE_CANONICAL_STATUSES)],
            start_date=start_date_obj.strftime("%Y-%m-%d"),
            end_date=end_date_obj.strftime("%Y-%m-%d"),
            context=None,
        )

        delivery_response = _get_media_buy_delivery_impl(req, identity)

        if not isinstance(delivery_response, GetMediaBuyDeliveryResponse):
            # %r, not %s: this branch proved the object is NOT the response model, so its
            # type is unknown (dict/None/other) — repr is unambiguous where a __str__
            # summary would not be. (Never .model_dump() here: that AttributeErrors on a
            # non-model and would raise from inside the error path.)
            logger.warning(
                "`Couldn't get media_delivery` for %s. Result is %r", media_buy.media_buy_id, delivery_response
            )
            return False

        if delivery_response.errors is not None:
            # Log the ERRORS, not the response: GetMediaBuyDeliveryResponse.__str__ is a
            # human-readable envelope summary ("No delivery data found for the specified
            # period."), so "%s" of the model renders a success-shaped sentence with the
            # error payload absent — the one diagnostic this branch exists to emit.
            logger.warning(
                "`Couldn't get media_delivery` for %s. We received an error in the result. errors=%s",
                media_buy.media_buy_id,
                delivery_response.errors,
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
        derived = derive_notification_type(enum_value(d.status) for d in delivery_response.media_buy_deliveries or [])
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
        # notification_type is None too, so the pair stays consistent. Unreachable from
        # this scheduler today (a single-ID request yields >=1 row, or an advisory that
        # aborts earlier); if it ever becomes reachable the body would omit
        # notification_type, which the webhook-result schema marks REQUIRED -- add an
        # explicit empty-deliveries no-send guard rather than emitting that body.

        delivery_response.sequence_number = sequence_number
        delivery_response.partial_data = False  # TODO: Check for reporting_delayed status
        # unavailable_count is "only present in webhook deliveries when partial_data
        # is true" (schema description) — leave None (excluded from the wire) until
        # partial_data reporting is implemented; setting 0 alongside partial_data
        # False put a spec-divergent field on every webhook body.
        delivery_response.unavailable_count = None

        # Extract webhook URL and authentication
        webhook_url = reporting_webhook.get("url")
        if not webhook_url:
            logger.warning("No webhook URL configured for media buy %s", media_buy.media_buy_id)
            return False

        # Try to find existing push notification config or create a temporary one
        auth_config = reporting_webhook.get("authentication", {})
        auth_type = None
        auth_token = None

        if auth_config:
            schemes = auth_config.get("schemes", [])
            auth_type = schemes[0] if schemes else None
            auth_token = auth_config.get("credentials")

        # Reuse the principal's registered push config for this URL, if any
        # (tenant-scoped repository lookup — no raw ORM select in the scheduler).
        push_notification_config = PushNotificationConfigRepository(
            session, media_buy.tenant_id
        ).get_active_by_principal_and_url(media_buy.principal_id, webhook_url)

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
        # Serialize via webhook_payload(): the schema scopes aggregated_totals to
        # "API responses (get_media_buy_delivery), not webhook notifications", and
        # this is the exclusion seam (it also drops None fields, preserving the
        # final-omits-next_expected_at contract).
        media_buy_delivery_payload = create_mcp_webhook_payload(
            task_id=media_buy.media_buy_id,
            task_type="update_media_buy",
            result=delivery_response.webhook_payload(),
            status=AdcpTaskStatus.completed,
        )

        # Atomic concurrency claim, taken NOW — immediately before the POST — so the
        # definitive no-send paths above never hold a claim. The loser skips; the
        # winner's claim is released below on a failed send (token-guarded) so an
        # immediate retry isn't blocked for the lease. (#1575; crash-after-POST
        # residual -> #1606.)
        claim_token = self._claim_final_webhook(session, media_buy) if is_final else None
        if is_final and claim_token is None:
            logger.info(
                "Final delivery webhook for media buy %s is claimed by another worker – skipping",
                media_buy.media_buy_id,
            )
            return False

        # Send webhook notification. ``session`` stays open here — it's reused below
        # on a failed send to release the claim — only the claim's transaction was
        # committed above (for cross-connection visibility, see _claim_final_webhook).
        try:
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
        except Exception:
            # Definitive failure: release our final claim (token-guarded) so an
            # immediate retry isn't blocked for the lease. Lease recovery still
            # covers an actual crash (where this release never runs).
            if claim_token is not None:
                self._release_final_webhook_claim(session, media_buy, claim_token)
            raise

        logger.info("Sent delivery report webhook for media buy %s", media_buy.media_buy_id)
        return True


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

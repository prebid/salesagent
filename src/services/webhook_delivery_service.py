"""Enhanced webhook delivery service for AdCP with security and reliability features.

This service implements the AdCP webhook specification from PR #86:
- HMAC-SHA256 signature generation with X-ADCP-Signature header
- Circuit breaker pattern (CLOSED/OPEN/HALF_OPEN states) for fault tolerance
- Exponential backoff with jitter for retry logic
- Replay attack prevention with 5-minute timestamp window
- Bounded queues (1000 webhooks per endpoint)
- Support for is_adjusted flag for late-arriving data
- Per-endpoint isolation to prevent cascading failures
"""

import atexit
import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from adcp import get_adcp_spec_version

from src.core.security.egress.attempts import env_float
from src.core.security.webhook_egress import deliver_webhook
from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.delivery import WebhookDeliveryOutcome, WebhookTaskContext
from src.services.webhook_conclusion import record_conclusion

logger = logging.getLogger(__name__)

# The ``task_type`` this sender stamps on its rows. It differs from the protocol
# sender's ("media_buy_delivery") because the two senders are answering about
# different things; ``WebhookTaskContext.records_delivery_log`` admits both. What
# must NOT differ is what a row SAYS became of a delivery — that vocabulary lives
# once, in DeliveryRepository.record_outcome.
DELIVERY_REPORT_TASK_TYPE = "delivery_report"


# How long a single delivery attempt may take. Read at CALL time, not import, so a
# test can shorten it without patching a transport — which is what lets the timeout
# path be graded against an origin that really stalls, rather than against a mocked
# clock. Production's value is unchanged.
_DELIVERY_TIMEOUT_ENV = "ADCP_WEBHOOK_DELIVERY_TIMEOUT_SECONDS"
_DEFAULT_DELIVERY_TIMEOUT_SECONDS = 10.0

# The breaker's three policy parameters, read at CALL time for the same reason the
# delivery timeout above is: an import-time read would freeze the first value.
#
# These are POLICY, not a test hatch. 5 / 2 / 60s is a default a deployment may
# legitimately disagree with — a seller with flaky buyers may want to trip later,
# and one with strict SLOs may want to recover sooner. Until now they were
# constructor defaults with no reader at all, so no deployment could express that.
#
# This is also what lets the e2e stack reach HALF_OPEN without spending 60 real
# seconds per scenario: docker-compose.e2e.yml supplies a shorter recovery timeout.
# The code path is identical in both environments — only the VALUE differs, which
# is the line between configuration and a branch that behaves differently under
# test. See prebid/salesagent#2094 for the general case.
_BREAKER_FAILURE_THRESHOLD_ENV = "ADCP_WEBHOOK_BREAKER_FAILURE_THRESHOLD"
_BREAKER_SUCCESS_THRESHOLD_ENV = "ADCP_WEBHOOK_BREAKER_SUCCESS_THRESHOLD"
_BREAKER_TIMEOUT_ENV = "ADCP_WEBHOOK_BREAKER_TIMEOUT_SECONDS"
_DEFAULT_BREAKER_FAILURE_THRESHOLD = 5
_DEFAULT_BREAKER_SUCCESS_THRESHOLD = 2
_DEFAULT_BREAKER_TIMEOUT_SECONDS = 60


def _configured_breaker() -> "CircuitBreaker":
    """Build a breaker from the configured policy, falling back to the shipped defaults."""
    return CircuitBreaker(
        failure_threshold=int(env_float(_BREAKER_FAILURE_THRESHOLD_ENV, _DEFAULT_BREAKER_FAILURE_THRESHOLD)),
        success_threshold=int(env_float(_BREAKER_SUCCESS_THRESHOLD_ENV, _DEFAULT_BREAKER_SUCCESS_THRESHOLD)),
        timeout_seconds=int(env_float(_BREAKER_TIMEOUT_ENV, _DEFAULT_BREAKER_TIMEOUT_SECONDS)),
    )


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Per-endpoint circuit breaker for fault isolation."""

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: int = 60,
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Consecutive failures before opening circuit
            success_threshold: Consecutive successes in HALF_OPEN to close circuit
            timeout_seconds: Time to wait before moving to HALF_OPEN
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: datetime | None = None
        self._lock = threading.Lock()

    def can_attempt(self) -> bool:
        """Check if request can be attempted.

        Returns:
            True if request should be attempted, False if circuit is OPEN
        """
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                # Check if timeout has elapsed
                if (
                    self.last_failure_time
                    and (datetime.now(UTC) - self.last_failure_time).total_seconds() >= self.timeout_seconds
                ):
                    # Move to HALF_OPEN to test recovery
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    logger.info("Circuit breaker moved to HALF_OPEN (testing recovery)")
                    return True
                return False

            # HALF_OPEN state
            return True

    def record_success(self):
        """Record successful request."""
        with self._lock:
            self.failure_count = 0

            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self.state = CircuitState.CLOSED
                    logger.info(f"Circuit breaker CLOSED after {self.success_count} successes")
            elif self.state == CircuitState.OPEN:
                # Shouldn't happen but handle gracefully
                self.state = CircuitState.CLOSED
                logger.info("Circuit breaker CLOSED (recovery)")

    def record_failure(self):
        """Record failed request."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now(UTC)

            if self.state == CircuitState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")
            elif self.state == CircuitState.HALF_OPEN:
                # Failed during recovery test - go back to OPEN
                self.state = CircuitState.OPEN
                self.failure_count = 0
                logger.warning("Circuit breaker reopened (recovery test failed)")


class WebhookQueue:
    """Bounded queue for webhook delivery per endpoint."""

    def __init__(self, max_size: int = 1000):
        """Initialize webhook queue.

        Args:
            max_size: Maximum number of webhooks in queue
        """
        self.max_size = max_size
        self.queue: deque = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._dropped_count = 0

    def enqueue(self, webhook_data: dict[str, Any]) -> bool:
        """Add webhook to queue.

        Args:
            webhook_data: Webhook payload and metadata

        Returns:
            True if enqueued, False if queue is full
        """
        with self._lock:
            if len(self.queue) >= self.max_size:
                self._dropped_count += 1
                logger.warning(
                    f"Webhook queue full ({self.max_size}), dropping webhook (total dropped: {self._dropped_count})"
                )
                return False

            self.queue.append(webhook_data)
            return True

    def dequeue(self) -> dict[str, Any] | None:
        """Remove and return oldest webhook from queue.

        Returns:
            Webhook data or None if queue is empty
        """
        with self._lock:
            if self.queue:
                return self.queue.popleft()
            return None


class WebhookDeliveryService:
    """Webhook delivery service with enhanced security and reliability features.

    Implements AdCP webhook specification from PR #86 with HMAC-SHA256 signatures,
    circuit breakers, exponential backoff, and replay attack prevention.
    """

    def __init__(self) -> None:
        """Initialize enhanced webhook delivery service."""
        self._sequence_numbers: dict[str, int] = {}  # Track sequence per media buy
        self._lock = threading.Lock()  # Protect shared state
        self._circuit_breakers: dict[str, CircuitBreaker] = {}  # Per-endpoint circuit breakers
        self._queues: dict[str, WebhookQueue] = {}  # Per-endpoint bounded queues

        # Register graceful shutdown
        atexit.register(self._shutdown)

        logger.info("✅ WebhookDeliveryService initialized")

    def send_delivery_webhook(
        self,
        media_buy_id: str,
        tenant_id: str,
        principal_id: str,
        reporting_period_start: datetime,
        reporting_period_end: datetime,
        impressions: int,
        spend: float,
        currency: str = "USD",
        status: str = "active",
        clicks: int | None = None,
        ctr: float | None = None,
        by_package: list[dict[str, Any]] | None = None,
        is_final: bool = False,
        is_adjusted: bool = False,
        next_expected_interval_seconds: float | None = None,
    ) -> bool:
        """Send AdCP V2.3 compliant delivery webhook with enhanced security.

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            reporting_period_start: Start of reporting period
            reporting_period_end: End of reporting period
            impressions: Impressions delivered
            spend: Spend amount
            currency: Currency code (default: USD)
            status: Media buy status
            clicks: Optional click count
            ctr: Optional CTR
            by_package: Optional package-level breakdown
            is_final: Whether this is the final webhook
            is_adjusted: Whether this replaces previous data (late arrivals)
            next_expected_interval_seconds: Seconds until next webhook

        Returns:
            True if webhook sent successfully, False otherwise
        """
        try:
            # Thread-safe sequence number increment
            with self._lock:
                self._sequence_numbers[media_buy_id] = self._sequence_numbers.get(media_buy_id, 0) + 1
                sequence_number = self._sequence_numbers[media_buy_id]

            # Determine notification type per new spec
            if is_final:
                notification_type = "final"
            elif is_adjusted:
                notification_type = "adjusted"  # New in spec
            else:
                notification_type = "scheduled"

            # Calculate next_expected_at if not final
            next_expected_at = None
            if not is_final and next_expected_interval_seconds:
                next_expected_at = (datetime.now(UTC) + timedelta(seconds=next_expected_interval_seconds)).isoformat()

            # Build AdCP compliant payload with new fields
            delivery_payload = {
                "adcp_version": get_adcp_spec_version(),
                "notification_type": notification_type,
                "is_adjusted": is_adjusted,  # New field for late data
                "sequence_number": sequence_number,
                "reporting_period": {
                    "start": reporting_period_start.isoformat(),
                    "end": reporting_period_end.isoformat(),
                },
                "currency": currency,
                "media_buy_deliveries": [
                    {
                        "media_buy_id": media_buy_id,
                        "status": status,
                        "totals": {
                            "impressions": impressions,
                            "spend": round(spend, 2),
                        },
                        "by_package": by_package or [],
                    }
                ],
            }

            # Add optional fields
            if next_expected_at:
                delivery_payload["next_expected_at"] = next_expected_at

            # Add optional metrics to totals dict
            # We know structure is valid as we just created it above
            media_buy_delivery = delivery_payload["media_buy_deliveries"][0]  # type: ignore[index]
            totals: dict[str, Any] = media_buy_delivery["totals"]
            if clicks is not None:
                totals["clicks"] = clicks
            if ctr is not None:
                totals["ctr"] = ctr

            logger.info(
                f"📤 Delivery webhook #{sequence_number} for {media_buy_id}: "
                f"{impressions:,} imps, ${spend:,.2f} "
                f"[{notification_type}{'|adjusted' if is_adjusted else ''}]"
            )

            # Send webhook with enhanced security and reliability
            success = self._send_webhook_enhanced(
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                delivery_payload=delivery_payload,
            )

            return success

        except Exception as e:
            logger.error(
                f"❌ Failed to send delivery webhook for {media_buy_id}: {e}",
                exc_info=True,
            )
            return False

    def _deliver_to_config(
        self,
        db: Any,
        config: Any,
        *,
        tenant_id: str,
        principal_id: str,
        media_buy_id: str,
        delivery_payload: dict[str, Any],
    ) -> bool:
        """Deliver one webhook to one configured endpoint and record the outcome.

        This is :meth:`_send_webhook_enhanced`'s per-config loop body, extracted
        unchanged so that method stays under the ADR-009 complexity ratchet
        (#1610). Each ``continue`` in the original loop becomes ``return False``:
        the caller counts ENDPOINTS THAT TOOK THE WEBHOOK, and every early exit
        here was already a not-delivered endpoint.

        Returns:
            True when the endpoint took the webhook; False for every other
            outcome -- auth-blocked, breaker open, queue full, nothing attempted,
            or a recorded delivery failure.
        """
        safe_url = webhook_url_for_log(config.url)
        # Skip auth-blocked endpoints (UC-004-EXT-G-07)
        if isinstance(getattr(config, "auth_blocked_at", None), datetime):
            logger.warning(
                "⚠️ Auth blocked for %s, skipping until credentials reconfigured",
                safe_url,
            )
            return False

        endpoint_key = f"{tenant_id}:{config.url}"

        # Get or create circuit breaker for this endpoint
        if endpoint_key not in self._circuit_breakers:
            self._circuit_breakers[endpoint_key] = _configured_breaker()

        # Get or create queue for this endpoint
        if endpoint_key not in self._queues:
            self._queues[endpoint_key] = WebhookQueue(max_size=1000)

        circuit_breaker = self._circuit_breakers[endpoint_key]
        queue = self._queues[endpoint_key]

        # Check circuit breaker
        if not circuit_breaker.can_attempt():
            logger.warning(
                "⚠️ Circuit breaker OPEN for %s, skipping webhook delivery",
                safe_url,
            )
            return False

        # No send-time address gate here: #1697 put one in front of the
        # raw POST this path used to do, and the egress seam that POST
        # became now owns exactly that policy — it resolves, validates
        # and PINS the connection to the validated IP inside the same
        # call, so there is no window between the verdict and the
        # socket. Re-checking here would only re-resolve, which is the
        # rebinding gap the pin closes, and #1697's refusal-records-a-
        # failure bookkeeping survives in _deliver_with_backoff: a
        # refused URL raises OutboundRequestBlocked, which reaches
        # record_failure() through the OutboundError handler.
        # (Registration-time validation, which must NOT resolve DNS,
        # still lives in src/core/webhook_validator.py.)

        # Add to queue (bounded)
        webhook_data = {
            "config": config,
            "payload": delivery_payload,
            "timestamp": datetime.now(UTC),
        }

        if not queue.enqueue(webhook_data):
            logger.warning("⚠️ Queue full for %s, webhook dropped", safe_url)
            return False

        attempt_started = time.time()

        # ONE conclusion per config. The outcome arrives from the
        # delivery function; what to DO about it — write it down, feed
        # the breaker, count it — is decided here, once, for every kind.
        # Splitting that across the delivery function's arms is how this
        # sender ended up feeding a breaker but recording nothing.
        outcome = self._deliver_with_backoff(endpoint_key, queue)
        if outcome is None:
            # The queue was empty: nothing was attempted, so there is no
            # outcome to record and no signal to give the breaker.
            return False

        ctx = WebhookTaskContext(
            task_id=media_buy_id,
            task_type=DELIVERY_REPORT_TASK_TYPE,
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            sequence_number=delivery_payload.get("sequence_number", 1),
            notification_type=delivery_payload.get("notification_type"),
        )
        # Persistence is observability; it does not get a vote on
        # delivery. Without this swallow a DB error would be caught by
        # this method's outer bare ``except`` and turn a webhook that WAS
        # delivered into ``False`` — and upstream into a spurious retry.
        # Committed PER CONCLUSION, not once at the end: a later config's
        # failure must not roll back an earlier config's recorded row.
        # (expire_on_commit is True, so `config` re-loads after this — safe,
        # the session is still open, and safe_url was computed above.)
        try:
            record_conclusion(
                db,
                tenant_id=tenant_id,
                ctx=ctx,
                log_id=str(uuid4()),
                webhook_url=config.url,
                outcome=outcome,
                response_time_ms=int((time.time() - attempt_started) * 1000),
            )
        except Exception as e:
            logger.error("Failed to write webhook delivery log: %s", e)
            db.rollback()

        # EVERY non-delivered kind records a breaker failure, exactly as
        # the base ``except OutboundError`` did: a destination we cannot
        # deliver to must not look healthy to the breaker.
        if outcome.kind == "delivered":
            circuit_breaker.record_success()
            return True
        circuit_breaker.record_failure()
        return False

    def _send_webhook_enhanced(
        self,
        tenant_id: str,
        principal_id: str,
        media_buy_id: str,
        delivery_payload: dict[str, Any],
    ) -> bool:
        """Send webhook with enhanced security and reliability features.

        Args:
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            media_buy_id: Media buy identifier
            delivery_payload: AdCP delivery payload

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Get webhook configurations

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.push_notification_config import (
                PushNotificationConfigRepository,
            )

            with get_db_session() as db:
                configs = PushNotificationConfigRepository(db, tenant_id).list_active_by_principal(principal_id)

                if not configs:
                    logger.debug(f"⚠️ No webhooks configured for {tenant_id}/{principal_id}")
                    return False

                # Send to all configured webhooks
                sent_count = 0
                for config in configs:
                    if self._deliver_to_config(
                        db,
                        config,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        media_buy_id=media_buy_id,
                        delivery_payload=delivery_payload,
                    ):
                        sent_count += 1

                if sent_count > 0:
                    logger.debug(f"✅ Delivery webhook sent to {sent_count} endpoint(s)")
                    return True
                else:
                    logger.warning("⚠️ Failed to deliver webhook to any endpoint")
                    return False

        except Exception as e:
            logger.error(f"❌ Error in webhook delivery: {e}", exc_info=True)
            return False

    def _deliver_with_backoff(
        self,
        endpoint_key: str,
        queue: WebhookQueue,
    ) -> WebhookDeliveryOutcome | None:
        """Deliver one queued webhook and say WHAT BECAME OF IT.

        Returns the outcome rather than a bool, and feeds neither the circuit
        breaker nor the delivery log itself: this function knows what happened,
        and :meth:`_send_webhook_enhanced` decides what to do about it, once, for
        every kind. Concluding in ``bool`` here is what destroyed the fact that a
        refusal is not a failure — and left this sender, alone among the two,
        writing no delivery-log row at all.

        Args:
            endpoint_key: Unique endpoint identifier
            queue: Webhook queue for this endpoint

        Returns:
            The outcome, or ``None`` when the queue was empty and nothing was
            attempted — which is not an outcome and must not be recorded as one.
        """
        webhook_data = queue.dequeue()
        if not webhook_data:
            return None

        config = webhook_data["config"]
        payload = webhook_data["payload"]
        safe_url = webhook_url_for_log(config.url)

        # Signing (X-ADCP-Signature / X-ADCP-Timestamp) is owned entirely by
        # deliver_webhook below -- it serializes, signs and stamps the timestamp
        # as one decision, so this function never holds a signature and a body
        # serialization as two independent things to keep in sync.
        #
        # The auth DECISION above that transport is owned entirely by
        # deliver_webhook/adeliver_webhook (#1802, GH #1894). This
        # sender used to make
        # it inline and made it wrong four ways at once: it read webhook_secret (a
        # column with zero writers in src/, so the signing branch was unreachable
        # for any row a buyer can create), signed on a truthy secret rather than on
        # the scheme, silently downgraded a weak secret to an UNSIGNED delivery, and
        # compared "bearer" against an enum every writer stores as "Bearer". One
        # seam call replaces all four, and the pinned Authentication type is what
        # makes "what if it is none of these" un-writable.
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/2.3 (Enhanced Webhooks)",
        }

        # ONE call. The seam validates the stored pair against the pinned type,
        # applies whatever that scheme requires, dials, retries on the BR-RULE-029
        # schedule, and reports what became of it. This sender no longer decides
        # anything about authentication — which is the point: three senders each
        # deciding is how one buyer's HMAC row signed on one path, went out
        # unsigned on another, and matched nothing on a third.
        #
        # The credential-length rule that used to be argued about here now lives at
        # the seam, applied to every sender at once. Pinned AdCP 3.1.1
        # (core/push-notification-config.json) requires authentication.credentials
        # with minLength 32, and owner ruling #2 for Epic D says a present-but
        # -non-conforming block REFUSES rather than silently downgrading to a plain
        # send. A stored row that cannot be delivered conformantly is not a delivery
        # we should be making; its owner re-registers. Do NOT re-add a local
        # tolerance here — that reinstates the divergence this seam removed.
        #
        # No ``field=``: this URL is read back out of storage, not off a request
        # document. Every log names ``safe_url`` (scheme://host/path), never
        # ``config.url``, which may carry userinfo credentials or a query token.
        try:
            outcome = deliver_webhook(
                config.url,
                payload,
                scheme=config.authentication_type,
                credentials=config.authentication_token,
                headers=headers,
                timeout=env_float(_DELIVERY_TIMEOUT_ENV, _DEFAULT_DELIVERY_TIMEOUT_SECONDS),
                max_attempts=3,
            )
        except Exception as e:
            # DELIBERATELY KEPT. The seam maps its OWN failure taxonomy onto the
            # outcome, so nothing it raises lands here — but no outcome kind covers
            # a NON-transport failure, and this function is contracted ``-> bool``.
            # The pinned transport's own wrong-host guard raises a bare RuntimeError,
            # which belongs here rather than escaping into the poller thread.
            logger.error("Unexpected error delivering to %s: %s", safe_url, e, exc_info=True)
            # No outcome kind covers a NON-transport failure, so this arm builds
            # the one it means. It no longer feeds the breaker itself — every kind
            # reaches the caller's single conclusion, so no arm can be the one that
            # forgets.
            return WebhookDeliveryOutcome.unexpected(type(e).__name__)

        if outcome.kind != "delivered":
            if outcome.kind == "refused_auth":
                # FAIL-CLOSED, and deliberately log-and-return rather than raise. The
                # buyer asked for an authentication this sender cannot produce; an
                # unsigned POST to an endpoint that will reject it is strictly worse
                # than no POST, because it is an unauthenticated request to a third
                # party that no receiver can attribute to us. By the time control
                # reaches here the poller is on its own thread with no request in
                # flight, so there is no caller left to receive a raise.
                logger.error("Refusing to deliver webhook to %s: %s", safe_url, outcome.detail or outcome.reason)
            elif outcome.kind == "client_error":
                # The seam logs nothing on a non-retryable 4xx and its records do not
                # propagate here, so this is the only operator-visible trace.
                logger.warning(
                    "Webhook delivery to %s returned client error %s, will not retry",
                    safe_url,
                    outcome.http_status,
                )
            elif outcome.kind == "refused_destination":
                # Severity carried on the outcome, not chosen here (#1802).
                logger.log(outcome.log_level, "Webhook delivery to %s was refused by egress policy", safe_url)
            else:
                cause = f"status {outcome.http_status}" if outcome.http_status is not None else "no response"
                logger.warning(
                    "Webhook delivery to %s failed after %s attempts (%s)", safe_url, outcome.attempts, cause
                )
            return outcome

        logger.debug(
            "Webhook delivered to %s (status: %s)",
            safe_url,
            outcome.http_status,
        )
        return outcome

    def reset_sequence(self, media_buy_id: str):
        """Reset sequence number for a media buy.

        Args:
            media_buy_id: Media buy identifier
        """
        with self._lock:
            if media_buy_id in self._sequence_numbers:
                del self._sequence_numbers[media_buy_id]

    def has_open_circuit_breaker(self, tenant_id: str) -> bool:
        """Check if any circuit breaker is OPEN for endpoints belonging to a tenant."""
        for key, cb in self._circuit_breakers.items():
            if key.startswith(f"{tenant_id}:") and cb.state == CircuitState.OPEN:
                return True
        return False

    def get_circuit_breaker_state(self, endpoint_url: str) -> tuple[CircuitState, int]:
        """Get circuit breaker state for an endpoint.

        Args:
            endpoint_url: Webhook endpoint URL

        Returns:
            Tuple of (state, failure_count)
        """
        for key in self._circuit_breakers.keys():
            if endpoint_url in key:
                circuit_breaker = self._circuit_breakers[key]
                return (circuit_breaker.state, circuit_breaker.failure_count)
        return (CircuitState.CLOSED, 0)

    def _shutdown(self):
        """Graceful shutdown handler."""
        try:
            with self._lock:
                # Clean up internal state without logging
                # (logging stream may be closed during interpreter shutdown)
                pass
        except (ValueError, OSError):
            # Logging stream may be closed during interpreter shutdown
            pass


# Global singleton instance
webhook_delivery_service = WebhookDeliveryService()

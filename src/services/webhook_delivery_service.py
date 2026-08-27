"""Enhanced webhook delivery service for AdCP with security and reliability features.

Authentication is NOT this module's concern: the receiver's own
``PushNotificationConfig`` registration selects exactly one mode at the single
signing boundary (``src.core.signing.webhook_sender_factory``, #1291 C1), which
serializes, authenticates and POSTs one delivery as a single act.

What this service owns:
- Circuit breaker pattern (CLOSED/OPEN/HALF_OPEN states) for fault tolerance
- Exponential backoff with jitter for retry logic
- Replay attack prevention with 5-minute timestamp window
- Bounded queues (1000 webhooks per endpoint)
- Support for is_adjusted flag for late-arriving data
- Per-endpoint isolation to prevent cascading failures
"""

import atexit
import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import httpx
from adcp import get_adcp_spec_version
from adcp.webhooks import generate_webhook_idempotency_key
from pydantic import JsonValue

from src.core.signing import deliver_adcp_webhook_sync
from src.core.webhook_validator import (
    reject_unsafe_outbound_webhook_url,
    webhook_url_for_log,
)

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class QueuedWebhook:
    """One queued delivery — PRIMITIVES ONLY, and that is the invariant.

    Every field is a plain value. No ORM model, no ``Mapped[...]``, no session, no
    repository. That is what makes the retry loop STRUCTURALLY unable to hold a database
    connection: a loop whose only input is primitives cannot lazy-load a relationship,
    cannot touch a session, and cannot keep one alive across ``time.sleep`` — no matter
    how many call frames deep the sleep sits (#1757, salesagent-n78j0.4).

    THIS REPLACED A ``dict[str, Any]`` CARRYING TWO NON-PRIMITIVES:

    * ``signing_repo`` — a ``SigningKeyRepository`` built on the caller's open session.
      That was the connection riding on the queue, and it is why the delivery loop ran
      inside a ``with get_db_session()`` block at all.
    * ``config`` — a live ``PushNotificationConfig`` ORM row, whose lazy-loads need a
      session that may well be closed by the time a retry touches it.

    ``url`` / ``authentication_type`` / ``authentication_token`` are exactly the three
    attributes the sender reads off a config (``webhook_sender_factory`` :167-168, :183,
    :321-322, :445, :452), so this projection satisfies the same attribute contract the
    ORM row did and is passed as ``config=`` unchanged — the sender's signature does not
    move.

    ``payload`` is ``dict[str, JsonValue]``, NOT ``dict[str, Any]``: ``JsonValue`` is
    pydantic's recursive JSON type, so an ORM object in the payload is a TYPE ERROR at
    every producer rather than a runtime possibility. It is deliberately NOT pre-
    serialized bytes — ``_deliver_with_backoff`` must "serialize, authenticate and POST
    as one act, so the bytes signed are the bytes sent" (#1441), and bytes on the queue
    would be something the sent body could diverge from.

    Pinned by ``tests/unit/test_architecture_repository_pattern.py``; the mutations that
    turn it red are an ORM-typed or ``Mapped[...]`` field, or dropping ``frozen=True``.
    """

    url: str
    authentication_type: str | None
    authentication_token: str | None
    payload: dict[str, JsonValue]
    tenant_id: str
    idempotency_key: str
    timestamp: datetime


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

    def enqueue(self, webhook_data: QueuedWebhook) -> bool:
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

    def dequeue(self) -> QueuedWebhook | None:
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
            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import PushNotificationConfig

            with get_db_session() as db:
                stmt = select(PushNotificationConfig).filter_by(
                    tenant_id=tenant_id, principal_id=principal_id, is_active=True
                )
                configs = db.scalars(stmt).all()

                if not configs:
                    logger.debug(f"⚠️ No webhooks configured for {tenant_id}/{principal_id}")
                    return False

                # PHASE 1 — everything that needs the session: read the receiver rows,
                # apply the gates, project to primitives, enqueue. Nothing here waits on
                # a network.
                pending: list[tuple[str, CircuitBreaker, WebhookQueue]] = []
                for config in configs:
                    safe_url = webhook_url_for_log(config.url)
                    # Skip auth-blocked endpoints (UC-004-EXT-G-07)
                    if isinstance(getattr(config, "auth_blocked_at", None), datetime):
                        logger.warning(
                            "⚠️ Auth blocked for %s, skipping until credentials reconfigured",
                            safe_url,
                        )
                        continue

                    endpoint_key = f"{tenant_id}:{config.url}"

                    # Get or create circuit breaker for this endpoint
                    if endpoint_key not in self._circuit_breakers:
                        self._circuit_breakers[endpoint_key] = CircuitBreaker()

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
                        continue

                    # Send-time SSRF gate before enqueue/POST (registration may skip DNS).
                    if self._reject_unsafe_outbound_url(config.url, circuit_breaker):
                        continue

                    # Add to queue (bounded). ``tenant_id`` rides on the entry because
                    # ``_deliver_with_backoff`` is reached through ``endpoint_key``
                    # alone, and splitting that composite string back apart to recover a
                    # tenant id is not a data path. It is a ``str``, so it costs nothing.
                    # THE SIGNING REPOSITORY USED TO RIDE ALONG FOR THE SAME STATED
                    # REASON, and that was the fault: a repository carries a SESSION, so
                    # the entry carried a pooled connection into a loop that sleeps and
                    # POSTs to a buyer-supplied URL. ``signing_repo`` now opens its own
                    # short session, so there is nothing left to hand over (#1757).
                    # Projected off the ORM row HERE, while the session is open — that
                    # read is legitimate and stays inside the block. What LEAVES the
                    # block is primitives only, so nothing downstream can reach back
                    # into a session (#1757).
                    webhook_data = QueuedWebhook(
                        url=config.url,
                        authentication_type=config.authentication_type,
                        authentication_token=config.authentication_token,
                        payload=delivery_payload,
                        timestamp=datetime.now(UTC),
                        tenant_id=tenant_id,
                        # ONE key per distinct event, reused across its retries.
                        idempotency_key=generate_webhook_idempotency_key(),
                    )

                    if not queue.enqueue(webhook_data):
                        logger.warning("⚠️ Queue full for %s, webhook dropped", safe_url)
                        continue

                    pending.append((endpoint_key, circuit_breaker, queue))

            # PHASE 2 — the session is CLOSED. ``_deliver_with_backoff`` sleeps between
            # retries and POSTs to a buyer-supplied URL, so a connection held here would
            # be parked on a third party's latency: a hanging receiver would consume a
            # pooled connection for the whole backoff. Nothing below needs a session —
            # the queue carries primitives (``QueuedWebhook``) and ``signing_repo``
            # opens its own short one for the key read (#1757, salesagent-n78j0.4).
            sent_count = 0
            for endpoint_key, circuit_breaker, queue in pending:
                if self._deliver_with_backoff(endpoint_key, circuit_breaker, queue):
                    sent_count += 1

            if sent_count > 0:
                logger.debug(f"✅ Delivery webhook sent to {sent_count} endpoint(s)")
                return True
            logger.warning("⚠️ Failed to deliver webhook to any endpoint")
            return False

        except Exception as e:
            logger.error(f"❌ Error in webhook delivery: {e}", exc_info=True)
            return False

    def _reject_unsafe_outbound_url(self, url: str, circuit_breaker: CircuitBreaker) -> bool:
        """Return True when outbound URL fails SSRF (caller must skip delivery)."""
        rejected, _error_msg = reject_unsafe_outbound_webhook_url(url, log=logger, kind="Application")
        if rejected:
            circuit_breaker.record_failure()
            return True
        return False

    def _deliver_with_backoff(
        self,
        endpoint_key: str,
        circuit_breaker: CircuitBreaker,
        queue: WebhookQueue,
    ) -> bool:
        """Deliver webhook with exponential backoff and jitter.

        Args:
            endpoint_key: Unique endpoint identifier
            circuit_breaker: Circuit breaker for this endpoint
            queue: Webhook queue for this endpoint

        Returns:
            True if delivered successfully, False otherwise
        """
        max_retries = 3
        base_delay = 1.0  # Initial delay in seconds

        webhook_data = queue.dequeue()
        if not webhook_data:
            return False

        payload = webhook_data.payload
        safe_url = webhook_url_for_log(webhook_data.url)

        # Authentication is NOT decided here — the receiver's registration selects
        # exactly one mode at the single boundary (#1291 C1). What stays local is the
        # one header this service adds for operator telemetry; ``Content-Type`` and
        # every auth/signature header belong to the sender that serialized the body.
        headers = {"User-Agent": "AdCP-Sales-Agent/2.3 (Enhanced Webhooks)"}

        # Exponential backoff with jitter
        for attempt in range(max_retries):
            try:
                # Calculate delay with exponential backoff and jitter
                if attempt > 0:
                    # Base delay * 2^attempt + random jitter (0-1 seconds)
                    delay = (base_delay * (2**attempt)) + random.uniform(0, 1)
                    logger.debug(f"Retrying webhook delivery after {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)

                # Send webhook — serialize, authenticate and POST as one act, so the
                # bytes signed are the bytes sent (#1441). The destination was already
                # SSRF-gated above (the sender owns authentication, the caller owns
                # destination policy) and the sender's client leaves httpx's
                # ``follow_redirects`` at its False default, so an open redirect
                # cannot walk the POST off the vetted host.
                delivery = deliver_adcp_webhook_sync(
                    url=webhook_data.url,
                    payload=payload,
                    idempotency_key=webhook_data.idempotency_key,
                    config=webhook_data,
                    tenant_id=webhook_data.tenant_id,
                    extra_headers=headers,
                )

                if 200 <= delivery.status_code < 300:
                    logger.debug(
                        "Webhook delivered to %s (status: %s)",
                        safe_url,
                        delivery.status_code,
                    )
                    circuit_breaker.record_success()
                    return True

                # Client errors (4xx): do NOT retry — the request is invalid
                if 400 <= delivery.status_code < 500:
                    logger.warning(
                        "Webhook delivery to %s returned client error %s, will not retry",
                        safe_url,
                        delivery.status_code,
                    )
                    circuit_breaker.record_failure()
                    return False

                logger.warning(
                    "Webhook delivery to %s returned status %s (attempt: %s/%s)",
                    safe_url,
                    delivery.status_code,
                    attempt + 1,
                    max_retries,
                )

            except httpx.TimeoutException:
                logger.warning(
                    "Webhook delivery to %s timed out (attempt: %s/%s)",
                    safe_url,
                    attempt + 1,
                    max_retries,
                )
            except httpx.RequestError as e:
                logger.warning(
                    "Webhook delivery to %s failed: %s (attempt: %s/%s)",
                    safe_url,
                    e,
                    attempt + 1,
                    max_retries,
                )
            except Exception as e:
                logger.error("Unexpected error delivering to %s: %s", safe_url, e, exc_info=True)
                break

        # All retries failed
        circuit_breaker.record_failure()
        return False

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

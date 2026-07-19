"""Legacy reporting-webhook delivery with local security and reliability controls.

This service preserves the repository's legacy HMAC delivery profile. It does
not claim AdCP 3.1.1 RFC 9421 default-signing conformance. It provides:
- HMAC-SHA256 signature generation with X-ADCP-Signature header
- Circuit breaker pattern (CLOSED/OPEN/HALF_OPEN states) for fault tolerance
- Exponential backoff with jitter for retry logic
- Replay attack prevention with 5-minute timestamp window
- Bounded queues (1000 webhooks per endpoint)
- Support for is_adjusted flag for late-arriving data
- Per-endpoint isolation to prevent cascading failures
"""

import atexit
import hashlib
import hmac
import json
import logging
import random
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import requests
from adcp import get_adcp_spec_version

from src.core.bounded_executor import SyncThreadPoolBulkhead
from src.core.database.repositories.push_notification_config import PushNotificationTarget
from src.core.database.repositories.uow import PushNotificationConfigUoW
from src.core.security.webhook_http import (
    WEBHOOK_DELIVERY_DEADLINE_SECONDS,
    WEBHOOK_DELIVERY_MAX_WORKERS,
    UnsafeWebhookTargetError,
    create_pinned_webhook_session,
    post_webhook_status,
)

logger = logging.getLogger(__name__)

_LEGACY_WEBHOOK_DELIVERY_BULKHEAD = SyncThreadPoolBulkhead(
    max_workers=WEBHOOK_DELIVERY_MAX_WORKERS,
    thread_name_prefix="legacy-webhook-delivery",
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

    Preserves the legacy HMAC profile from PR #86 with circuit breakers,
    exponential backoff, replay controls, and SSRF-safe transport hardening.
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
        """Send one legacy-profile delivery-reporting webhook securely.

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

    def _generate_hmac_signature(self, payload: dict[str, Any] | bytes, secret: str, timestamp: str) -> str:
        """Generate HMAC-SHA256 signature for webhook payload.

        Args:
            payload: Webhook payload
            secret: Webhook secret (min 32 characters)
            timestamp: ISO format timestamp

        Returns:
            HMAC signature as hex string
        """
        payload_bytes = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        message = timestamp.encode("utf-8") + b"." + payload_bytes
        signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

        return signature

    def _verify_secret_strength(self, secret: str) -> bool:
        """Verify webhook secret meets minimum strength requirements.

        Args:
            secret: Webhook secret

        Returns:
            True if secret is strong enough
        """
        return len(secret) >= 32

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
            # Snapshot scalar targets inside the UoW, then close the session before
            # any outbound request, retry sleep, or queue operation.
            with PushNotificationConfigUoW(tenant_id) as uow:
                assert uow.push_notification_configs is not None
                targets = uow.push_notification_configs.list_active_delivery_targets(principal_id)

            if not targets:
                logger.debug(f"⚠️ No webhooks configured for {tenant_id}/{principal_id}")
                return False

            sent_count = sum(self._queue_and_deliver_target(tenant_id, target, delivery_payload) for target in targets)
            if sent_count > 0:
                logger.debug(f"✅ Delivery webhook sent to {sent_count} endpoint(s)")
                return True
            logger.warning("⚠️ Failed to deliver webhook to any endpoint")
            return False

        except Exception as e:
            logger.error(f"❌ Error in webhook delivery: {e}", exc_info=True)
            return False

    def _queue_and_deliver_target(
        self,
        tenant_id: str,
        target: PushNotificationTarget,
        delivery_payload: dict[str, Any],
    ) -> bool:
        """Deliver one target within the process-wide legacy worker budget.

        The caller's deadline covers admission and the complete retry operation.
        If it expires, ``SyncThreadPoolBulkhead`` retains the permit until the
        underlying worker really finishes; stuck DNS or socket I/O therefore
        cannot be replaced by an unbounded sequence of simulator threads.
        """
        try:
            return _LEGACY_WEBHOOK_DELIVERY_BULKHEAD.run(
                self._enqueue_and_deliver_target,
                tenant_id,
                target,
                delivery_payload,
                timeout_seconds=WEBHOOK_DELIVERY_DEADLINE_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Webhook delivery to %s exceeded the %.1fs total deadline",
                target.url,
                WEBHOOK_DELIVERY_DEADLINE_SECONDS,
            )
            return False

    def _enqueue_and_deliver_target(
        self,
        tenant_id: str,
        target: PushNotificationTarget,
        delivery_payload: dict[str, Any],
    ) -> bool:
        """Queue and synchronously drain one session-independent target snapshot."""
        if isinstance(target.auth_blocked_at, datetime):
            logger.warning(f"⚠️ Auth blocked for {target.url}, skipping until credentials reconfigured")
            return False

        endpoint_key = f"{tenant_id}:{target.url}"
        circuit_breaker = self._circuit_breakers.setdefault(endpoint_key, CircuitBreaker())
        queue = self._queues.setdefault(endpoint_key, WebhookQueue(max_size=1000))
        if not circuit_breaker.can_attempt():
            logger.warning(f"⚠️ Circuit breaker OPEN for {target.url}, skipping webhook delivery")
            return False

        if not queue.enqueue({"config": target, "payload": delivery_payload, "timestamp": datetime.now(UTC)}):
            logger.warning(f"⚠️ Queue full for {target.url}, webhook dropped")
            return False
        return self._deliver_with_backoff(endpoint_key, circuit_breaker, queue)

    def _build_delivery_headers(
        self,
        config: PushNotificationTarget,
        payload_bytes: bytes,
        timestamp: str,
    ) -> dict[str, str]:
        """Build authentication and integrity headers for one queued target."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/2.3 (Enhanced Webhooks)",
            "X-ADCP-Timestamp": timestamp,
        }
        if config.webhook_secret:
            if self._verify_secret_strength(config.webhook_secret):
                headers["X-ADCP-Signature"] = self._generate_hmac_signature(
                    payload_bytes,
                    config.webhook_secret,
                    timestamp,
                )
            else:
                logger.warning(f"⚠️ Webhook secret for {config.url} is too weak (min 32 characters required)")
        if config.authentication_type == "bearer" and config.authentication_token:
            headers["Authorization"] = f"Bearer {config.authentication_token}"
        return headers

    @staticmethod
    def _wait_before_retry(attempt: int, max_retries: int) -> None:
        """Apply exponential backoff plus jitter before a retry attempt."""
        if attempt == 0:
            return
        delay = (2**attempt) + random.uniform(0, 1)
        logger.debug(f"Retrying webhook delivery after {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(delay)

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
        webhook_data = queue.dequeue()
        if not webhook_data:
            return False

        config = webhook_data["config"]
        payload = webhook_data["payload"]
        timestamp = webhook_data["timestamp"].isoformat()
        try:
            # ``allow_nan=False`` preserves the prior httpx ``json=`` behavior
            # and the JSON wire contract. Python's default would emit the invalid
            # JSON tokens NaN/Infinity and then sign those malformed bytes.
            payload_bytes = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.error("Webhook payload is not valid JSON for %s: %s", config.url, exc)
            circuit_breaker.record_failure()
            return False
        headers = self._build_delivery_headers(config, payload_bytes, timestamp)

        # One session belongs to this worker delivery; every retry re-enters the
        # adapter, so DNS is resolved, validated, and pinned again each time.
        with create_pinned_webhook_session() as session:
            for attempt in range(max_retries):
                try:
                    self._wait_before_retry(attempt, max_retries)

                    status_code = post_webhook_status(
                        session,
                        config.url,
                        body=payload_bytes,
                        headers=headers,
                        timeout=10.0,
                    )
                    if 200 <= status_code < 300:
                        logger.debug(f"Webhook delivered to {config.url} (status: {status_code})")
                        circuit_breaker.record_success()
                        return True

                    # Refused redirects and client errors are permanent for this
                    # payload/configuration. Redirects are never followed.
                    if 300 <= status_code < 500:
                        logger.warning(f"Webhook delivery to {config.url} returned non-retryable status {status_code}")
                        circuit_breaker.record_failure()
                        return False

                    logger.warning(
                        f"Webhook delivery to {config.url} returned status {status_code} "
                        f"(attempt: {attempt + 1}/{max_retries})"
                    )

                except UnsafeWebhookTargetError as e:
                    # DNS rebinding/private targets are permanent security failures,
                    # not transient network errors. Never retry the unsafe URL.
                    logger.warning(f"Webhook delivery to {config.url} refused: {e}")
                    break
                except requests.Timeout:
                    logger.warning(f"Webhook delivery to {config.url} timed out (attempt: {attempt + 1}/{max_retries})")
                except requests.RequestException as e:
                    logger.warning(
                        f"Webhook delivery to {config.url} failed: {e} (attempt: {attempt + 1}/{max_retries})"
                    )
                except Exception as e:
                    logger.error(f"Unexpected error delivering to {config.url}: {e}", exc_info=True)
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

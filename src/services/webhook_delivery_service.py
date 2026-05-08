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
import json
import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import httpx
from adcp import get_adcp_version

from src.core.metrics import webhook_signing_misconfigured_total
from src.services.webhook_signing import (
    SIGNING_MODE_HMAC,
    LoadedSigningCredential,
    SigningConfigurationError,
    build_auth_headers,
    load_active_signing_credential,
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

    def __init__(
        self,
        *,
        signing_credential_loader: Callable[..., LoadedSigningCredential | None] | None = None,
    ) -> None:
        """Initialize enhanced webhook delivery service.

        :param signing_credential_loader: Optional override for the
            function that loads a tenant's active webhook-signing
            credential. Defaults to :func:`load_active_signing_credential`
            (DB + filesystem read). Tests can pass a stub to exercise
            the service without DB or PEM files; production injection
            (e.g. KMS-backed alternative) can swap the loader without
            touching the call site.
        """
        self._sequence_numbers: dict[str, int] = {}  # Track sequence per media buy
        self._lock = threading.Lock()  # Protect shared state
        self._circuit_breakers: dict[str, CircuitBreaker] = {}  # Per-endpoint circuit breakers
        self._queues: dict[str, WebhookQueue] = {}  # Per-endpoint bounded queues
        self._signing_credential_loader = signing_credential_loader or load_active_signing_credential

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
                "adcp_version": get_adcp_version(),
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

                # Pre-serialize the payload ONCE for the whole tenant
                # batch. The same bytes are used for every endpoint's
                # signature base AND wire body; serializing later (per
                # endpoint, per dequeue) opens a window where the dict
                # could be mutated in flight and signed bytes drift from
                # wire bytes.
                body_bytes = json.dumps(delivery_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                enqueue_timestamp = datetime.now(UTC).isoformat()

                # Snapshot per-config primitives off the ORM rows BEFORE
                # the session closes — webhook_data must not retain a
                # reference to a detached SQLAlchemy instance, or any
                # post-session attribute access becomes a footgun.
                # ``signing_mode`` defaults to legacy HMAC for forward
                # compat with rows written before the column existed.
                config_snapshots = []
                for config in configs:
                    if isinstance(getattr(config, "auth_blocked_at", None), datetime):
                        logger.warning(f"⚠️ Auth blocked for {config.url}, skipping until credentials reconfigured")
                        continue
                    config_snapshots.append(
                        {
                            "tenant_id": tenant_id,
                            "url": config.url,
                            "signing_mode": getattr(config, "signing_mode", SIGNING_MODE_HMAC),
                            "webhook_secret": getattr(config, "webhook_secret", None),
                            "authentication_type": config.authentication_type,
                            "authentication_token": config.authentication_token,
                        }
                    )

            if not config_snapshots:
                # Either nothing was configured or every endpoint was
                # auth-blocked. Either way, no work to do.
                return False

            # Load the active signing credential ONCE per send (not per
            # endpoint) — every config for the same tenant signs with the
            # same key. ``load_active_signing_credential`` returns None
            # for HMAC-only mode and raises for misconfigured RFC 9421;
            # we surface the latter as a per-endpoint circuit-breaker
            # failure inside _deliver_with_backoff so individual buyer
            # configs don't poison the whole batch.
            #
            # Mode is per-config but the credential is per-tenant —
            # if ANY config requests rfc9421/both we need to load. Pick
            # the strongest mode in the batch.
            modes = {snap["signing_mode"] for snap in config_snapshots}
            tenant_signing_mode = "rfc9421" if "rfc9421" in modes or "both" in modes else SIGNING_MODE_HMAC
            try:
                active_credential = self._signing_credential_loader(
                    tenant_id=tenant_id, signing_mode=tenant_signing_mode
                )
            except SigningConfigurationError as exc:
                # Tenant-level credential problem — every rfc9421/both
                # config in this batch will fail. Log once, continue so
                # any pure-HMAC configs in the batch still deliver.
                logger.error(
                    "❌ Cannot load active webhook-signing credential for tenant=%s: %s",
                    tenant_id,
                    exc,
                )
                # Distinct counter so operators can alert on config errors
                # without drowning in transient buyer-endpoint failures.
                webhook_signing_misconfigured_total.labels(tenant_id=tenant_id, signing_mode=tenant_signing_mode).inc()
                active_credential = None

            sent_count = 0
            for snapshot in config_snapshots:
                endpoint_key = f"{tenant_id}:{snapshot['url']}"

                if endpoint_key not in self._circuit_breakers:
                    self._circuit_breakers[endpoint_key] = CircuitBreaker()
                if endpoint_key not in self._queues:
                    self._queues[endpoint_key] = WebhookQueue(max_size=1000)

                circuit_breaker = self._circuit_breakers[endpoint_key]
                queue = self._queues[endpoint_key]

                if not circuit_breaker.can_attempt():
                    logger.warning(f"⚠️ Circuit breaker OPEN for {snapshot['url']}, skipping webhook delivery")
                    continue

                webhook_data = {
                    "snapshot": snapshot,
                    "body_bytes": body_bytes,
                    "timestamp": enqueue_timestamp,
                    "active_credential": active_credential,
                }

                if not queue.enqueue(webhook_data):
                    logger.warning(f"⚠️ Queue full for {snapshot['url']}, webhook dropped")
                    continue

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

        # All primitives — no ORM rows, no detached-instance hazards.
        # ``snapshot`` was captured under the same DB session that read
        # the configs in _send_webhook_enhanced; ``active_credential``
        # was loaded atomically there (one PEM read pinned to the kid
        # we read in the same transaction).
        snapshot = webhook_data["snapshot"]
        body_bytes: bytes = webhook_data["body_bytes"]
        timestamp: str = webhook_data["timestamp"]
        active_credential: LoadedSigningCredential | None = webhook_data["active_credential"]

        url = snapshot["url"]
        signing_mode = snapshot["signing_mode"]

        base_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/2.3 (Enhanced Webhooks)",
            "X-ADCP-Timestamp": timestamp,  # legacy replay prevention
        }
        # Bearer auth lives on the request alongside whatever signing the
        # mode dictates — buyers may require both an API token AND a
        # signed request.
        if snapshot["authentication_type"] == "bearer" and snapshot["authentication_token"]:
            base_headers["Authorization"] = f"Bearer {snapshot['authentication_token']}"

        try:
            headers = build_auth_headers(
                signing_mode=signing_mode,
                method="POST",
                url=url,
                body=body_bytes,
                timestamp=timestamp,
                base_headers=base_headers,
                webhook_secret=snapshot["webhook_secret"],
                active_credential=active_credential,
            )
        except SigningConfigurationError as exc:
            # Buyer asked for signed delivery and we can't produce a
            # signature — drop the webhook rather than send unauthenticated.
            # Circuit-break the endpoint so we don't retry until the
            # operator fixes the credential config.
            logger.error(
                "❌ Cannot sign webhook for %s (signing_mode=%s): %s",
                url,
                signing_mode,
                exc,
            )
            # Increment the misconfig counter, but ONLY for per-endpoint
            # config errors (e.g. ``both`` mode without HMAC secret,
            # missing Content-Type). Tenant-level credential load failures
            # are already counted once in _send_webhook_enhanced; re-counting
            # them here would inflate the gauge by N (one per endpoint in
            # the batch). Detect the cascade case via active_credential is
            # None for a mode that requires a credential — that's
            # exclusively the tenant-level-load-failed reaper path.
            requires_credential = signing_mode in ("rfc9421", "both")
            cascading_from_tenant_load = requires_credential and active_credential is None
            if not cascading_from_tenant_load:
                webhook_signing_misconfigured_total.labels(
                    tenant_id=snapshot.get("tenant_id", "unknown"),
                    signing_mode=signing_mode,
                ).inc()
            circuit_breaker.record_failure()
            return False

        # Exponential backoff with jitter
        for attempt in range(max_retries):
            try:
                # Calculate delay with exponential backoff and jitter
                if attempt > 0:
                    # Base delay * 2^attempt + random jitter (0-1 seconds)
                    delay = (base_delay * (2**attempt)) + random.uniform(0, 1)
                    logger.debug(f"Retrying webhook delivery after {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)

                # Send webhook. Use ``content=body_bytes`` (NOT ``json=``)
                # so the wire body is byte-identical to what we signed —
                # httpx's ``json`` re-serializes via its own encoder.
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(
                        url,
                        content=body_bytes,
                        headers=headers,
                    )

                    if 200 <= response.status_code < 300:
                        logger.debug(f"Webhook delivered to {url} (status: {response.status_code})")
                        circuit_breaker.record_success()
                        return True

                    # Client errors (4xx): do NOT retry — the request is invalid
                    if 400 <= response.status_code < 500:
                        logger.warning(
                            f"Webhook delivery to {url} returned client error {response.status_code}, will not retry"
                        )
                        circuit_breaker.record_failure()
                        return False

                    logger.warning(
                        f"Webhook delivery to {url} returned "
                        f"status {response.status_code} "
                        f"(attempt: {attempt + 1}/{max_retries})"
                    )

            except httpx.TimeoutException:
                logger.warning(f"Webhook delivery to {url} timed out (attempt: {attempt + 1}/{max_retries})")
            except httpx.RequestError as e:
                logger.warning(f"Webhook delivery to {url} failed: {e} (attempt: {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.error(f"Unexpected error delivering to {url}: {e}", exc_info=True)
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

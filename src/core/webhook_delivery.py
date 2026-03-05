"""Webhook delivery service with exponential backoff retry logic.

This module provides reliable webhook delivery with:
- Exponential backoff retry strategy (1s, 2s, 4s)
- Database tracking of delivery attempts
- Retry on 5xx errors, no retry on 4xx client errors
- SSRF protection via WebhookURLValidator
- HMAC signing support via WebhookAuthenticator
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from src.core.database.database_session import get_db_session
from src.core.webhook_authenticator import WebhookAuthenticator
from src.core.webhook_validator import WebhookURLValidator

logger = logging.getLogger(__name__)


@dataclass
class WebhookDelivery:
    """Configuration for webhook delivery with retry logic.

    Attributes:
        webhook_url: Target URL for webhook POST request
        payload: JSON payload to send
        headers: HTTP headers (will be modified with signature if secret provided)
        max_retries: Maximum number of retry attempts (default: 3)
        timeout: Request timeout in seconds (default: 10)
        signing_secret: Optional secret for HMAC signing
        event_type: Event type for database tracking (e.g., "creative.status_changed")
        tenant_id: Tenant ID for database tracking
        object_id: Object ID related to webhook (e.g., creative_id)
    """

    webhook_url: str
    payload: dict[str, Any]
    headers: dict[str, str]
    max_retries: int = 3
    timeout: int = 10
    signing_secret: str | None = None
    event_type: str | None = None
    tenant_id: str | None = None
    object_id: str | None = None


def deliver_webhook_with_retry(delivery: WebhookDelivery) -> tuple[bool, dict[str, Any]]:
    """Deliver webhook with exponential backoff retry and database tracking.

    Retry strategy:
    - Attempt 1: Immediate
    - Attempt 2: After 1 second (2^0)
    - Attempt 3: After 2 seconds (2^1)
    - Attempt 4: After 4 seconds (2^2)

    Retry conditions:
    - 5xx errors: Retry (server-side issues)
    - 4xx errors: Do NOT retry (client errors, invalid request)
    - Network errors: Retry (timeouts, connection failures)

    Args:
        delivery: WebhookDelivery configuration object

    Returns:
        Tuple of (success: bool, result: dict) where result contains:
        - delivery_id: Unique ID for this delivery attempt
        - status: "delivered" or "failed"
        - attempts: Number of attempts made
        - response_code: HTTP status code (if received)
        - error: Error message (if failed)
    """
    from src.core.metrics import webhook_delivery_attempts, webhook_delivery_duration, webhook_delivery_total

    # Reject delivery for paused media buys
    if delivery.payload.get("status") == "paused":
        logger.info("[Webhook Delivery] Rejected: media buy is paused")
        return False, {
            "status": "rejected",
            "error": "Media buy is paused",
            "attempts": 0,
        }

    # Validate webhook URL for SSRF protection
    is_valid, error_msg = WebhookURLValidator.validate_webhook_url(delivery.webhook_url)
    if not is_valid:
        logger.error(f"Webhook URL validation failed: {error_msg}")
        # Record validation failure metrics
        if delivery.tenant_id and delivery.event_type:
            webhook_delivery_total.labels(
                tenant_id=delivery.tenant_id, event_type=delivery.event_type, status="validation_failed"
            ).inc()
        return False, {"status": "failed", "error": f"Invalid webhook URL: {error_msg}", "attempts": 0}

    # Generate delivery ID for tracking
    delivery_id = f"whd_{uuid.uuid4().hex[:12]}"

    # Add HMAC signature if secret provided
    headers = delivery.headers.copy()
    if delivery.signing_secret:
        signature_headers = WebhookAuthenticator.sign_payload(delivery.payload, delivery.signing_secret)
        headers.update(signature_headers)

    # Track delivery attempts
    attempts = 0
    last_error = None
    response_code = None
    start_time = time.time()

    # Create initial database record if tracking is enabled
    if delivery.tenant_id and delivery.event_type:
        _create_delivery_record(
            delivery_id=delivery_id,
            tenant_id=delivery.tenant_id,
            webhook_url=delivery.webhook_url,
            payload=delivery.payload,
            event_type=delivery.event_type,
            object_id=delivery.object_id,
        )

    for attempt in range(delivery.max_retries):
        attempts += 1
        attempt_start = time.time()

        try:
            logger.info(
                f"[Webhook Delivery] Attempt {attempt + 1}/{delivery.max_retries} for {delivery_id} to {delivery.webhook_url}"
            )

            response = requests.post(
                delivery.webhook_url, json=delivery.payload, headers=headers, timeout=delivery.timeout
            )

            response_code = response.status_code
            attempt_duration = time.time() - attempt_start

            logger.debug(f"[Webhook Delivery] Response: {response_code} in {attempt_duration:.2f}s for {delivery_id}")

            # Success: 2xx status codes
            if 200 <= response_code < 300:
                total_duration = time.time() - start_time
                logger.info(
                    f"[Webhook Delivery] SUCCESS: {delivery_id} delivered in {total_duration:.2f}s after {attempts} attempts"
                )

                # Update database record
                if delivery.tenant_id and delivery.event_type:
                    _update_delivery_record(
                        delivery_id=delivery_id,
                        tenant_id=delivery.tenant_id,
                        status="delivered",
                        attempts=attempts,
                        response_code=response_code,
                        delivered_at=datetime.now(UTC),
                    )

                    # Record success metrics
                    webhook_delivery_total.labels(
                        tenant_id=delivery.tenant_id, event_type=delivery.event_type, status="success"
                    ).inc()
                    webhook_delivery_duration.labels(
                        tenant_id=delivery.tenant_id, event_type=delivery.event_type
                    ).observe(total_duration)
                    webhook_delivery_attempts.labels(
                        tenant_id=delivery.tenant_id, event_type=delivery.event_type
                    ).observe(attempts)

                return True, {
                    "delivery_id": delivery_id,
                    "status": "delivered",
                    "attempts": attempts,
                    "response_code": response_code,
                    "duration": total_duration,
                }

            # Client errors (4xx): Don't retry
            if 400 <= response_code < 500:
                error_msg = f"Client error {response_code}: {response.text[:200]}"
                logger.warning(f"[Webhook Delivery] Client error, will NOT retry: {error_msg}")
                last_error = error_msg

                # Auth failures (401/403): block until credentials reconfigured
                if response_code in (401, 403) and delivery.tenant_id:
                    _set_auth_blocked(delivery.tenant_id, delivery.webhook_url)

                # Update database record
                if delivery.tenant_id and delivery.event_type:
                    _update_delivery_record(
                        delivery_id=delivery_id,
                        tenant_id=delivery.tenant_id,
                        status="failed",
                        attempts=attempts,
                        response_code=response_code,
                        last_error=error_msg,
                    )

                    # Record client error metrics
                    webhook_delivery_total.labels(
                        tenant_id=delivery.tenant_id, event_type=delivery.event_type, status="client_error"
                    ).inc()

                return False, {
                    "delivery_id": delivery_id,
                    "status": "failed",
                    "attempts": attempts,
                    "response_code": response_code,
                    "error": error_msg,
                }

            # Server errors (5xx): Retry
            if response_code >= 500:
                error_msg = f"Server error {response_code}: {response.text[:200]}"
                logger.warning(f"[Webhook Delivery] Server error, will retry: {error_msg}")
                last_error = error_msg

        except requests.exceptions.Timeout:
            error_msg = f"Request timeout after {delivery.timeout}s"
            logger.warning(f"[Webhook Delivery] Timeout, will retry: {error_msg}")
            last_error = error_msg

        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: {str(e)[:200]}"
            logger.warning(f"[Webhook Delivery] Connection error, will retry: {error_msg}")
            last_error = error_msg

        except requests.exceptions.RequestException as e:
            error_msg = f"Request exception: {str(e)[:200]}"
            logger.warning(f"[Webhook Delivery] Request exception, will retry: {error_msg}")
            last_error = error_msg

        # Exponential backoff before next retry (unless this was the last attempt)
        if attempt < delivery.max_retries - 1:
            backoff_time = 2**attempt  # 1s, 2s, 4s
            logger.debug(f"[Webhook Delivery] Backing off {backoff_time}s before retry")
            time.sleep(backoff_time)

    # All retries exhausted
    total_duration = time.time() - start_time
    logger.error(f"[Webhook Delivery] FAILED: {delivery_id} failed after {attempts} attempts in {total_duration:.2f}s")

    # Update database record and record failure metrics
    if delivery.tenant_id and delivery.event_type:
        _update_delivery_record(
            delivery_id=delivery_id,
            tenant_id=delivery.tenant_id,
            status="failed",
            attempts=attempts,
            response_code=response_code,
            last_error=last_error or "Max retries exceeded",
        )

        # Record failure metrics (max retries exceeded)
        webhook_delivery_total.labels(
            tenant_id=delivery.tenant_id, event_type=delivery.event_type, status="max_retries_exceeded"
        ).inc()
        webhook_delivery_duration.labels(tenant_id=delivery.tenant_id, event_type=delivery.event_type).observe(
            total_duration
        )
        webhook_delivery_attempts.labels(tenant_id=delivery.tenant_id, event_type=delivery.event_type).observe(attempts)

    return False, {
        "delivery_id": delivery_id,
        "status": "failed",
        "attempts": attempts,
        "response_code": response_code,
        "error": last_error or "Max retries exceeded",
        "duration": total_duration,
    }


def _create_delivery_record(
    delivery_id: str,
    tenant_id: str,
    webhook_url: str,
    payload: dict[str, Any],
    event_type: str,
    object_id: str | None = None,
) -> None:
    """Create initial webhook delivery record in database.

    Args:
        delivery_id: Unique delivery identifier
        tenant_id: Tenant ID
        webhook_url: Target webhook URL
        payload: JSON payload being sent
        event_type: Type of event (e.g., "creative.status_changed")
        object_id: Optional object ID related to webhook
    """
    try:
        from src.core.database.repositories.delivery import DeliveryRepository

        with get_db_session() as session:
            repo = DeliveryRepository(session, tenant_id)
            repo.create_record(
                delivery_id=delivery_id,
                webhook_url=webhook_url,
                payload=payload,
                event_type=event_type,
                object_id=object_id,
                created_at=datetime.now(UTC),
            )
            session.commit()
            logger.debug(f"[Webhook Delivery] Created delivery record: {delivery_id}")
    except Exception as e:
        # Don't fail delivery if we can't create tracking record
        logger.error(f"[Webhook Delivery] Failed to create delivery record: {e}", exc_info=True)


def _update_delivery_record(
    delivery_id: str,
    tenant_id: str,
    status: str,
    attempts: int,
    response_code: int | None = None,
    last_error: str | None = None,
    delivered_at: datetime | None = None,
) -> None:
    """Update webhook delivery record in database.

    Args:
        delivery_id: Delivery identifier
        tenant_id: Tenant ID for repository scoping
        status: Delivery status ("delivered" or "failed")
        attempts: Number of delivery attempts made
        response_code: HTTP response code (if received)
        last_error: Error message (if failed)
        delivered_at: Timestamp of successful delivery
    """
    try:
        from src.core.database.repositories.delivery import DeliveryRepository

        with get_db_session() as session:
            repo = DeliveryRepository(session, tenant_id)
            result = repo.update_record(
                delivery_id,
                status=status,
                attempts=attempts,
                response_code=response_code,
                last_error=last_error,
                last_attempt_at=datetime.now(UTC),
                delivered_at=delivered_at,
            )
            if result is not None:
                session.commit()
                logger.debug(f"[Webhook Delivery] Updated delivery record: {delivery_id} status={status}")
            else:
                logger.warning(f"[Webhook Delivery] Delivery record not found: {delivery_id}")

    except Exception as e:
        # Don't fail delivery if we can't update tracking record
        logger.error(f"[Webhook Delivery] Failed to update delivery record: {e}", exc_info=True)


def _set_auth_blocked(tenant_id: str, webhook_url: str) -> None:
    """Set auth_blocked_at on PushNotificationConfig for 401/403 auth failures.

    Blocks further delivery attempts until credentials are reconfigured.
    """
    try:
        from sqlalchemy import update

        from src.core.database.models import PushNotificationConfig

        with get_db_session() as session:
            stmt = (
                update(PushNotificationConfig)
                .where(
                    PushNotificationConfig.tenant_id == tenant_id,
                    PushNotificationConfig.url == webhook_url,
                    PushNotificationConfig.auth_blocked_at.is_(None),
                )
                .values(auth_blocked_at=datetime.now(UTC))
            )
            session.execute(stmt)
            session.commit()
            logger.warning(f"[Webhook Delivery] Auth failure blocked endpoint {webhook_url} for tenant {tenant_id}")
    except Exception as e:
        logger.error(f"[Webhook Delivery] Failed to set auth block: {e}", exc_info=True)

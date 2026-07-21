"""
Protocol-level webhook delivery service for A2A/MCP push notifications.

This service handles protocol-level push notifications (operation status updates)
as distinct from application-level webhooks (scheduled reporting delivery).

Protocol-level webhooks are configured via:
- A2A: MessageSendConfiguration.pushNotificationConfig
- MCP: (future) protocol wrapper extension

Application-level webhooks are configured via:
- AdCP: CreateMediaBuyRequest.reporting_webhook
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import requests
from a2a.types import Task, TaskStatusUpdateEvent
from adcp import extract_webhook_result_data, sign_legacy_webhook, to_wire_dict
from adcp.types import McpWebhookPayload

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.lifecycle import register_shutdown
from src.core.logging_config import scrub_control_chars
from src.core.security.webhook_http import (
    UnsafeWebhookTargetError,
    create_pinned_webhook_session,
    post_webhook_status_async,
)

logger = logging.getLogger(__name__)


def _canonical_body_bytes(payload_dict: dict[str, Any]) -> bytes:
    """Serialize a webhook payload to the canonical on-wire bytes.

    Compact separators (``","``/``":"``) per the adcp canonical form
    (adcontextprotocol/adcp#2478) — byte-for-byte identical to what
    ``sign_legacy_webhook`` computes its HMAC over. These EXACT bytes are both
    signed and transmitted (``data=<bytes>``, never ``json=``), so the signature
    can never cover different bytes than the receiver sees on the wire.
    """
    return json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")


def _normalize_localhost_for_docker(url: str) -> str:
    """Replace localhost host with host.docker.internal while preserving userinfo and port."""
    try:
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname.lower() == "localhost":
            userinfo = ""
            if parsed.username:
                userinfo = parsed.username
                if parsed.password:
                    userinfo += f":{parsed.password}"
                userinfo += "@"
            port = f":{parsed.port}" if parsed.port else ""
            new_netloc = f"{userinfo}host.docker.internal{port}"
            return urlunparse(parsed._replace(netloc=new_netloc))
    except Exception:
        logger.debug("Docker URL rewrite failed, using original URL", exc_info=True)
    return url


class ProtocolWebhookService:
    """
    Service for sending protocol-level push notifications to clients.

    Supports authentication schemes:
    - HMAC-SHA256: Signs payload with shared secret
    - Bearer: Sends credentials as Bearer token
    - None: No authentication
    """

    def __init__(self):
        self._session = create_pinned_webhook_session()

    async def send_notification(
        self,
        push_notification_config: PushNotificationConfig,
        payload: Task | TaskStatusUpdateEvent | McpWebhookPayload,
        metadata: dict[str, Any],
    ) -> bool:
        """
        Send a protocol-level push notification to the configured webhook.

        Args:
            push_notification_config: Push notification configuration from protocol layer
            payload: For A2A it can be Task or TaskStatusUpdateEvent types for MCP it wil be McpWebhookPayload.
                Use create_a2a_webhook_payload or create_mcp_webhook_payload from adcp's official python client to get the payload for particular task and status
            metadata: Contains app specific metadata's such as task_type, tenant_id, principal_id

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not push_notification_config or not push_notification_config.url:
            # TODO: @yusuf - Double check logging actually works for Task, TaskStatusUpdateEvent and McpWebhookPayload types
            logger.debug(
                f"No webhook URL configured in the push notification. Here's payload: {payload}, skipping notification"
            )
            return False

        url = _normalize_localhost_for_docker(push_notification_config.url)

        # Prepare headers
        headers = {"Content-Type": "application/json", "User-Agent": "AdCP-Sales-Agent/1.0"}

        # Log sanitized config (exclude sensitive authentication_token)
        safe_config = {
            "url": push_notification_config.url if hasattr(push_notification_config, "url") else None,
            "authentication_type": (
                push_notification_config.authentication_type
                if hasattr(push_notification_config, "authentication_type")
                else None
            ),
            # DO NOT log authentication_token - security risk
        }
        logger.info(f"push_notification_config (sanitized): {scrub_control_chars(str(safe_config))}")

        # The pinned SDK owns the canonical A2A protobuf / MCP Pydantic wire
        # conversion; do not fork that behavior locally.
        payload_dict: dict[str, Any] = to_wire_dict(payload)

        # Serialize the body to exact bytes ONCE. Whatever we sign, we transmit
        # THESE bytes verbatim (``data=body_bytes`` in _post, never ``json=`` which
        # would re-serialize with spaced separators and break the signature).
        body_bytes: bytes

        # Apply authentication based on schemes
        if (
            push_notification_config.authentication_type == "HMAC-SHA256"
            and push_notification_config.authentication_token
        ):
            # Legacy HMAC-SHA256 profile. sign_legacy_webhook returns the signature
            # headers AND the exact compact body bytes it signed — we send those bytes,
            # guaranteeing the HMAC covers precisely what the receiver verifies.
            timestamp = str(int(time.time()))
            sig_headers, body_bytes = sign_legacy_webhook(
                push_notification_config.authentication_token, payload_dict, timestamp=timestamp
            )
            headers.update(sig_headers)
        else:
            # Bearer or unauthenticated: no signature, but still transmit the canonical
            # compact bytes so the body is deterministic and matches the signed form used
            # everywhere else.
            body_bytes = _canonical_body_bytes(payload_dict)
            if (
                push_notification_config.authentication_type == "Bearer"
                and push_notification_config.authentication_token
            ):
                headers["Authorization"] = f"Bearer {push_notification_config.authentication_token}"

        # Send notification with retry logic and logging
        return await self._send_with_retry_and_logging(
            url=url, payload=payload_dict, body=body_bytes, headers=headers, metadata=metadata
        )

    @staticmethod
    def _write_delivery_log(
        *,
        log_id: str,
        tenant_id: str,
        principal_id: str,
        media_buy_id: str,
        webhook_url: str,
        task_type: str,
        status: str,
        sequence_number: int = 1,
        notification_type: str | None = None,
        attempt_count: int = 1,
        http_status_code: int | None = None,
        error_message: str | None = None,
        payload_size_bytes: int | None = None,
        response_time_ms: int | None = None,
        completed_at: datetime | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """Write a webhook delivery log entry via the DeliveryRepository."""
        try:
            with get_db_session() as session:
                repo = DeliveryRepository(session, tenant_id)
                repo.create_log(
                    log_id=log_id,
                    principal_id=principal_id,
                    media_buy_id=media_buy_id,
                    webhook_url=webhook_url,
                    task_type=task_type,
                    status=status,
                    sequence_number=sequence_number,
                    notification_type=notification_type,
                    attempt_count=attempt_count,
                    http_status_code=http_status_code,
                    error_message=error_message,
                    payload_size_bytes=payload_size_bytes,
                    response_time_ms=response_time_ms,
                    completed_at=completed_at,
                    next_retry_at=next_retry_at,
                )
                session.commit()
        except Exception as e:
            logger.error(f"Failed to write webhook delivery log: {e}")

    async def _send_with_retry_and_logging(
        self,
        url: str,
        payload: dict[str, Any],
        body: bytes,
        headers: dict,
        metadata: dict[str, Any],
        max_attempts: int = 3,
    ) -> bool:
        """Send webhook with exponential backoff retry logic, logging, and audit trail.

        ``body`` is the exact serialized bytes to transmit — the same bytes any
        signature header covers. It is sent verbatim via ``data=`` so the wire body
        can never diverge from the signed body. ``payload`` is the parsed dict, used
        only for metadata extraction (task_id, notification_type, sequence).
        """
        # Payload size metric reflects the ACTUAL transmitted bytes.
        payload_size_bytes = len(body)

        task_type = metadata["task_type"] if "task_type" in metadata else None
        tenant_id = metadata["tenant_id"] if "tenant_id" in metadata else None
        principal_id = metadata["principal_id"] if "principal_id" in metadata else None
        media_buy_id = metadata["media_buy_id"] if "media_buy_id" in metadata else None

        # TODO: Fix type annotation discrepancy in adcp library - extract_webhook_result_data
        # returns dict at runtime but is typed as AdcpAsyncResponseData | None
        result = cast(dict[str, Any] | None, extract_webhook_result_data(payload))
        # After serialization, payload is always a dict - extract task_id accordingly.
        # A2A Task uses 'id'; A2A TaskStatusUpdateEvent uses camelCase 'taskId' (proto
        # json_name wire contract); MCP uses snake_case 'task_id'.
        task_id = payload.get("id") or payload.get("taskId") or payload.get("task_id") or ""

        # If we are delivering media buy delivery report
        notification_type_from_result = result.get("notification_type") if result is not None else None
        sequence_number_from_result = result.get("sequence_number") if result is not None else None
        notification_type = notification_type_from_result
        sequence_number = sequence_number_from_result if isinstance(sequence_number_from_result, int) else 1

        # Create webhook delivery log entry
        log_id = str(uuid4())
        start_time = time.time()

        # Log to audit system (start)
        audit_logger = None
        if tenant_id:
            audit_logger = get_audit_logger("webhook", tenant_id)
            audit_logger.log_info(f"Sending {task_type} webhook for task {task_id} (sequence #{sequence_number})")

        for attempt in range(max_attempts):
            try:
                logger.info(f"Sending webhook for task {task_id} (attempt {attempt + 1}/{max_attempts})")

                status_code = await post_webhook_status_async(
                    self._session,
                    url,
                    body=body,
                    headers=headers,
                    timeout=10.0,
                )
                # Require a 2xx. raise_for_status() does NOT raise for 3xx, and with
                # redirects disabled a 3xx is a REFUSED redirect — a failed delivery,
                # not a success. Treat any non-2xx uniformly via the HTTPError path.
                if not (200 <= status_code < 300):
                    response = requests.Response()
                    response.status_code = status_code
                    raise requests.HTTPError(
                        f"Webhook returned non-2xx status {status_code}",
                        response=response,
                    )

                # Calculate response time
                response_time_ms = int((time.time() - start_time) * 1000)

                logger.info(f"Successfully sent webhook for task {task_id} (status: {status_code})")

                # Write to webhook_delivery_log (success)
                if (
                    task_type in ("delivery_report", "media_buy_delivery")
                    and media_buy_id
                    and tenant_id
                    and principal_id
                ):
                    self._write_delivery_log(
                        log_id=log_id,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        media_buy_id=media_buy_id,
                        webhook_url=url,
                        task_type=task_type,
                        status="success",
                        sequence_number=sequence_number,
                        notification_type=notification_type,
                        attempt_count=attempt + 1,
                        http_status_code=status_code,
                        payload_size_bytes=payload_size_bytes,
                        response_time_ms=response_time_ms,
                        completed_at=datetime.now(UTC),
                    )

                # Log to audit system (success)
                if audit_logger:
                    audit_logger.log_success(
                        f"{task_type} webhook delivered successfully (sequence #{sequence_number}, "
                        f"{response_time_ms}ms, {payload_size_bytes} bytes)"
                    )

                return True

            except requests.HTTPError as e:
                error_status_code = e.response.status_code if e.response is not None else None
                response_time_ms = int((time.time() - start_time) * 1000)
                error_message = f"HTTP {error_status_code}: {str(e)}"

                # Refused redirects and client errors are permanent for this
                # delivery attempt. Retrying cannot make a 3xx/4xx target safe
                # or valid, and would multiply outbound traffic.
                if error_status_code and 300 <= error_status_code < 500:
                    logger.error(
                        f"Webhook failed for task {task_id} with permanent HTTP {error_status_code} - not retrying"
                    )

                    # Write to webhook_delivery_log (failed)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        self._write_delivery_log(
                            log_id=log_id,
                            tenant_id=tenant_id,
                            principal_id=principal_id,
                            media_buy_id=media_buy_id,
                            webhook_url=url,
                            task_type=task_type,
                            status="failed",
                            sequence_number=sequence_number,
                            notification_type=notification_type,
                            attempt_count=attempt + 1,
                            http_status_code=error_status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            completed_at=datetime.now(UTC),
                        )

                    # Log to audit system (failure)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed with permanent HTTP {error_status_code}")

                    return False

                # Retry on 5xx errors (server errors - transient)
                if attempt < max_attempts - 1:
                    wait_seconds = min(2**attempt, 60)  # Exponential backoff, max 60 seconds
                    logger.warning(
                        f"Webhook failed for task {task_id}: HTTP {error_status_code}. "
                        f"Retrying in {wait_seconds}s (attempt {attempt + 1}/{max_attempts})"
                    )

                    # Write to webhook_delivery_log (retrying)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        next_retry = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=wait_seconds)
                        self._write_delivery_log(
                            log_id=log_id,
                            tenant_id=tenant_id,
                            principal_id=principal_id,
                            media_buy_id=media_buy_id,
                            webhook_url=url,
                            task_type=task_type,
                            status="retrying",
                            sequence_number=sequence_number,
                            notification_type=notification_type,
                            attempt_count=attempt + 1,
                            http_status_code=error_status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            next_retry_at=next_retry,
                        )

                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(
                        f"Webhook failed for task {task_id} after {max_attempts} attempts: HTTP {error_status_code}"
                    )

                    # Write to webhook_delivery_log (failed after all retries)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        self._write_delivery_log(
                            log_id=log_id,
                            tenant_id=tenant_id,
                            principal_id=principal_id,
                            media_buy_id=media_buy_id,
                            webhook_url=url,
                            task_type=task_type,
                            status="failed",
                            sequence_number=sequence_number,
                            notification_type=notification_type,
                            attempt_count=max_attempts,
                            http_status_code=error_status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            completed_at=datetime.now(UTC),
                        )

                    # Log to audit system (failure after all retries)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed after {max_attempts} attempts")

                    return False

            except UnsafeWebhookTargetError as e:
                # SSRF, embedded-credential, and proxy-bypass refusals are
                # deterministic policy failures. Never retry them as if they
                # were transient network outages.
                logger.error(f"Webhook target refused for task {task_id} - not retrying ({type(e).__name__})")
                if audit_logger:
                    audit_logger.log_warning(f"{task_type} webhook target refused by security policy")
                return False

            except requests.RequestException as e:
                response_time_ms = int((time.time() - start_time) * 1000)
                error_message = f"{type(e).__name__}: {str(e)}"

                # Network errors - retry
                if attempt < max_attempts - 1:
                    wait_seconds = min(2**attempt, 60)
                    logger.warning(
                        f"Webhook network error for task {task_id}: {type(e).__name__}. "
                        f"Retrying in {wait_seconds}s (attempt {attempt + 1}/{max_attempts})"
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(
                        f"Webhook failed for task {task_id} after {max_attempts} attempts: {type(e).__name__} - {e}"
                    )

                    # Write to webhook_delivery_log (failed)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        self._write_delivery_log(
                            log_id=log_id,
                            tenant_id=tenant_id,
                            principal_id=principal_id,
                            media_buy_id=media_buy_id,
                            webhook_url=url,
                            task_type=task_type,
                            status="failed",
                            sequence_number=sequence_number,
                            notification_type=notification_type,
                            attempt_count=max_attempts,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            completed_at=datetime.now(UTC),
                        )

                    # Log to audit system (network failure)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed with network error: {type(e).__name__}")

                    return False

            except Exception as e:
                logger.error(f"Unexpected error sending webhook for task {task_id}: {e}")

                # Write to webhook_delivery_log (unexpected failure)
                if (
                    task_type in ("delivery_report", "media_buy_delivery")
                    and media_buy_id
                    and tenant_id
                    and principal_id
                ):
                    self._write_delivery_log(
                        log_id=log_id,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        media_buy_id=media_buy_id,
                        webhook_url=url,
                        task_type=task_type,
                        status="failed",
                        sequence_number=sequence_number,
                        notification_type=notification_type,
                        attempt_count=attempt + 1,
                        error_message=f"Unexpected error: {str(e)}",
                        payload_size_bytes=payload_size_bytes,
                        completed_at=datetime.now(UTC),
                    )

                return False

        # Should never reach here
        return False

    async def close(self):
        """Close HTTP client."""
        self._session.close()


# Global service instance
_webhook_service: ProtocolWebhookService | None = None


def get_protocol_webhook_service() -> ProtocolWebhookService:
    """Get or create global webhook service instance.

    On first construction, self-registers ``close`` with the shutdown
    registry so the long-lived ``requests.Session`` connection pool is
    released on FastAPI lifespan shutdown — the service owns its own
    lifecycle.
    """
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = ProtocolWebhookService()
        register_shutdown(_webhook_service.close)
    return _webhook_service


def get_webhook_service_or_none() -> ProtocolWebhookService | None:
    """Return the current singleton instance, or None if never constructed.

    Distinct from :func:`get_protocol_webhook_service`: this does NOT trigger
    construction. Use it from shutdown hooks where you only want to close an
    *existing* instance, not create one (and its long-lived ``requests.Session``
    connection pool) just to immediately close it.

    Resolving the singleton through this function call is location-independent:
    it reads the live module global at call time, so callers may import it at
    module top-level without the lazy-import tripwire that a direct
    ``from ... import _webhook_service`` would introduce (a hoisted private
    import binds the initial ``None`` forever).
    """
    return _webhook_service

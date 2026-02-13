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
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import requests
from a2a.types import Task, TaskStatusUpdateEvent
from adcp import extract_webhook_result_data, get_adcp_signed_headers_for_webhook
from adcp.types import McpWebhookPayload

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import PushNotificationConfig, WebhookDeliveryLog

logger = logging.getLogger(__name__)


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
        # If anything goes wrong, fall back to the original URL
        pass
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
        self._session = requests.Session()

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
        logger.info(f"push_notification_config (sanitized): {safe_config}")

        # Serialize payload to dict for signing and sending
        # Task/TaskStatusUpdateEvent need serialization; McpWebhookPayload is already SalesAgentBaseModel
        payload_dict: dict[str, Any]
        if isinstance(payload, (Task, TaskStatusUpdateEvent)):
            payload_dict = payload.model_dump(mode="json", exclude_none=True)
        elif isinstance(payload, McpWebhookPayload):
            payload_dict = payload.model_dump(mode="json", exclude_none=True)
        else:
            payload_dict = payload

        # Apply authentication based on schemes
        if (
            push_notification_config.authentication_type == "HMAC-SHA256"
            and push_notification_config.authentication_token
        ):
            # Sign payload with HMAC-SHA256
            timestamp = str(int(time.time()))
            get_adcp_signed_headers_for_webhook(
                headers, push_notification_config.authentication_token, timestamp, payload_dict
            )

        elif push_notification_config.authentication_type == "Bearer" and push_notification_config.authentication_token:
            # Use Bearer token authentication
            headers["Authorization"] = f"Bearer {push_notification_config.authentication_token}"

        # Send notification with retry logic and logging
        return await self._send_with_retry_and_logging(
            url=url, payload=payload_dict, headers=headers, metadata=metadata
        )

    async def _send_with_retry_and_logging(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict,
        metadata: dict[str, Any],
        max_attempts: int = 3,
    ) -> bool:
        """Send webhook with exponential backoff retry logic, logging, and audit trail."""
        # Calculate payload size for metrics
        payload_size_bytes = len(json.dumps(payload).encode("utf-8"))

        task_type = metadata["task_type"] if "task_type" in metadata else None
        tenant_id = metadata["tenant_id"] if "tenant_id" in metadata else None
        principal_id = metadata["principal_id"] if "principal_id" in metadata else None
        media_buy_id = metadata["media_buy_id"] if "media_buy_id" in metadata else None

        # TODO: Fix type annotation discrepancy in adcp library - extract_webhook_result_data
        # returns dict at runtime but is typed as AdcpAsyncResponseData | None
        result = cast(dict[str, Any] | None, extract_webhook_result_data(payload))
        # After serialization, payload is always a dict - extract task_id accordingly
        # A2A Task uses 'id', TaskStatusUpdateEvent uses 'task_id', MCP uses 'task_id'
        task_id = payload.get("id") or payload.get("task_id") or ""

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
                logger.info(f"Sending webhook for task {task_id} to {url} (attempt {attempt + 1}/{max_attempts})")

                def _post() -> requests.Response:
                    return self._session.post(url, json=payload, headers=headers, timeout=10.0)

                response = await asyncio.to_thread(_post)
                response.raise_for_status()

                # Calculate response time
                response_time_ms = int((time.time() - start_time) * 1000)

                logger.info(f"Successfully sent webhook for task {task_id} (status: {response.status_code})")

                # Write to webhook_delivery_log (success)
                if (
                    task_type in ("delivery_report", "media_buy_delivery")
                    and media_buy_id
                    and tenant_id
                    and principal_id
                ):
                    try:
                        with get_db_session() as session:
                            log_entry = WebhookDeliveryLog(
                                id=log_id,
                                tenant_id=tenant_id,
                                principal_id=principal_id,
                                media_buy_id=media_buy_id,
                                webhook_url=url,
                                task_type=task_type,
                                sequence_number=sequence_number,
                                notification_type=notification_type,
                                attempt_count=attempt + 1,
                                status="success",
                                http_status_code=response.status_code,
                                payload_size_bytes=payload_size_bytes,
                                response_time_ms=response_time_ms,
                                completed_at=datetime.now(UTC),
                            )
                            session.merge(log_entry)
                            session.commit()
                    except Exception as e:
                        logger.error(f"Failed to write webhook delivery log: {e}")

                # Log to audit system (success)
                if audit_logger:
                    audit_logger.log_success(
                        f"{task_type} webhook delivered successfully (sequence #{sequence_number}, "
                        f"{response_time_ms}ms, {payload_size_bytes} bytes)"
                    )

                return True

            except requests.HTTPError as e:
                status_code = e.response.status_code if e.response else None
                response_time_ms = int((time.time() - start_time) * 1000)
                error_message = f"HTTP {status_code}: {str(e)}"

                # Don't retry on 4xx errors (client errors - permanent failures)
                if status_code and 400 <= status_code < 500:
                    logger.error(f"Webhook failed for task {task_id} with client error {status_code} - not retrying")

                    # Write to webhook_delivery_log (failed)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        try:
                            with get_db_session() as session:
                                log_entry = WebhookDeliveryLog(
                                    id=log_id,
                                    tenant_id=tenant_id,
                                    principal_id=principal_id,
                                    media_buy_id=media_buy_id,
                                    webhook_url=url,
                                    task_type=task_type,
                                    sequence_number=sequence_number,
                                    notification_type=notification_type,
                                    attempt_count=attempt + 1,
                                    status="failed",
                                    http_status_code=status_code,
                                    error_message=error_message,
                                    payload_size_bytes=payload_size_bytes,
                                    response_time_ms=response_time_ms,
                                    completed_at=datetime.now(UTC),
                                )
                                session.merge(log_entry)
                                session.commit()
                        except Exception as e:
                            logger.error(f"Failed to write webhook delivery log: {e}")

                    # Log to audit system (failure)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed with client error {status_code}")

                    return False

                # Retry on 5xx errors (server errors - transient)
                if attempt < max_attempts - 1:
                    wait_seconds = min(2**attempt, 60)  # Exponential backoff, max 60 seconds
                    logger.warning(
                        f"Webhook failed for task {task_id}: HTTP {status_code}. "
                        f"Retrying in {wait_seconds}s (attempt {attempt + 1}/{max_attempts})"
                    )

                    # Write to webhook_delivery_log (retrying)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        try:
                            with get_db_session() as session:
                                next_retry = datetime.now(UTC).replace(microsecond=0)
                                next_retry = next_retry.replace(second=next_retry.second + int(wait_seconds))

                                log_entry = WebhookDeliveryLog(
                                    id=log_id,
                                    tenant_id=tenant_id,
                                    principal_id=principal_id,
                                    media_buy_id=media_buy_id,
                                    webhook_url=url,
                                    task_type=task_type,
                                    sequence_number=sequence_number,
                                    notification_type=notification_type,
                                    attempt_count=attempt + 1,
                                    status="retrying",
                                    http_status_code=status_code,
                                    error_message=error_message,
                                    payload_size_bytes=payload_size_bytes,
                                    response_time_ms=response_time_ms,
                                    next_retry_at=next_retry,
                                )
                                session.merge(log_entry)
                                session.commit()
                        except Exception as e:
                            logger.error(f"Failed to write webhook delivery log: {e}")

                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(f"Webhook failed for task {task_id} after {max_attempts} attempts: HTTP {status_code}")

                    # Write to webhook_delivery_log (failed after all retries)
                    if (
                        task_type in ("delivery_report", "media_buy_delivery")
                        and media_buy_id
                        and tenant_id
                        and principal_id
                    ):
                        try:
                            with get_db_session() as session:
                                log_entry = WebhookDeliveryLog(
                                    id=log_id,
                                    tenant_id=tenant_id,
                                    principal_id=principal_id,
                                    media_buy_id=media_buy_id,
                                    webhook_url=url,
                                    task_type=task_type,
                                    sequence_number=sequence_number,
                                    notification_type=notification_type,
                                    attempt_count=max_attempts,
                                    status="failed",
                                    http_status_code=status_code,
                                    error_message=error_message,
                                    payload_size_bytes=payload_size_bytes,
                                    response_time_ms=response_time_ms,
                                    completed_at=datetime.now(UTC),
                                )
                                session.merge(log_entry)
                                session.commit()
                        except Exception as e:
                            logger.error(f"Failed to write webhook delivery log: {e}")

                    # Log to audit system (failure after all retries)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed after {max_attempts} attempts")

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
                        try:
                            with get_db_session() as session:
                                log_entry = WebhookDeliveryLog(
                                    id=log_id,
                                    tenant_id=tenant_id,
                                    principal_id=principal_id,
                                    media_buy_id=media_buy_id,
                                    webhook_url=url,
                                    task_type=task_type,
                                    sequence_number=sequence_number,
                                    notification_type=notification_type,
                                    attempt_count=max_attempts,
                                    status="failed",
                                    error_message=error_message,
                                    payload_size_bytes=payload_size_bytes,
                                    response_time_ms=response_time_ms,
                                    completed_at=datetime.now(UTC),
                                )
                                session.merge(log_entry)
                                session.commit()
                        except Exception as log_err:
                            logger.error(f"Failed to write webhook delivery log: {log_err}")

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
                    try:
                        with get_db_session() as session:
                            log_entry = WebhookDeliveryLog(
                                id=log_id,
                                tenant_id=tenant_id,
                                principal_id=principal_id,
                                media_buy_id=media_buy_id,
                                webhook_url=url,
                                task_type=task_type,
                                sequence_number=sequence_number,
                                notification_type=notification_type,
                                attempt_count=attempt + 1,
                                status="failed",
                                error_message=f"Unexpected error: {str(e)}",
                                payload_size_bytes=payload_size_bytes,
                                completed_at=datetime.now(UTC),
                            )
                            session.merge(log_entry)
                            session.commit()
                    except Exception as log_err:
                        logger.error(f"Failed to write webhook delivery log: {log_err}")

                return False

        # Should never reach here
        return False

    async def close(self):
        """Close HTTP client."""
        self._session.close()


# Global service instance
_webhook_service: ProtocolWebhookService | None = None


def get_protocol_webhook_service() -> ProtocolWebhookService:
    """Get or create global webhook service instance."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = ProtocolWebhookService()
    return _webhook_service

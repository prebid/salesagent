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
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
from a2a.types import Task, TaskStatusUpdateEvent
from adcp import extract_webhook_result_data
from adcp.types import McpWebhookPayload
from adcp.webhooks import generate_webhook_idempotency_key
from google.protobuf.json_format import MessageToDict

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.lifecycle import register_shutdown
from src.core.signing import deliver_adcp_webhook
from src.core.webhook_validator import (
    reject_unsafe_outbound_webhook_url,
    webhook_url_for_log,
)

logger = logging.getLogger(__name__)


class _WebhookStatusError(Exception):
    """A delivery that reached the receiver and came back non-2xx.

    The retry ladder below branches on 4xx (permanent) vs 5xx (transient) exactly as
    it did when the sender raised ``requests.HTTPError``; raising keeps that ladder
    in one ``except`` arm instead of duplicating it inline after the call.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(f"webhook receiver returned HTTP {status_code}")
        self.status_code = status_code


# FIXME(gh-#1299): behaviour-identical backport of adcp 5.4.0
# ``adcp.to_wire_dict`` + ``_normalize_a2a_task_state_to_v03`` (adcp #602).
# salesagent is pinned to adcp 4.3.0, which predates that public seam.
# Delete this block and call ``adcp.to_wire_dict()`` directly once salesagent
# bumps adcp to the version that ships it.
def _normalize_message_role(message: dict[str, Any]) -> None:
    """Rewrite a2a-sdk 1.0 ``ROLE_*`` to the A2A 0.3 lowercase wire form."""
    role = message.get("role")
    if isinstance(role, str) and role.startswith("ROLE_"):
        message["role"] = role[len("ROLE_") :].lower()


def _normalize_a2a_task_state_to_v03(payload: dict[str, Any]) -> None:
    """Rewrite a2a-sdk 1.0 ``TASK_STATE_*`` / ``ROLE_*`` enums to A2A 0.3
    lowercase wire strings in-place. Buyer receivers parse the 0.3 shape
    (``"state": "completed"``); the 1.0 protobuf JSON emitter produces
    ``"state": "TASK_STATE_COMPLETED"`` by default.
    """
    status = payload.get("status")
    if isinstance(status, dict):
        state = status.get("state")
        if isinstance(state, str) and state.startswith("TASK_STATE_"):
            # Spec uses hyphens for multi-word states (e.g. "auth-required").
            status["state"] = state[len("TASK_STATE_") :].lower().replace("_", "-")
        message = status.get("message")
        if isinstance(message, dict):
            _normalize_message_role(message)
    history = payload.get("history")
    if isinstance(history, list):
        for entry in history:
            if isinstance(entry, dict):
                _normalize_message_role(entry)
    if "role" in payload:
        _normalize_message_role(payload)


def _to_wire_dict(payload: Any) -> dict[str, Any]:
    """Serialize any AdCP webhook payload to a JSON-ready dict.

    Behaviour-identical backport of adcp 5.4.0 ``adcp.to_wire_dict``:

    * a2a ``Task`` / ``TaskStatusUpdateEvent`` (protobuf, a2a-sdk 1.0+) ->
      ``MessageToDict(preserving_proto_field_name=False)`` so JSON keys are
      the A2A wire camelCase (``id``, ``contextId``, ``taskId``), then enum
      values normalized from the 1.0 form (``TASK_STATE_COMPLETED``,
      ``ROLE_AGENT``) to the 0.3-spec lowercase form (``completed``,
      ``agent``).
    * Any Pydantic model (``McpWebhookPayload`` ...) ->
      ``model_dump(mode="json", exclude_none=True)``.
    * ``Mapping`` -> coerced to ``dict`` (legacy hand-built passthrough).
    """
    if isinstance(payload, (Task, TaskStatusUpdateEvent)):
        data: dict[str, Any] = MessageToDict(payload, preserving_proto_field_name=False)
        _normalize_a2a_task_state_to_v03(data)
        return data
    if hasattr(payload, "model_dump"):
        return cast(dict[str, Any], payload.model_dump(mode="json", exclude_none=True))
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError(
        f"Unsupported webhook payload type {type(payload).__name__}: expected "
        "a2a Task / TaskStatusUpdateEvent (protobuf), an AdCP Pydantic model "
        "(e.g. McpWebhookPayload), or a Mapping[str, Any]."
    )


class ProtocolWebhookService:
    """
    Service for sending protocol-level push notifications to clients.

    How a delivery is authenticated is NOT decided here: the receiver's own
    ``PushNotificationConfig`` row selects exactly one mode at the single boundary,
    ``src.core.signing.webhook_sender_factory`` (#1291 C1). This service owns
    retry/backoff, delivery logging and the audit trail.

    It also owns ONE long-lived ``httpx.AsyncClient`` so protocol notifications keep
    a connection pool across deliveries; the signing boundary borrows it rather than
    opening a socket per webhook, and ``close`` releases it on lifespan shutdown.
    """

    def __init__(self) -> None:
        # ``follow_redirects=False`` is httpx's default, stated here because it is a
        # security property this service depends on, not an incidental one: the SSRF
        # gate judges the configured URL, so a followed 302 to a metadata/private IP
        # would slip past it (open-redirect SSRF).
        self._client = httpx.AsyncClient(timeout=10.0, follow_redirects=False)

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

        # SSRF gate on the configured URL *before* docker localhost rewrite.
        # Under ADCP_TESTING, localhost/loopback is allowed for capture servers;
        # production uses the full DNS-backed check (HTTPS required).
        rejected, _error_msg = reject_unsafe_outbound_webhook_url(
            push_notification_config.url,
            log=logger,
            kind="Protocol",
        )
        if rejected:
            return False

        # The URL the gate judged IS the URL dialled. There is no rewrite hop: one
        # used to swap ``localhost`` for ``host.docker.internal`` so a containerised
        # server could reach a receiver on the developer's host, but that host is
        # itself in BLOCKED_HOSTNAMES — so the gate approved one destination and the
        # process dialled another the gate refuses, making the gate advisory. The e2e
        # stack now delivers to a real HTTPS origin on a non-private address
        # (webhooks.adcp-e2e.dev), which the gate accepts unpatched, so nothing needs
        # the hop. It matters for signing too: ``@target-uri`` and ``@authority`` are
        # covered components of the RFC 9421 signature, so the signed URI is now the
        # registered one rather than a post-rewrite variant.
        url = push_notification_config.url

        # Content-Type is the sender's (it frames the body it serialized), and the
        # auth headers are the boundary's. Only genuinely extra headers go here.
        headers = {"User-Agent": "AdCP-Sales-Agent/1.0"}

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

        # Serialize payload to dict at the delivery boundary (for HMAC signing
        # and JSON send). Single seam: a2a protobuf -> camelCase + A2A 0.3
        # lowercase enum values; Pydantic -> model_dump; Mapping -> dict.
        payload_dict: dict[str, Any] = _to_wire_dict(payload)

        # Send notification with retry logic and logging. Authentication is chosen
        # from ``push_notification_config`` at the boundary, once, for every attempt.
        return await self._send_with_retry_and_logging(
            url=url,
            payload=payload_dict,
            headers=headers,
            metadata=metadata,
            config=push_notification_config,
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
        headers: dict,
        metadata: dict[str, Any],
        config: PushNotificationConfig | None = None,
        max_attempts: int = 3,
    ) -> bool:
        """Send webhook with exponential backoff retry logic, logging, and audit trail."""
        # Calculate payload size for metrics
        payload_size_bytes = len(json.dumps(payload).encode("utf-8"))

        # ONE key per distinct event, reused across this event's retries — a fresh
        # one per attempt would defeat the receiver's dedup (adcp webhooks.mdx).
        idempotency_key = generate_webhook_idempotency_key()

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
                safe_url = webhook_url_for_log(url)
                logger.info(
                    "Sending webhook for task %s to %s (attempt %s/%s)",
                    task_id,
                    safe_url,
                    attempt + 1,
                    max_attempts,
                )

                # Serialize + authenticate + POST as ONE act, so the bytes signed
                # are the bytes sent (#1441). The key material read behind this runs
                # on the loop, the same side as ``_write_delivery_log`` below — this
                # method already does synchronous DB work inline, and moving one of
                # the two behind a thread hop would only look like a rule.
                # Redirects are never followed (``self._client`` is built with
                # ``follow_redirects=False``): a 302 to metadata/private IPs would
                # bypass the pre-POST SSRF check (open-redirect SSRF).
                delivery = await deliver_adcp_webhook(
                    url=url,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    config=config,
                    tenant_id=tenant_id,
                    extra_headers=headers,
                    client=self._client,
                )
                if not 200 <= delivery.status_code < 300:
                    raise _WebhookStatusError(delivery.status_code)

                # Calculate response time
                response_time_ms = int((time.time() - start_time) * 1000)

                logger.info(f"Successfully sent webhook for task {task_id} (status: {delivery.status_code})")

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
                        http_status_code=delivery.status_code,
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

            except _WebhookStatusError as e:
                status_code = e.status_code
                response_time_ms = int((time.time() - start_time) * 1000)
                error_message = f"HTTP {status_code}"

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
                            http_status_code=status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            completed_at=datetime.now(UTC),
                        )

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
                        next_retry = datetime.now(UTC).replace(microsecond=0)
                        next_retry = next_retry.replace(second=next_retry.second + int(wait_seconds))
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
                            http_status_code=status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            next_retry_at=next_retry,
                        )

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
                            http_status_code=status_code,
                            error_message=error_message,
                            payload_size_bytes=payload_size_bytes,
                            response_time_ms=response_time_ms,
                            completed_at=datetime.now(UTC),
                        )

                    # Log to audit system (failure after all retries)
                    if audit_logger:
                        audit_logger.log_warning(f"{task_type} webhook failed after {max_attempts} attempts")

                    return False

            except httpx.HTTPError as e:
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
        """Release the connection pool this service owns (lifespan shutdown)."""
        await self._client.aclose()


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

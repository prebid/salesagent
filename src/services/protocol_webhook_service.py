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
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import requests
from a2a.types import Task, TaskStatusUpdateEvent
from adcp import extract_webhook_result_data, sign_legacy_webhook
from adcp.types import McpWebhookPayload
from google.protobuf.json_format import MessageToDict
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.delivery import DeliveryRepository
from src.core.lifecycle import register_shutdown
from src.core.security.url_validator import resolve_and_validate_target
from src.core.webhook_validator import _allow_private_webhook_targets

logger = logging.getLogger(__name__)


class _PinningHTTPAdapter(HTTPAdapter):
    """requests adapter that SSRF-validates each webhook target and pins the TCP
    connection to the validated IP, keeping TLS SNI + certificate verification bound
    to the ORIGINAL hostname.

    Mounted ONCE on the service's long-lived pooled ``requests.Session`` — so connection
    pooling (and any test that patches ``session.post``) is preserved. Overriding the
    per-request connection seam closes the validate-then-reconnect (DNS-rebinding) gap:
    the address checked by ``resolve_and_validate_target`` is exactly the address the
    socket connects to. Every A/AAAA record is validated; redirects are disabled by the
    caller so a validated URL cannot 302 to a private/metadata target after the check.
    """

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: bool | str | None,
        proxies: Mapping[str, str] | None = None,
        cert: Any = None,
    ) -> Any:
        url = request.url or ""
        allow_private = _allow_private_webhook_targets()
        pinned_ip, ssrf_error = resolve_and_validate_target(
            url, require_https=not allow_private, allow_private=allow_private
        )
        if pinned_ip is None:
            raise requests.RequestException(f"Webhook URL failed SSRF validation: {ssrf_error}")

        # An egress proxy would perform its OWN resolution, defeating host-pinning
        # (the socket connects to the proxy, then the proxy re-resolves the
        # original hostname — reopening the DNS-rebinding gap this adapter closes).
        # No proxy is ever configured intentionally here (the session sets
        # trust_env=False, so env proxies cannot activate this branch), so refuse
        # to deliver via a proxy rather than silently unpinning.
        if select_proxy(url, proxies):
            raise requests.RequestException(
                "Webhook delivery refused: a proxy is configured for this target, which "
                "would bypass SSRF connection-pinning. Webhook egress must be direct."
            )

        # ``verify`` reaches this override typed as Optional to match the base signature;
        # normalize None -> True (default verification) without collapsing an explicit False.
        resolved_verify: bool | str = True if verify is None else verify
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, resolved_verify, cert)
        hostname = host_params["host"]
        host_params["host"] = pinned_ip  # connect the socket to the validated IP
        pinned_kwargs: dict[str, Any] = dict(pool_kwargs)
        if host_params["scheme"] == "https":
            # Send SNI for, and verify the certificate against, the ORIGINAL hostname — not the IP.
            pinned_kwargs["server_hostname"] = hostname
            pinned_kwargs["assert_hostname"] = hostname
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=cast(Any, pinned_kwargs))


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
        self._session = requests.Session()
        # Ignore the process environment for egress: no HTTP(S)_PROXY / NO_PROXY,
        # no ~/.netrc credential injection. An env proxy would defeat the SSRF
        # connection-pinning (see _PinningHTTPAdapter, which now REFUSES a proxied
        # target); netrc could leak credentials to a buyer-controlled webhook host.
        self._session.trust_env = False
        # Pin every webhook delivery to an SSRF-validated IP while preserving the
        # long-lived pooled session (see _PinningHTTPAdapter). One adapter instance
        # serves both schemes.
        pinning_adapter = _PinningHTTPAdapter()
        self._session.mount("https://", pinning_adapter)
        self._session.mount("http://", pinning_adapter)

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

        # Serialize payload to dict at the delivery boundary (for HMAC signing
        # and JSON send). Single seam: a2a protobuf -> camelCase + A2A 0.3
        # lowercase enum values; Pydantic -> model_dump; Mapping -> dict.
        payload_dict: dict[str, Any] = _to_wire_dict(payload)

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
                logger.info(f"Sending webhook for task {task_id} to {url} (attempt {attempt + 1}/{max_attempts})")

                def _post() -> requests.Response:
                    # Redirect-disabled POST over the pooled session whose pinning adapter
                    # (#1512 SSRF) resolves + validates every A/AAAA record and pins the
                    # connection to the validated IP — so a validated URL cannot be
                    # re-resolved (DNS rebinding) or 302-redirected to a private/metadata
                    # target after validation. Host is set explicitly so vhost routing stays
                    # correct even though the socket connects by IP.
                    return self._session.post(
                        url,
                        data=body,
                        headers={**headers, "Host": urlparse(url).netloc},
                        timeout=10.0,
                        allow_redirects=False,
                        # Only the status code is consumed — do not buffer the
                        # (buyer-controlled, potentially large) response body.
                        stream=True,
                    )

                response = await asyncio.to_thread(_post)
                # The status code is available from the response line/headers without
                # reading the body; close immediately to return the connection to the
                # pool without downloading the buyer-controlled body.
                response.close()
                # Require a 2xx. raise_for_status() does NOT raise for 3xx, and with
                # redirects disabled a 3xx is a REFUSED redirect — a failed delivery,
                # not a success. Treat any non-2xx uniformly via the HTTPError path.
                if not (200 <= response.status_code < 300):
                    raise requests.HTTPError(
                        f"Webhook returned non-2xx status {response.status_code}", response=response
                    )

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
                        http_status_code=response.status_code,
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

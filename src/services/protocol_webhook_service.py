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

import logging
import time
from collections.abc import Mapping
from typing import Any, Protocol, cast
from uuid import uuid4

from a2a.types import Task, TaskStatusUpdateEvent
from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import McpWebhookPayload
from adcp.webhooks import GeneratedTaskStatus
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel as PydanticBaseModel

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.security.webhook_egress import adeliver_webhook
from src.core.webhook_validator import validate_webhook_task_type, webhook_url_for_log
from src.core.webhooks.delivery import WebhookDeliveryOutcome, WebhookTaskContext
from src.services.webhook_conclusion import record_conclusion


class DeliverableWebhookTarget(Protocol):
    """What this sender actually needs off a push-notification config: three fields.

    Structural, and READ-ONLY on purpose. Two kinds of object arrive here — the
    stored ORM ``PushNotificationConfig`` row, and the
    ``ValidatedWebhookRegistration`` value handed straight from the A2A protocol
    stash — and both satisfy this without either knowing about the other. Before
    this, the annotation named the ORM class, so the A2A path fabricated a
    detached row with ``tenant_id=""`` / ``principal_id=""`` purely to type-check:
    a config-shaped object with empty scope ids, which is exactly how an
    unreceipted config reached a sender.

    Declared as properties rather than plain attributes because a Protocol with
    mutable attributes is invariant, and would then REFUSE a frozen(slots)
    dataclass — the read-only form admits both.
    """

    @property
    def url(self) -> str: ...

    @property
    def authentication_type(self) -> str | None: ...

    @property
    def authentication_token(self) -> str | None: ...


logger = logging.getLogger(__name__)


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

    Supports authentication schemes:
    - HMAC-SHA256: Signs payload with shared secret
    - Bearer: Sends credentials as Bearer token
    - None: No authentication
    """

    async def notify(
        self,
        push_notification_config: DeliverableWebhookTarget,
        *,
        task: WebhookTaskContext,
        status: GeneratedTaskStatus,
        result: PydanticBaseModel | dict[str, Any],
        protocol: str,
        context_id: str = "",
    ) -> bool:
        """Deliver one protocol notification from VALUES, choosing the dialect here.

        THE delivery entry point. Every sender used to re-derive the same two
        decisions at its own call site: which payload builder to call
        (``create_a2a_webhook_payload`` vs ``create_mcp_webhook_payload``, forked
        on ``protocol``), and what to put in a free-form ``metadata`` dict. Seven
        files forked the dialect and six built the dict, which is how
        ``delivery_webhook_scheduler`` came to import only the MCP builder — a
        buyer registered over A2A receives an MCP-shaped delivery report from it.

        Taking a typed :class:`WebhookTaskContext` instead of ``metadata:
        dict[str, Any]`` is what closes the other half. ``records_delivery_log``
        needs ``tenant_id`` and ``principal_id``; the admin sender passed
        ``{"task_type": ...}`` alone, so admin-originated deliveries wrote no
        ``webhook_delivery_log`` row and said nothing about it. A caller now has
        to name those fields to construct the context, so omitting one is a
        visible decision at the call site rather than an absence in a dict.

        The dialect is selected ONCE, here, from ``protocol``. A caller passes
        values and cannot choose a builder.
        """
        payload: Task | TaskStatusUpdateEvent | McpWebhookPayload
        if protocol == "a2a":
            payload = create_a2a_webhook_payload(
                task_id=task.task_id,
                status=status,
                result=result,
                context_id=context_id,
            )
        else:
            payload = create_mcp_webhook_payload(
                task_id=task.task_id,
                status=status,
                task_type=validate_webhook_task_type(task.task_type or ""),
                result=result,
            )

        return await self.send_notification(
            push_notification_config=push_notification_config,
            payload=payload,
            task=task,
        )

    async def send_notification(
        self,
        push_notification_config: DeliverableWebhookTarget,
        payload: Task | TaskStatusUpdateEvent | McpWebhookPayload,
        task: WebhookTaskContext,
    ) -> bool:
        """
        Send a protocol-level push notification to the configured webhook.

        Args:
            push_notification_config: Push notification configuration from protocol layer
            payload: For A2A it can be Task or TaskStatusUpdateEvent types for MCP it wil be McpWebhookPayload.
                Use create_a2a_webhook_payload or create_mcp_webhook_payload from adcp's official python client to get the payload for particular task and status
            task: The delivery's task identity, typed. Threaded through to the
                logger unchanged -- it used to be flattened to a loose dict here
                and rebuilt from the PAYLOAD downstream, which silently reset
                sequence_number to 1 and notification_type to None on every row
                the payload did not happen to carry them in.

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not push_notification_config or not push_notification_config.url:
            # TODO: @yusuf - Double check logging actually works for Task, TaskStatusUpdateEvent and McpWebhookPayload types
            logger.debug(
                f"No webhook URL configured in the push notification. Here's payload: {payload}, skipping notification"
            )
            return False

        # The buyer's URL is delivered verbatim: the egress seam (asend) is the only
        # place allowed to decide anything about the destination. Test stacks that
        # need a reachable callback register a reachable hostname instead — the e2e
        # stack runs a long-lived webhook-capture service behind the shared TLS front
        # (see tests/e2e/webhook_capture_service.py).
        #
        # No separate send-time SSRF gate here (#1697 added one in front of the old
        # requests.Session POST): the seam's pre-connection check IS that gate and
        # strictly more — same HTTPS requirement and same reserved/private-address
        # refusal over a real DNS resolution, but it then PINS the connection to the
        # address it validated, so the resolve-then-connect rebinding window a
        # separate validator leaves open does not exist. Re-validating here would be
        # a second copy of address policy, which is what deleting the hand-rolled
        # validator (formerly src/core/security/url_validator.py, deleted; the shared
        # predicate now lives in src/core/security/egress/policy.py) was for.
        url = push_notification_config.url

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

        # No authentication decision here. The seam validates the stored pair against
        # the pinned type and applies whatever that scheme requires — the same
        # decision, made the same way, for every sender. This function used to
        # resolve it, project it into a secret-or-header and call
        # prepare_signed_request itself, which is how it became the only sender that
        # silently dropped a stored Basic row.
        # Send notification with retry logic and logging
        return await self._send_with_retry_and_logging(
            url=url,
            payload=payload_dict,
            headers=headers,
            task=task,
            scheme=push_notification_config.authentication_type,
            credentials=push_notification_config.authentication_token,
        )

    def _conclude(
        self,
        *,
        ctx: WebhookTaskContext,
        log_id: str,
        url: str,
        outcome: WebhookDeliveryOutcome,
        start_time: float,
        audit_logger: Any,
    ) -> bool:
        """Book one delivery: the row, the audit entry, and the bool the caller gets.

        THE single conclusion for this sender. Every arm — refused destination,
        client error, exhausted retries, an unexpected exception, and success —
        ends here, because a refusal, a failure and a delivery differ only in
        what they KNOW (attempts, status, wording), not in what they must record.
        An arm that concludes on its own is an arm that can be written without
        recording anything, which for a refusal means a misconfigured destination
        leaving no trace at all — the absence lane #1802 closes.

        The outcome IS the conclusion: the returned bool is derived from it, not
        decided here, and the row is written from it rather than from arguments
        each arm re-derived.
        """
        response_time_ms = int((time.time() - start_time) * 1000)

        # Persistence is observability; it does not get a vote on delivery. A DB
        # error must not propagate out of a function contracted ``-> bool`` and
        # turn a webhook that WAS delivered into a failure (and, upstream, into a
        # retry). The swallow used to live at the write helper; it lives at the
        # one conclusion now, which is the only place it is needed.
        # Gated on the ELIGIBILITY property, not merely on tenant_id: record_outcome
        # is a no-op for an ineligible ctx, so gating any wider would check out a
        # session and commit an empty transaction for every task status update this
        # sender fires — a per-webhook round trip that did not exist before.
        if ctx.records_delivery_log:
            # records_delivery_log already requires tenant_id truthy; the assert
            # only narrows mypy's view from str | None to str and can never fire.
            assert ctx.tenant_id
            try:
                with get_db_session() as session:
                    record_conclusion(
                        session,
                        tenant_id=ctx.tenant_id,
                        ctx=ctx,
                        log_id=log_id,
                        webhook_url=url,
                        outcome=outcome,
                        response_time_ms=response_time_ms,
                    )
            except Exception as e:
                logger.error(f"Failed to write webhook delivery log: {e}")

        if audit_logger:
            if outcome.kind == "delivered":
                audit_logger.log_success(
                    f"{ctx.task_type} webhook delivered successfully (sequence #{ctx.sequence_number}, "
                    f"{response_time_ms}ms, {outcome.payload_size_bytes or 0} bytes)"
                )
            else:
                audit_logger.log_warning(
                    f"{ctx.task_type} webhook failed for task {ctx.task_id}: {outcome.detail or outcome.kind}"
                )

        return outcome.kind == "delivered"

    async def _send_with_retry_and_logging(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict,
        task: WebhookTaskContext,
        scheme: str | None = None,
        credentials: str | None = None,
        max_attempts: int = 3,
    ) -> bool:
        """Deliver one webhook through the egress seam, with logging and audit trail.

        ``body_bytes`` are the exact bytes ``prepare_signed_request`` produced —
        signed over them when the destination is signed, always the sole
        serialization of ``payload`` otherwise. They go on the wire unchanged;
        this function does not call ``json.dumps`` on ``payload`` itself, so
        there is no second serialization that could disagree with the first.
        """
        # The caller's typed context, used as given. It used to be rebuilt here
        # from a four-key dict plus the payload, and the rebuild was lossy in both
        # directions that mattered: as_metadata never emitted sequence_number or
        # notification_type, and from_metadata recovered them from the PAYLOAD's
        # result -- so a payload that did not carry them yielded 1 and None, and
        # those were the values PERSISTED to webhook_delivery_log. A buyer reading
        # the log saw a webhook claiming to be first in its sequence and carrying
        # no notification type, when the server had sent the seventh and marked it
        # final.
        ctx = task

        # Create webhook delivery log entry
        log_id = str(uuid4())
        start_time = time.time()

        # Log to audit system (start)
        audit_logger = None
        if ctx.tenant_id:
            audit_logger = get_audit_logger("webhook", ctx.tenant_id)
            audit_logger.log_info(
                f"Sending {ctx.task_type} webhook for task {ctx.task_id} (sequence #{ctx.sequence_number})"
            )

        # One call through the egress seam. It owns address and TLS policy, the
        # refusal to follow redirects, the retry schedule and which statuses are
        # worth another attempt — and it builds a transport pinned to THIS
        # destination, which is why no client or session may outlive the call.
        #
        # The seam's redirect refusal is what #1697 reached for with
        # ``allow_redirects=False``: httpx defaults to ``follow_redirects=False``
        # and the seam never overrides it, so a 302 toward metadata or a private
        # address cannot carry us past the validated destination.
        #
        # No ``field=``: the URL is read back out of a stored PushNotificationConfig,
        # not off a request document a buyer just sent — the buyer-actionable
        # refusal already happened at ingest (src/core/webhook_validator.py, reject_unsafe_webhook_registration_url).
        #
        # The URL is logged sanitized (scheme://host/path): a buyer's webhook URL
        # may carry credentials in userinfo or a token in the query string, and a
        # log line is the one place they would sit in cleartext (#1697).
        logger.info("Sending webhook for task %s to %s", ctx.task_id, webhook_url_for_log(url))
        try:
            outcome = await adeliver_webhook(
                url,
                payload,
                scheme=scheme,
                credentials=credentials,
                headers=headers,
                timeout=10.0,
                max_attempts=max_attempts,
            )
        except Exception as e:
            # Deliberately kept. The seam maps its OWN failure taxonomy onto the
            # outcome, but relying on that alone would let anything else escape a
            # function contracted ``-> bool``, and the delivery scheduler re-raises
            # what it catches. The pinned transport's own wrong-host guard raises a
            # bare RuntimeError, which belongs here.
            logger.error(f"Unexpected error sending webhook for task {ctx.task_id}: {e}", exc_info=True)
            # Nothing reached the wire, and no outcome kind covers a NON-transport
            # failure — so this arm builds the one it means: exhausted with zero
            # attempts. The arm no longer decides what gets recorded; it only says
            # what became of the delivery, and the epilogue books it.
            return self._conclude(
                ctx=ctx,
                log_id=log_id,
                url=url,
                outcome=WebhookDeliveryOutcome.unexpected(type(e).__name__),
                start_time=start_time,
                audit_logger=audit_logger,
            )

        if outcome.kind == "refused_auth":
            # FAIL-CLOSED. This used to fall through to an unsigned delivery: the
            # buyer asked for authentication and received none, with no error on any
            # surface. log-and-return, and NO delivery-log row and no audit entry —
            # nothing was attempted, so a row claiming an attempt would misreport a
            # refusal as a delivery that failed on the wire. The refusal a buyer can
            # act on already happened at ingest.
            #
            # It still concludes through the epilogue, so this arm cannot be the one
            # that forgets to. Both absences survive the move and are the RULING,
            # not an oversight: record_outcome maps no status for ``refused_auth``
            # (so no row), and _conclude is passed no audit_logger (so no entry).
            logger.error(
                "Refusing to send webhook for task %s to %s: %s",
                ctx.task_id,
                webhook_url_for_log(url),
                outcome.detail or outcome.reason,
            )
            return self._conclude(
                ctx=ctx,
                log_id=log_id,
                url=url,
                outcome=outcome,
                start_time=start_time,
                audit_logger=None,
            )

        if outcome.kind == "refused_destination":
            # Refused before a connection was opened. It still writes a row and an
            # audit entry — a misconfigured destination that leaves no trace is
            # indistinguishable from one nobody configured. The honest attempt count
            # (0) and the ``refused`` spelling are the recorder's, not this arm's.
            # Severity carried on the outcome, not chosen here (#1802).
            logger.log(outcome.log_level, f"Webhook for task {ctx.task_id} was refused by egress policy")
        elif outcome.kind != "delivered":
            logger.error(
                f"Webhook for task {ctx.task_id} {outcome.detail or f'failed after {outcome.attempts} attempts'}"
            )
        else:
            logger.info(f"Successfully sent webhook for task {ctx.task_id} (status: {outcome.http_status})")

        return self._conclude(
            ctx=ctx,
            log_id=log_id,
            url=url,
            outcome=outcome,
            start_time=start_time,
            audit_logger=audit_logger,
        )


# Global service instance
_webhook_service: ProtocolWebhookService | None = None


def get_protocol_webhook_service() -> ProtocolWebhookService:
    """Get or create global webhook service instance.

    The service owns no connection state, so there is nothing to close and no
    shutdown callback to register. Each delivery builds a transport pinned to its
    own destination and discards it: a pooled client shared across destinations
    would resolve once and then serve a hostname it was never validated for,
    which is the whole reason the pin exists.
    """
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = ProtocolWebhookService()
    return _webhook_service


def get_webhook_service_or_none() -> ProtocolWebhookService | None:
    """Return the current singleton instance, or None if never constructed.

    Distinct from :func:`get_protocol_webhook_service`: this does NOT trigger
    construction. Use it from shutdown hooks where you only want to close an
    *existing* instance, not create one just to inspect it.

    Resolving the singleton through this function call is location-independent:
    it reads the live module global at call time, so callers may import it at
    module top-level without the lazy-import tripwire that a direct
    ``from ... import _webhook_service`` would introduce (a hoisted private
    import binds the initial ``None`` forever).
    """
    return _webhook_service

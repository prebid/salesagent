"""Sender for AdCP task-status webhooks.

Every notification this service delivers is one ``mcp-webhook-payload`` envelope,
because every registration it delivers against arrived through the AdCP channel --
``push_notification_config`` in the task arguments, or ``reporting_webhook`` on
``create_media_buy``. AdCP 3.1.1 ``L3/webhooks.mdx`` § "Registration channel
determines envelope shape" (:308) makes that the rule: the envelope follows the
registration mechanism, and the page names keying on the sync transport as the
model it is NOT ("Why this is the model, not 'match inbound transport'", :328).
The A2A-native channel that would ask for a ``Task`` -- ``TaskPushNotificationConfig``
-- is not implemented here at all.
"""

import logging
import time
from typing import Any, Protocol
from uuid import uuid4

from adcp.types import McpWebhookPayload
from adcp.webhooks import GeneratedTaskStatus
from pydantic import BaseModel as PydanticBaseModel

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.security.webhook_egress import adeliver_webhook
from src.core.signing.outbound import WebhookAuthConfig, delivery_signer_for_tenant
from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.delivery import WebhookDeliveryOutcome, WebhookTaskContext, build_webhook_envelope
from src.services.webhook_conclusion import record_conclusion


class DeliverableWebhookTarget(WebhookAuthConfig, Protocol):
    """What this sender actually needs off a push-notification config: five fields.

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

    The three AUTHENTICATION-bearing fields (``url``, ``authentication_type``,
    ``authentication_token``) are not restated here: they ARE
    :class:`~src.core.signing.outbound.WebhookAuthConfig`, the shape the signing
    boundary reads off a registration, so this name is a local alias for that
    contract rather than a second copy of it. Restating them would put the same
    document in two modules, and a value satisfying one but not the other would
    then be constructible.
    """

    #: The two values core/push-notification-config.json obliges the seller to echo
    #: verbatim into every payload built against this registration.
    @property
    def operation_id(self) -> str | None: ...

    @property
    def token(self) -> str | None: ...


logger = logging.getLogger(__name__)


class ProtocolWebhookService:
    """
    Service for sending protocol-level push notifications to clients.

    How a delivery is authenticated is NOT decided here. The receiver's stored
    ``PushNotificationConfig`` selects exactly one of three modes, and the
    selection is made by ``src.core.security.webhook_egress``'s one three-arm
    match: legacy HMAC-SHA256, Bearer, or — selected by the ABSENCE of an
    ``authentication`` block, which is the pinned schema's own selector for the
    profile (AdCP 3.1.1 ``L1/security.mdx`` :1424) — RFC 9421 request signing.
    :1425 forbids answering a legacy registration with a 9421 signature, so the
    arms are exclusive by construction rather than by a rule kept here. The key
    that arm signs with comes from the one signing seam, reached through
    ``delivery_signer_for_tenant``. This service owns delivery logging and the
    audit trail, and nothing else.
    """

    async def notify(
        self,
        push_notification_config: DeliverableWebhookTarget,
        *,
        task: WebhookTaskContext,
        status: GeneratedTaskStatus,
        result: PydanticBaseModel | dict[str, Any],
    ) -> bool:
        """Deliver one notification from VALUES; the envelope is built here.

        THE delivery entry point. Every sender used to build its own payload and
        its own free-form ``metadata`` dict at its own call site, which is how
        ``delivery_webhook_scheduler`` came to emit a different shape from the
        admin routes.

        Taking a typed :class:`WebhookTaskContext` instead of ``metadata:
        dict[str, Any]`` is what closes the other half. ``records_delivery_log``
        needs ``tenant_id`` and ``principal_id``; the admin sender passed
        ``{"task_type": ...}`` alone, so admin-originated deliveries wrote no
        ``webhook_delivery_log`` row and said nothing about it. A caller now has
        to name those fields to construct the context, so omitting one is a
        visible decision at the call site rather than an absence in a dict.

        There is no dialect to pick. A caller passes values; every buyer receives
        the same envelope, built where both senders build it.
        """
        payload = build_webhook_envelope(
            task=task,
            status=status,
            result=result,
            operation_id=push_notification_config.operation_id,
            token=push_notification_config.token,
        )

        return await self.send_notification(
            push_notification_config=push_notification_config,
            payload=payload,
            task=task,
        )

    async def send_notification(
        self,
        push_notification_config: DeliverableWebhookTarget,
        payload: McpWebhookPayload,
        task: WebhookTaskContext,
    ) -> bool:
        """
        Send a protocol-level push notification to the configured webhook.

        Args:
            push_notification_config: Push notification configuration from protocol layer
            payload: The AdCP webhook envelope, from ``create_mcp_webhook_payload``.
            task: The delivery's task identity, typed. Threaded through to the
                logger unchanged -- it used to be flattened to a loose dict here
                and rebuilt from the PAYLOAD downstream, which silently reset
                sequence_number to 1 and notification_type to None on every row
                the payload did not happen to carry them in.

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not push_notification_config or not push_notification_config.url:
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

        # Content-Type is the sender's (it frames the body it serialized) and is
        # load-bearing rather than decoration now that the seam transmits ``content=``
        # bytes: httpx sets no Content-Type of its own on that path, while the 9421
        # webhook profile COVERS the ``content-type`` component — so it ships here or
        # the signature covers a header that never left, which a conformant verifier
        # rejects with ``webhook_signature_components_incomplete`` (security.mdx @
        # v3.1.1 :1476). ``application/json`` is the one spelling the 9421 arm accepts;
        # the seam refuses any other value loudly rather than signing a lie. Auth
        # headers are the boundary's and never appear here.
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

        # Serialize once, at the delivery boundary, for HMAC signing and the JSON
        # send. ``exclude_none`` keeps the envelope's optional fields off the wire
        # rather than sending them as explicit nulls.
        payload_dict: dict[str, Any] = payload.model_dump(mode="json", exclude_none=True)

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

        THE single conclusion for this sender. Every branch — refused destination,
        client error, exhausted retries, an unexpected exception, and success —
        ends here, because a refusal, a failure and a delivery differ only in
        what they KNOW (attempts, status, wording), not in what they must record.
        An branch that concludes on its own is an branch that can be written without
        recording anything, which for a refusal means a misconfigured destination
        leaving no trace at all — the absence lane salesagent-gra7.1 closes.

        The outcome IS the conclusion: the returned bool is derived from it, not
        decided here, and the row is written from it rather than from arguments
        each branch re-derived.
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

        One body, many SIGNATURES: the RFC 9421 arm signs those same bytes afresh
        on every attempt, because a ``nonce`` a conformant receiver must reject on
        replay makes a signature computed once above a retry loop invalid on
        attempt two. That is why the seam takes a ``sign=`` callback rather than a
        finished header map, and why the payload's ``idempotency_key`` — minted
        once by :func:`~src.core.webhooks.delivery.build_webhook_envelope` and
        therefore constant across those attempts — is what the receiver dedups on.
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
                # Resolved as an ARGUMENT, so the signing session opens, is read and
                # CLOSES before ``adeliver_webhook`` is entered and any socket exists
                # (#1757) — the rule a pooled connection held across a POST to a
                # buyer-supplied URL would break. WHICH key is not this sender's
                # decision: ``delivery_signer_for_tenant`` is the one open-read-close
                # all three webhook senders share, so two transports cannot sign one
                # tenant's deliveries with two different keys.
                #
                # Passed UNCONDITIONALLY, and that is not a dropped decision. A stored
                # row does not tell its reader which arm it selects, so the seam's one
                # ``_headers_for`` match decides: with an ``authentication`` block
                # present the block selects a legacy arm and the signer is IGNORED
                # (security.mdx @ v3.1.1 :1424, :1425); with the block absent the
                # signer signs. Re-deriving that here would put arm selection in a
                # second place.
                #
                # No silent downgrade. ``None`` is not a failure and never a fallback:
                # it is the decided posture of a tenant whose advertised
                # ``webhook_signing.supported`` is already ``false``, so its receivers
                # have been told not to expect a ``Signature`` header. A tenant that
                # CAN sign but whose material cannot be honestly used RAISES out of the
                # helper — inside this ``try`` on purpose, so the raise lands on the
                # handler below and is BOOKED as an unexpected outcome with zero
                # attempts, which is the literal truth: nothing was serialized and
                # nothing was dialled.
                signer=delivery_signer_for_tenant(ctx.tenant_id),
            )
        except Exception as e:
            # Deliberately kept, and it carries more weight after the signing rewire.
            # The seam maps its OWN failure taxonomy onto the outcome, but relying on
            # that alone would let anything else escape a function contracted
            # ``-> bool``, and the delivery scheduler re-raises what it catches. The
            # pinned transport's own wrong-host guard raises a bare RuntimeError, and
            # an unresolvable-but-declared signing posture raises out of
            # ``delivery_signer_for_tenant`` before the seam is called at all — both
            # belong here.
            logger.error(f"Unexpected error sending webhook for task {ctx.task_id}: {e}", exc_info=True)
            # Nothing reached the wire, and no outcome kind covers a NON-transport
            # failure — so this branch builds the one it means: exhausted with zero
            # attempts. The branch no longer decides what gets recorded; it only says
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
            # It still concludes through the epilogue, so this branch cannot be the one
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
            # (0) and the ``refused`` spelling are the recorder's, not this branch's.
            # Severity carried on the outcome, not chosen here (salesagent-pldmk.39).
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

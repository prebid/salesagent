"""Proof-of-control challenge for account-level notification subscribers (#1592 T2).

AdCP requires a seller to verify endpoint control BEFORE persisting or exposing a
notification subscriber as ``active: true``. This service performs that check.

## Why this runs in the request cycle (a narrow, explicitly-granted exception)

The project's architecture direction is that outbound I/O does NOT belong in the
HTTP request cycle: accept, validate against Postgres, return pending, let a worker
do the network call. This service is a deliberate exception, granted on
2026-07-27 as the #1592 T2 carve-out.

The reason it is an exception rather than a precedent: the spec forbids the async
shape here. There is no per-subscriber pending state -- ``active`` is a boolean and
the per-entry action enum is created/updated/unchanged/failed -- and the response
echoes state *after* activation-proof checks. So echoing ``active: true`` before the
proof violates a MUST, echoing ``active: false`` misstates the applied state, and a
background flip behind a synchronous "updated" is non-conformant. If the response is
synchronous, the proof outcome must be in it.

Containment, so this stays one bounded handshake and not the beginning of adapter
I/O in the request path:
  - ONE POST, no retries;
  - a hard per-challenge timeout, and a per-request budget in the caller;
  - fired ONLY for an entry declaring ``active: true`` whose proof tuple is not
    already persisted (the spec's proof-reuse allowance);
  - fail-closed: anything other than a clear success is "not proven".

## The challenge IS signed, and what that does and does not claim

#1291 C2. The challenge carries an RFC 9421 signature made with this tenant's
``adcp_use: "request-signing"`` key under the WEBHOOK profile tag
(``adcp/webhook-signing/v1``), over the exact bytes POSTed — or it is not sent.
``sync_accounts.mdx`` @ v3.1.1 :207 requires exactly that, "even when the candidate
config selects legacy delivery auth", and requires the receiver to verify the
signature and to reject the challenge unless ``seller_agent_url``, ``delivery_auth``
and ``event_types`` match the pending registration. So the body is the schema's
seven-field ``webhook.challenge`` document, and proof is the receiver ECHOING the
single-use value (:223-235) — not merely answering 2xx, which any endpoint that
accepts POSTs does, including one an attacker pointed at us.

Domain separation between requests and webhooks is carried by the signature ``tag``,
not by the key purpose (security.mdx @ v3.1.1 :1426), which is why a
``request-signing`` key under the webhook tag is the conformant pairing and why no
second key purpose exists for this. :1438 deprecates ``adcp_use:
"webhook-signing"``; a webhook verifier that is handed the wrong purpose rejects with
``webhook_signature_key_purpose_invalid``.

What this does NOT claim: nothing here decides what ``get_adcp_capabilities``
advertises. ``webhook_signing.supported`` is derived from key material and trust-root
publishability (#1291 D1) and is unaffected by this service.

**No 3.1.1 conformance storyboard grades this surface.**
``notification-config-lifecycle.yaml`` puts active registration explicitly out of
scope, so the obligation is prose plus schema, and
``tests/integration/test_notification_proof_challenge.py`` is the only grader.
"""

import logging
from typing import TYPE_CHECKING, NamedTuple

from src.core.schemas.notification import NotificationConfig
from src.core.security.egress.policy import OutboundRequestBlocked, is_reserved_tld_host
from src.core.security.outbound_http import CounterpartyUrl, validate_url

if TYPE_CHECKING:
    from adcp.types.generated_poc.core.webhook_challenge import WebhookChallenge
    from adcp.webhook_auth import JwkSignerStrategy

    from src.core.signing.outbound import ChallengeAnswer

logger = logging.getLogger(__name__)

#: Hard ceiling for a single challenge. Deliberately small: this runs inside the
#: request cycle, so the buyer's latency budget is the constraint, not the
#: endpoint's convenience.
#:
#: Applied EXPLICITLY at the send rather than inherited: the shared outbound webhook
#: boundary bakes in 10.0s, which is right for a background delivery and wrong here.
#: The spec SHOULDs 10s too — this deployment is stricter because the in-request-cycle
#: carve-out is what makes the whole handshake permissible at all.
CHALLENGE_TIMEOUT_SECONDS = 2.0


class ChallengeSigning(NamedTuple):
    """What a challenge needs that only a DB session can produce.

    Resolved by the caller inside one short transaction that is CLOSED before any socket
    opens, and passed in — so this service opens no session of its own and holds no
    transaction across a network round trip.
    """

    #: The RFC 9421 strategy from the shared outbound signing boundary. Its type is the
    #: oracle: only a JWK signer can be handed here. Spelled concretely rather than as
    #: ``Any`` because ``src.core.tools.accounts`` -- which builds this tuple -- is scoped
    #: ``disallow_any_explicit`` (#1721 M3), so an ``Any`` here surfaces at that caller.
    strategy: "JwkSignerStrategy"
    #: The PUBLISHED ``agents[].url`` for the transport this registration arrived on. A
    #: receiver matches it byte-for-byte against our brand.json, so it is derived, never
    #: composed here.
    seller_agent_url: str


class NotificationProofService:
    """Performs the proof-of-control challenge for a notification subscriber."""

    async def prove(
        self,
        account_id: str,
        config: NotificationConfig,
        *,
        signing: ChallengeSigning | None = None,
    ) -> bool:
        """Whether endpoint control over ``config.url`` is proven. Fail-closed.

        Returns False -- never raises -- so a proof failure becomes a per-account
        rejection inside a transport-level success, which is what the spec asks
        for, rather than a transport error.

        ``signing`` is ``None`` when the caller could not produce a signable identity for
        this registration: the tenant holds no usable key, or the registration arrived on a
        transport with no published ``agents[].url``. Either way there is nothing to send
        that a conformant receiver could attribute to us, so no POST is made at all.
        """
        url = str(config.url or "")
        if not url:
            return False

        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""

        # RFC 2606/6761 reserved TLDs can never be proven. Deciding this WITHOUT a
        # DNS lookup is what makes the outcome deterministic: relying on
        # `buyer.example` returning NXDOMAIN would make the result depend on
        # whether the local resolver hijacks unknown names, which many do.
        if is_reserved_tld_host(hostname):
            logger.info(
                "Notification proof for account %s refused: %s is under a reserved TLD and can never be proven",
                account_id,
                hostname,
            )
            return False

        if signing is None:
            # Refused BEFORE the SSRF check so the log names the real cause: an
            # unsigned challenge is worthless whatever the destination resolves to.
            logger.info(
                "Notification proof for account %s refused: this challenge cannot be RFC 9421-signed, "
                "and an unsigned challenge is one no conformant receiver can attribute to us "
                "(sync_accounts.mdx @ v3.1.1 :207)",
                account_id,
            )
            return False

        if _has_uncredentialed_legacy_auth(config):
            # `Authentication.credentials` is OPTIONAL in the SDK type, so
            # `{"schemes": ["Bearer"]}` with no credential is a legal registration (C1 models
            # it as LEGACY_UNCREDENTIALED). But `webhook-challenge.json` REQUIRES
            # `delivery_auth.credential_fingerprint` when `mode` is Bearer/HMAC-SHA256 and
            # FORBIDS it for rfc9421, so NO conformant challenge document exists for that
            # registration — there is no fingerprint to compute and no legal mode to report.
            # Pydantic does not enforce a conditional required, so without this branch the
            # body would serialize as `{"mode": "Bearer"}` and the receiver would reject it:
            # the entry would fail by ACCIDENT rather than by design, and the log would blame
            # the network.
            logger.info(
                "Notification proof for account %s refused: the registration declares a legacy "
                "authentication scheme with no credential, and webhook-challenge.json requires a "
                "credential_fingerprint for a legacy delivery_auth mode — no conformant challenge "
                "can be built for it. Supply the credential, or register without an authentication "
                "block to use RFC 9421 delivery",
                account_id,
            )
            return False

        # Destination policy at FIRE time (not write time): registration already took the
        # DNS-free verdict, and this is the moment something is about to dial, so where
        # the name resolves NOW is what matters.
        #
        # ONE address policy, the egress seam's. This site used to call
        # ``url_validator.check_url_ssrf`` — the fourth copy of that policy, which
        # GH #1802 deleted. ``validate_url`` reaches the SAME
        # ``EgressPolicy.resolve_for_dial`` verdict ``send``/``asend`` reach and opens no
        # socket, which is exactly what this site needs; https is unconditional there
        # (GH #1757), so the old ``require_https=True`` is implicit, not dropped. Nothing
        # is re-implemented locally: a second spelling of "where does this land" is the
        # defect #1802 makes structurally impossible.
        #
        # The refusal is deliberately OPAQUE. ``resolve_for_dial`` logs its own cause at
        # WARNING and raises WITHOUT one, because echoing a fetch error back to the party
        # that supplied the URL is what AdCP 3.1.1 ``security.mdx`` point 6 forbids — so
        # nothing is interpolated below. The outcome for the caller is unchanged: an
        # explicit, logged refusal that returns False.
        #
        # ``CounterpartyUrl`` marks BUYER provenance. No field: this service is handed a
        # NotificationConfig, never the request document or the entry's index, so there is
        # no honest ``notification_configs[i].url`` locator to name here (a fabricated one
        # is worse than none) — and the refusal is swallowed into False rather than an
        # envelope anyway.
        #
        # This stays a PRE-FLIGHT even though ``send_signed_challenge`` now dials through
        # ``asend`` (GH #1890, which resolves once and pins that address for the socket) and
        # would reach the same verdict a moment later. Two reasons it is not redundant: the
        # refusal is logged HERE with the account it belongs to, where the seam's raise would
        # otherwise arrive as an anonymous entry in the broad handler below; and it lands
        # BEFORE a single-use challenge value is minted and signed, so a destination we were
        # never going to dial does not consume one. The seam remains the only address
        # AUTHORITY -- ``validate_url`` is the same ``EgressPolicy.resolve_for_dial`` call,
        # not a second policy -- so the double resolution costs a lookup and can reach no
        # answer the dial would disagree with.
        try:
            validate_url(url, provenance=CounterpartyUrl(field=None))
        except OutboundRequestBlocked:
            logger.info(
                "Notification proof for account %s refused: the egress seam refused this destination "
                "before any connection was attempted (the cause is logged by the seam, not echoed "
                "here — AdCP 3.1.1 security.mdx point 6)",
                account_id,
            )
            return False

        try:
            from src.core.signing.outbound import send_signed_challenge

            challenge, body = _build_challenge(account_id, config, signing.seller_agent_url)
            # The sign and the POST are the boundary's, so this service holds no raw POST at
            # all — see `send_signed_challenge`. The 2.0s ceiling is passed EXPLICITLY
            # because the boundary's own default is 10.0s, which is right for a background
            # delivery and wrong inside the request cycle.
            response = await send_signed_challenge(
                url=url,
                body=body,
                strategy=signing.strategy,
                timeout_seconds=CHALLENGE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            # Any failure -- a timeout, TLS, connection refused, a revoked key, an
            # algorithm the posture would not declare -- is "not proven". Broad by intent:
            # the question is binary and the safe answer to every unexpected condition is
            # False. It must never escape, or one unprovable subscriber would fail the
            # whole sync_accounts request instead of its own entry.
            logger.info("Notification proof for account %s failed for %s: %s", account_id, url, exc)
            return False

        return _response_proves_control(account_id, url, response, challenge.challenge)


def _has_uncredentialed_legacy_auth(config: NotificationConfig) -> bool:
    """Whether the candidate declares a legacy scheme but supplies no credential.

    Split out so the refusal reads as a named condition at the call site rather than as an
    inline shape test, and so it derives from the ONE shared auth pluck rather than a second
    reading of ``authentication``.
    """
    from src.core.signing.outbound import declared_auth

    auth = declared_auth(config.authentication)
    return auth.scheme is not None and auth.credential is None


def _build_challenge(
    account_id: str, config: NotificationConfig, seller_agent_url: str
) -> tuple["WebhookChallenge", bytes]:
    """The seven-field ``webhook.challenge`` document, and the exact bytes to sign and send.

    Returns both because the two are not interchangeable here: the MODEL is the validator and
    the BYTES are the wire, and one field deliberately differs between them — see the
    ``seller_agent_url`` note below. Serialized once, so the bytes signed are the bytes sent.

    Built from the SDK's own generated type (CLAUDE.md Pattern #1) rather than a dict:
    ``webhook-challenge.json`` requires seven properties and is
    ``additionalProperties: false``, so a hand-rolled body drifts from the schema
    silently while a typed one fails loudly at construction.

    The SDK's imperative challenge HELPERS are deliberately not used. All three
    (``create_webhook_challenge_payload``, ``challenge_webhook_destination``,
    ``WebhookSender.send_webhook_challenge``) emit only FOUR of the seven required fields
    and accept no arguments for ``seller_agent_url``, ``delivery_auth`` or
    ``event_types`` — the three the receiver MUST match against its pending registration
    (:207). They are structurally unable to produce a conformant body, so signing through
    them would yield a valid signature over a document every conformant buyer rejects.
    Filed upstream; the SDK's TYPES already agree with the spec, only its helpers lag.

    ``seller_agent_url`` is emitted from the PUBLISHED string rather than from the model's
    serializer, and that is not a nicety. The field is typed ``AnyUrl``, whose serializer
    LOWERCASES the host: a tenant whose ``virtual_host`` carries any uppercase would emit
    ``https://seller.example.com/mcp/`` while brand.json publishes
    ``https://Seller.Example.com/mcp/``. A receiver matches this value BYTE-FOR-BYTE against
    our published ``agents[].url`` (``security.mdx`` @ v3.1.1 :1104 step 5, "no
    canonicalization at this step"), so the lowercased form fails resolution at a strict
    receiver — silently, because both strings look right to a human. The model still
    VALIDATES the value; only the emitted bytes carry the published spelling.
    """
    import json

    from adcp.types.generated_poc.core.webhook_challenge import DeliveryAuth, WebhookChallenge
    from adcp.webhooks import generate_webhook_challenge_value

    from src.core.signing.outbound import credential_fingerprint, declared_auth, delivery_auth_mode

    auth = declared_auth(config.authentication)
    challenge = WebhookChallenge(
        challenge=generate_webhook_challenge_value(),
        account_id=account_id,
        subscriber_id=str(config.subscriber_id or ""),
        seller_agent_url=seller_agent_url,
        # The CANDIDATE's future delivery mode, as DATA. It never selects how this
        # challenge is signed -- :207 requires the webhook profile key either way.
        delivery_auth=DeliveryAuth(
            mode=delivery_auth_mode(auth),
            credential_fingerprint=credential_fingerprint(auth),
        ),
        event_types=list(config.event_types or []),
    )

    payload = challenge.model_dump(mode="json", exclude_none=True)
    payload["seller_agent_url"] = seller_agent_url
    return challenge, json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _response_proves_control(account_id: str, url: str, response: "ChallengeAnswer", challenge: str) -> bool:
    """Whether *response* proves control: 2xx AND an echo of the single-use value.

    The rule this replaces -- "any 2xx is proof" -- is satisfied by every endpoint that
    accepts a POST, so it proved only reachability. Only the echo binds the answer to the
    value WE generated for THIS registration (:223-235); a non-2xx, malformed, mismatched
    or timed-out challenge all mean proof FAILED.

    The echo check is the SDK's own ``validate_webhook_challenge_response``, with one
    recorded divergence: it accepts a body carrying BOTH ``challenge`` and ``token``,
    which ``webhook-challenge-response.json`` forbids (``not: {required: [challenge,
    token]}``). Harmless for a seller — we only need to know whether the value came back —
    and worth naming rather than silently relying on.
    """
    from adcp.webhooks import WebhookChallengeError, validate_webhook_challenge_response

    if not 200 <= response.status_code < 300:
        logger.info(
            "Notification proof for account %s failed for %s: status %s",
            account_id,
            url,
            response.status_code,
        )
        return False

    try:
        validate_webhook_challenge_response(response.content, challenge=challenge)
    except WebhookChallengeError as exc:
        logger.info(
            "Notification proof for account %s failed for %s: the endpoint answered %s but did not echo "
            "the challenge value (%s), so it proved reachability rather than control",
            account_id,
            url,
            response.status_code,
            getattr(exc, "reason", exc),
        )
        return False
    return True


_service: NotificationProofService | None = None


def get_notification_proof_service() -> NotificationProofService:
    """Module-level singleton -- and the ONE injection seam tests patch.

    Mirrors ``get_protocol_webhook_service``. Tests override this getter rather
    than the class, so production always holds a real prover: a test-scoped
    auto-pass prover would persist ``active: true`` without proof, which is the
    exact violation this service exists to prevent.
    """
    global _service
    if _service is None:
        _service = NotificationProofService()
    return _service

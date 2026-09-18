"""WHICH KEY signs a tenant's outbound traffic (#1291 C1/C2).

``salesagent-z6nr.18``. Core Invariant: *every outbound AdCP webhook is authenticated
by exactly ONE mode, selected at ONE seam from the receiver's own registration, and
the bytes signed are the bytes sent.*

This module does NOT open sockets for deliveries and does not choose the arm. Both of
those belong to :mod:`src.core.security.webhook_egress`: :func:`~src.core.security.
webhook_egress._headers_for` is the ONE match that reads a validated
``PushAuthentication`` block and returns exactly one of (HMAC credential, Bearer header,
per-attempt RFC 9421 signer), and its two twins ``deliver_webhook`` / ``adeliver_webhook``
own the socket through :mod:`src.core.security.outbound_http`. "Signed both ways"
(security.mdx @ v3.1.1 :1425) is therefore a shape that cannot be expressed rather than
a rule three senders must remember.

What this module decides, and nothing else:

* **which key a tenant signs with** — :func:`webhook_delivery_signer` for deliveries and
  :func:`adcp_challenge_signer` for a proof-of-control challenge, both off the same two
  calls (``webhook_signing_posture`` + ``resolve_signing_material``) so a challenge and
  the deliveries that follow it cannot select different keys.
* **on whose session that key is read** — :func:`signing_repo`, which owns its session
  and accepts none, so no pooled connection can be parked across a buyer's latency
  (#1757).
* **what a challenge reports about the receiver's own registration** —
  :func:`declared_auth`, :func:`delivery_auth_mode`, :func:`credential_fingerprint`.

The ONE socket opened here is :func:`send_signed_challenge`, and it goes through
:func:`src.core.security.outbound_http.asend`: one resolution pinned to the dialled
address, no redirects, ``trust_env=False``, port allowlist, capped body. Nothing in this
module imports ``httpx``, and nothing here builds an ``adcp.webhooks.WebhookSender`` —
that sender owns its own client, which is not a dialer this deployment sanctions.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, runtime_checkable

from adcp.types import AuthenticationScheme

from src.core.enum_helpers import enum_value
from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.security.outbound_http import CounterpartyUrl, OutboundDeliveryFailed, asend
from src.core.signing.posture import webhook_signing_posture
from src.core.signing.provider import resolve_signing_material

if TYPE_CHECKING:  # pragma: no cover - typing only
    from adcp.webhook_auth import JwkSignerStrategy

    from src.core.database.repositories.signing_key import SigningKeyRepository

logger = logging.getLogger(__name__)

#: (warning id, tenant) pairs already warned about. security.mdx obliges an honest
#: posture, not a log line per webhook: without this the WARNING fires on EVERY delivery
#: for every keyless tenant, which today is all of them.
#:
#: KEYED BY PAIR, NOT BY TENANT. This set replaced two caches guarding two DISTINCT
#: conditions — deliveries going out unsigned, and a proof-of-control challenge that
#: cannot be signed at all. Under a tenant-only key whichever condition fired first would
#: permanently suppress the other, so an operator seeing "delivered unsigned" would never
#: learn that subscriber activation is also going to fail. The pair keeps one suppression
#: domain per warning, and a third warning gets its own for free instead of inheriting a
#: neighbour's.
_WARNED: set[tuple[str, str]] = set()
_warn_lock = threading.Lock()


@runtime_checkable
class WebhookAuthConfig(Protocol):
    """What a sender READS off a receiver registration — three attributes, no more.

    Declared structurally so the boundary names the SHAPE it needs instead of a concrete
    ORM class. Both a ``PushNotificationConfig`` row and the delivery service's frozen
    ``QueuedWebhook`` projection satisfy it, which is what lets the retry loop carry
    primitives instead of a live ORM instance whose lazy-loads need a session that may
    already be closed (#1757, salesagent-n78j0.4).

    Naming the concrete model here instead was a real constraint, not a cosmetic one: it
    made "queue a projection rather than the row" a type error, so the only way to keep
    mypy happy was to keep the ORM object on the queue.
    """

    @property
    def url(self) -> str: ...

    @property
    def authentication_type(self) -> str | None: ...

    @property
    def authentication_token(self) -> str | None: ...


class DeclaredAuth(NamedTuple):
    """A receiver's declared delivery authentication, normalized to ONE shape.

    The same fact is spelled six ways across this codebase — the ORM row
    (``authentication_type``/``authentication_token``), the AdCP request type
    (``authentication.schemes[0]`` + ``credentials``), the A2A protobuf's singular
    ``scheme``, the wire enum (``delivery_auth.mode``), the admin form's
    ``auth_type``/``auth_config``, and the string ``"None"`` sentinel MCP header ingest
    writes. Six spellings of one concept is how they end up disagreeing, and two of them
    already do.

    This is the normalized pair every derivation should start from. It is deliberately the
    INPUT half only: :func:`src.core.security.webhook_egress._headers_for` decides the
    DELIVERY arm from a validated block, :func:`delivery_auth_mode` reports the wire enum,
    and neither reimplements the pluck.
    """

    #: The scheme the receiver named, verbatim (never case-folded — the wire enum is
    #: ``Bearer``/``HMAC-SHA256``, and folding here would lose the spelling we must echo).
    scheme: str | None
    #: The credential it supplied, or ``None`` for a scheme with no usable secret.
    credential: str | None


def declared_auth(authentication: Any) -> DeclaredAuth:
    """Normalize an ``authentication`` block, whichever shape it arrives in.

    Accepts a pydantic model (the AdCP request types), a plain dict (a
    ``step.request_data`` blob or raw transport params) or ``None``, because the four
    wire->row call sites read two of those and the challenge payload reads the third.
    A dual accessor here is the price of ONE derivation; four copies of the same
    three-line pluck — which is what exists today, two of them with a shape guard the
    other two lack — is the alternative, and the drift is already real.

    ``schemes`` is PLURAL: that is the field name on every AdCP type. The singular
    ``scheme`` spelling belongs to the A2A protobuf and is translated at that transport's
    own boundary; reading it here would put protobuf shape in the signing layer.
    """
    if authentication is None:
        return DeclaredAuth(scheme=None, credential=None)

    def _read(field: str) -> Any:
        if isinstance(authentication, Mapping):
            return authentication.get(field)
        return getattr(authentication, field, None)

    schemes = _read("schemes")
    if not isinstance(schemes, (list, tuple)):
        # A bare string here would ITERATE — ``"Bearer"`` yielding ``"B"`` — and the
        # derivation would report a mode nobody declared. The dict path is advertised for
        # callers reading raw params, so the shape it accepts has to be checked rather than
        # assumed; a non-list is treated as no declaration at all, which is the same answer
        # an absent ``authentication`` gets.
        schemes = []
    scheme = next((enum_value(s) for s in schemes if s), None)
    credential = _read("credentials")
    return DeclaredAuth(scheme=str(scheme) if scheme else None, credential=str(credential) if credential else None)


#: The wire value for "we will sign subsequent webhooks with RFC 9421", i.e. the mode a
#: receiver gets when it declares no ``authentication`` at all.
DELIVERY_AUTH_RFC9421 = "rfc9421"


def _fold_scheme(scheme: str) -> str:
    """The case- and separator-folded key a scheme is looked up by.

    ONE folding, used both to BUILD :data:`_DELIVERY_AUTH_MODES` and to look up in it:
    two copies of this expression could fold differently, and then a receiver's spelling
    would miss a table built from the same pin that knows it.
    """
    return scheme.strip().lower().replace("_", "-")


#: The wire spelling of every legacy mode, keyed by a case- and separator-folded form of
#: the scheme a receiver named. DERIVED FROM THE PINNED ENUM, never transcribed:
#: ``webhook-challenge.json``'s ``delivery_auth.mode`` is closed over
#: ``[rfc9421, Bearer, HMAC-SHA256]``, and those two non-9421 values are exactly
#: ``AuthenticationScheme``'s members — the same object
#: :func:`src.core.security.webhook_egress._headers_for` matches on to pick the arm. So
#: the mode we TELL a receiver we will use and the arm the seam actually takes cannot
#: disagree, and a member added to (or removed from) the pin moves both at once.
_DELIVERY_AUTH_MODES: dict[str, str] = {
    _fold_scheme(enum_value(member)): enum_value(member) for member in AuthenticationScheme
}


def delivery_auth_mode(auth: DeclaredAuth) -> str:
    """The ``delivery_auth.mode`` a challenge reports for *auth* (#1291 C2).

    The REPORTING twin of the seam's arm selection, off the pinned
    ``AuthenticationScheme`` both read.

    Absent ``authentication`` is :data:`DELIVERY_AUTH_RFC9421`, matching the seam's
    ``auth is None`` arm (security.mdx @ v3.1.1 :1424 keys 9421 on ABSENCE). A scheme the
    pin knows reports its own wire spelling. An UNRECOGNIZED scheme reports ``Bearer``
    rather than being echoed verbatim, because the challenge enum is closed and a value
    outside it is a document the receiver must reject; ``Bearer`` is the answer that says
    "a legacy, token-bearing registration", which is the only honest thing left to say
    about a scheme this pin cannot name.
    """
    if auth.scheme is None:
        return DELIVERY_AUTH_RFC9421
    return _DELIVERY_AUTH_MODES.get(_fold_scheme(auth.scheme), AuthenticationScheme.Bearer.value)


def credential_fingerprint(auth: DeclaredAuth) -> str | None:
    """The sha256 hex of *auth*'s credential, or ``None`` when there is none.

    ``webhook-challenge.json`` requires this for the legacy modes and FORBIDS it for
    ``rfc9421``, so the two are derived from the same pair: no scheme means no
    fingerprint, by construction rather than by a caller remembering the rule. It is a
    fingerprint and not the credential because the challenge body is a document the
    receiver may log — the receiver already knows its own secret and only needs to confirm
    we hold the same one.
    """
    if auth.credential is None:
        return None
    return hashlib.sha256(auth.credential.encode("utf-8")).hexdigest()


def _warn_once(warning_id: str, tenant_id: str | None) -> bool:
    """Claim the right to warn about *warning_id* for this tenant, once per process.

    True the first time a (warning, tenant) pair is seen and False afterwards, so each
    caller keeps its own message next to its own condition rather than routing every
    warning through one formatter.
    """
    key = (warning_id, tenant_id or "<unknown-tenant>")
    with _warn_lock:
        if key in _WARNED:
            return False
        _WARNED.add(key)
    return True


def reset_warning_state() -> None:
    """Forget every warning already emitted. For rotation tooling and test isolation.

    The seam this module previously only claimed to have: ``reset_keyless_warning_state``
    existed but no caller in ``src/`` or ``tests/`` invoked it, and the second cache had
    no reset at all. A process-level signing cache with no reset has already produced
    order-dependent failures in this layer.
    """
    with _warn_lock:
        _WARNED.clear()


def _warn_keyless_once(tenant_id: str | None) -> None:
    """WARN once per tenant per process that deliveries are going out unsigned."""
    if not _warn_once("keyless", tenant_id):
        return
    logger.warning(
        "Tenant %s has no ACTIVE signing key, so its outbound AdCP webhooks are delivered "
        "UNSIGNED. This is the honest posture only while webhook_signing.supported is false; "
        "provision a request-signing key to enable the RFC 9421 profile (#1291).",
        tenant_id or "<unknown-tenant>",
    )


def _warn_unsignable_challenge(tenant_id: str) -> None:
    """WARN once that this tenant cannot prove endpoint control, and how to fix it.

    Names the provisioning path, because "no signing key" is an operator action and not a
    dead end — an activation that fails with no actionable log is the quiet failure this
    project bans even when the OUTCOME is correct.

    Its own suppression domain, distinct from the keyless-delivery warning: this condition
    means an activation WILL FAIL, which an operator needs to hear even after already being
    told that deliveries go out unsigned.
    """
    if not _warn_once("unsignable-challenge", tenant_id):
        return
    logger.warning(
        "Tenant %s cannot sign a notification proof-of-control challenge: it has no ACTIVE "
        "signing key this deployment can open on an https origin it can publish a trust root "
        "from. Activating a notification subscriber will fail until one is provisioned — see "
        "scripts/ops/provision_signing_key.py or the admin signing-keys route (#1291).",
        tenant_id,
    )


@contextmanager
def signing_repo(tenant_id: str | None) -> Iterator[SigningKeyRepository | None]:
    """The tenant-scoped signing repository, on a session THIS function owns.

    It always opens its own and takes none from a caller. A ``repo=`` parameter used to
    let a caller donate one built on ITS session, which is how the webhook delivery loop
    came to hold a pooled connection across ``time.sleep`` and a POST to a buyer-supplied
    URL: the connection's lifetime belonged to whoever called, not to the work.

    Removing the parameter makes that UNREPRESENTABLE rather than merely discouraged — a
    caller cannot donate a lifetime it has no way to pass (#1757, salesagent-n78j0.4).
    The session opened here lives for the key read and closes with the block, so it is
    never held across a delivery.
    """
    if tenant_id is None:
        yield None
        return

    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.signing_key import SigningKeyRepository

    with get_db_session() as session:
        yield SigningKeyRepository(session, tenant_id)


def _agent_origin(repo: SigningKeyRepository) -> str | None:
    """This tenant's canonical origin, read on the session *repo* already holds.

    ``canonical_agent_url`` (``src/core/agent_identity.py``) is the ONE agent-URL
    derivation — a second literal here is a ``request_signature_key_origin_mismatch``
    waiting to happen, because the verifier byte-matches the origin our keys resolved
    at against the one we published.

    ``None`` when there is no tenant row: unknown origin is not a publishable one, so
    the RFC 9421 arm closes. The read happens inside the repository's OWN transaction —
    the same one that produced the key row — which ``canonical_origin`` enforces by
    construction rather than by asking (#1757).
    """
    return repo.canonical_origin()


def webhook_delivery_signer(
    *,
    tenant_id: str | None,
    repo: SigningKeyRepository | None,
    now: datetime,
) -> JwkSignerStrategy | None:
    """The RFC 9421 strategy this tenant's DELIVERIES are signed with, or ``None``.

    The whole of the RFC 9421 arm's KEY decision, with the delivery act removed. The act
    itself belongs to :func:`src.core.security.webhook_egress.deliver_webhook` and its
    async twin; this answers only "which key", so a second copy of it cannot appear beside
    a second sender.

    ``None`` means "deliver unsigned, honestly": a tenant with no active signing key,
    or a keyed one on an origin whose trust root cannot be published, has
    ``webhook_signing.supported=false`` in the capabilities a receiver reads, so a
    signature it could not resolve a key for is worse than no signature (security.mdx @
    v3.1.1 :1226). That is a DECIDED posture, warned once per tenant per process, not a
    fallback taken because something failed — the caller gets ``None`` and delivers plain,
    and never sees a signer that quietly stopped signing.

    The posture read here is the SAME object ``get_adcp_capabilities`` serializes
    (#1291 D1), which is what makes the advertised ``webhook_signing.supported`` and
    this branch one decision rather than two. It therefore inherits the publishability
    gate: on an origin that cannot serve https there is no conformant
    ``identity.brand_json_url`` for a receiver to resolve our key through, so the arm is
    dropped. That removes an UNVERIFIABLE signature rather than withdrawing a capability.

    The algorithm about to go on the wire is still checked against that posture before
    the strategy is handed back. After D1 the check is unreachable by construction —
    both sides read one object — which is the point: it is the belt to the derivation's
    braces, and a future second derivation trips it instead of shipping.

    The return TYPE is the RFC 9421 oracle, exactly as on :func:`adcp_challenge_signer`:
    ``JwkSignerStrategy`` is the class that emits the webhook profile's tag, so a future
    edit cannot hand a caller a Bearer or legacy-HMAC strategy and still satisfy the
    annotation. That matters most to the caller this function exists for —
    :func:`src.core.security.webhook_egress.deliver_webhook`, whose RFC 9421 arm is
    selected by the ABSENCE of an ``authentication`` block (security.mdx @ v3.1.1 :1424)
    and which must therefore never be handed a legacy strategy to apply there (:1425
    forbids answering a legacy registration with an RFC 9421 signature, and the converse
    shape — a legacy strategy on the 9421 arm — would be the same confusion mirrored).
    The seam IGNORES ``signer`` on both legacy arms, so the two rules meet as a shape.

    It is deliberately NOT the same function as :func:`adcp_challenge_signer`. A
    challenge and a delivery select the same key by the same two calls
    (``webhook_signing_posture`` + ``resolve_signing_material``, both shared), but they
    are different obligations with different failure vocabularies: a challenge that
    cannot be signed means an activation WILL FAIL and gets its own warning domain,
    while an unsignable delivery goes out plain. Fusing them would give one of the two
    conditions the other's log line.

    Raises:
        AdCPConfigurationError: the resolved key's ``alg`` contradicts the declared
            ``webhook_signing.algorithms``, or key material cannot be opened. Never
            downgraded to ``None`` — see :func:`delivery_signer_for_tenant`.
    """
    from adcp.webhook_auth import JwkSignerStrategy

    if repo is None or tenant_id is None:
        _warn_keyless_once(tenant_id)
        return None

    origin = _agent_origin(repo)
    posture = webhook_signing_posture(repo, now=now, origin=origin)
    if not posture.supported:
        _warn_keyless_once(tenant_id)
        return None

    material = resolve_signing_material(repo, tenant_id=tenant_id, now=now)
    declared = {enum_value(alg) for alg in posture.algorithms or ()}
    if material.alg not in declared:
        # No ``message=``: the buyer-facing sentence is CODE_TABLE's. What the operator
        # needs — which tenant, which axis, the value we would have signed with and the
        # set we advertised — is the details shape, and the raise escapes before anything
        # is serialized, so nothing is sent either signed or plain.
        raise AdCPConfigurationError(
            details=ConfigurationDetails(
                tenant_id=tenant_id,
                capability="webhook_signing.algorithms",
                rejected_value=material.alg,
                accepted_values=sorted(declared),
            )
        )

    return JwkSignerStrategy(private_key=material.private_key, key_id=material.kid, alg=material.alg)


def delivery_signer_for_tenant(tenant_id: str | None, *, now: datetime | None = None) -> JwkSignerStrategy | None:
    """*tenant_id*'s RFC 9421 delivery strategy, resolved on a session closed BEFORE any dial.

    The open-read-close composition every sender on the egress seam (GH #1802) needs
    verbatim: own a signing session, consume it eagerly, close it, hand back primitives.
    It lives HERE, beside the decision it composes, because three senders —
    ``webhook_delivery_service``, ``order_approval_service`` and
    ``protocol_webhook_service`` — each wrote it independently and each left a docstring
    saying it belonged in one place. Three copies of "which session resolves which
    tenant's key" is how one transport signs with a key another transport does not have,
    which is the exact failure :func:`webhook_delivery_signer` exists to prevent one level
    down. ``signing_repo`` is defined above in this module and resolved at call time; it is
    named here rather than reimplemented so the session lifetime has one owner.

    **The session never spans a socket.** :func:`signing_repo` owns it (it accepts none
    from a caller) and :func:`webhook_delivery_signer` consumes it EAGERLY — origin,
    posture and key material are all read before it returns, and the strategy it hands
    back holds key material rather than a repository — so nothing lazy survives the
    ``with`` and no pooled connection is parked on a buyer's latency (#1757,
    salesagent-n78j0.4). Callers must therefore call this BEFORE the delivery call, never
    inside a context that also holds the connection.

    **No silent downgrade, and no ``try`` here.** ``None`` and a raise are two different
    answers and this function never converts one into the other:

    * ``None`` is a DECIDED posture, not a failure — no tenant/repository, no ACTIVE
      signing key, or a key on an origin whose trust root cannot be published. Such a
      tenant's capabilities already advertise ``webhook_signing.supported=false``, so its
      receivers have been told not to expect a ``Signature`` header and the delivery goes
      out plain on whichever arm :func:`src.core.security.webhook_egress._headers_for`
      selects.
    * anything else RAISES (notably ``AdCPConfigurationError``: the key's ``alg``
      contradicts the declared ``webhook_signing.algorithms``, which every receiver
      validating the declaration must reject). Catching that and passing ``signer=None``
      would turn a misconfiguration into an unsigned delivery to a receiver that IS
      verifying — the silent downgrade the seam exists to remove. The raise escapes before
      anything is serialized, so nothing is sent.

    What each caller does with that raise is deliberately NOT unified here, because the
    three senders genuinely differ and flattening them would change behaviour:
    ``protocol_webhook_service`` catches it at its own delivery boundary and books an
    ``unexpected`` outcome with zero attempts (delivery-log row plus audit warning);
    ``webhook_delivery_service`` lets it reach ``_send_webhook_enhanced``'s outermost
    handler; ``order_approval_service`` lets it propagate to the two polling-thread
    callers that already wrap their call. All three send nothing.

    Callers pass the signer UNCONDITIONALLY, without first asking whether the receiver's
    registration selects a legacy arm: ``_headers_for`` owns that selection and IGNORES
    the signer on both legacy arms, so re-deriving it at a call site would be the second
    copy of the selector this layer exists to delete.

    Where *tenant_id* comes from also stays the caller's: the delivery service reads it off
    the DEQUEUED queue entry (so the key that signs is the one the item being delivered
    names), while the other two read it off the config row / task context they were handed.
    That is a different question from "which key signs this tenant's webhooks", and only the
    latter is shared.

    ``now`` defaults to :func:`datetime.now` in UTC — the value all three call sites passed
    — and is injectable because key validity is a time-dependent decision and a caller
    pinning a clock must be able to.
    """
    with signing_repo(tenant_id) as repo:
        return webhook_delivery_signer(tenant_id=tenant_id, repo=repo, now=now or datetime.now(UTC))


def adcp_challenge_signer(*, tenant_id: str, repo: SigningKeyRepository, now: datetime) -> JwkSignerStrategy | None:
    """The RFC 9421 strategy for a proof-of-control challenge, or ``None`` if we cannot sign.

    #1291 C2. A challenge is not a delivery: it is an assertion of THIS seller's identity
    that the receiver must verify before echoing, so it needs the signing decision without
    the delivery act — and with a body the delivery path cannot produce. ``send_raw``
    INJECTS ``idempotency_key`` into the body before signing, and ``webhook-challenge.json``
    is ``additionalProperties: false`` with exactly seven allowed properties, so a
    delivery-shaped send produces a document every conformant receiver must reject. The
    SDK's own challenge helpers emit FOUR of those seven and accept no argument for the
    other three, so the caller builds the payload from the SDK TYPE and posts it through
    :func:`send_signed_challenge`.

    **There is deliberately NO ``config`` parameter.** ``sync_accounts.mdx`` @ v3.1.1 :207
    is explicit that the challenge "MUST be signed with the seller's RFC 9421 webhook
    profile key EVEN WHEN the candidate config selects legacy delivery auth". Threading the
    candidate registration in here — the shape a DELIVERY reads, where :1425 forbids the
    opposite — would silently downgrade the challenge to Bearer or HMAC and break that
    MUST. The candidate's authentication belongs in the challenge as DATA
    (``delivery_auth``, via :func:`delivery_auth_mode` and
    :func:`credential_fingerprint`), never as the signing mode.

    ``None`` means "this tenant cannot sign", which the caller must turn into "not proven".
    The return TYPE is the RFC 9421 oracle: ``JwkSignerStrategy`` is the exact class that
    emits the webhook profile's tag, so a future edit cannot hand back an unauthenticated
    strategy and still satisfy the annotation.

    Reuses :func:`webhook_signing_posture` (the ONE key-presence derivation, which since
    #1291 D1 also carries the trust-root publishability gate) and
    ``resolve_signing_material`` — the same two calls :func:`webhook_delivery_signer`
    makes, so key selection cannot diverge between a challenge and the deliveries that
    follow it.
    """
    from adcp.webhook_auth import JwkSignerStrategy

    if not webhook_signing_posture(repo, now=now, origin=_agent_origin(repo)).supported:
        _warn_unsignable_challenge(tenant_id)
        return None

    material = resolve_signing_material(repo, tenant_id=tenant_id, now=now)
    return JwkSignerStrategy(private_key=material.private_key, key_id=material.kid, alg=material.alg)


@dataclass(frozen=True, slots=True)
class ChallengeAnswer:
    """What a proof-of-control challenge came back with: a status and the bytes.

    The seam answers with :class:`~src.core.security.egress.response.OutboundResult`,
    which carries four more fields the proof decision has no business reading. This is the
    projection onto the two members ``notification_proof_service`` actually grades — the
    same move :class:`~src.core.security.outbound_http.WrappedFailure` makes one layer
    down, and for the same reason: a type crossing a boundary is how an httpx object
    reaches a module the egress ban forbids importing httpx into.
    """

    status_code: int
    content: bytes


async def send_signed_challenge(
    *,
    url: str,
    body: bytes,
    strategy: JwkSignerStrategy,
    timeout_seconds: float,
) -> ChallengeAnswer:
    """Sign *body* and POST it — the ONE place a proof-of-control challenge leaves (#1291 C2).

    The sign and the POST are one function because no PUBLIC SDK path can send a conformant
    AdCP challenge, read from the installed ``adcp==6.6.0`` rather than inferred:

    * ``WebhookSender.send_raw`` does ``body_dict = {**payload, "idempotency_key": …}``
      BEFORE signing, and ``webhook-challenge.json`` is ``additionalProperties: false`` with
      exactly seven allowed properties — so a delivery-shaped send produces a document every
      conformant receiver must reject;
    * ``WebhookSender.resend()`` raises ``ValueError("cannot resend: result has no captured
      sent_body …")`` — the SDK author anticipated a fabricated result and refused it;
    * ``_send_bytes``, the only method that posts caller-supplied bytes without injection, is
      private;
    * the three challenge helpers emit four of the seven required fields and accept no
      argument for ``seller_agent_url``, ``delivery_auth`` or ``event_types``.

    Living HERE rather than in the calling service is what leaves that service with no raw
    POST at all, so the outbound boundary has one fewer allowlisted exception rather than a
    renamed one.

    ``timeout_seconds`` is a PARAMETER. The seam's own default of 10.0s is right for a
    background delivery and wrong for a handshake inside the request cycle, where the
    buyer's latency budget is the constraint — the caller passes its own ceiling and the
    difference stays visible at the call site.

    ``Content-Type`` is decisive rather than decoration: ``JwkSignerStrategy`` builds its
    signature base from ``headers={"Content-Type": "application/json"}`` and covers the
    ``content-type`` component whenever that header is present, while a webhook verifier
    REJECTS a signature whose covered components omit it (``security.mdx`` @ v3.1.1 :1476,
    ``webhook_signature_components_incomplete``). The seam's ``content=`` path sets no
    Content-Type of its own, so it ships explicitly or the signature covers a header that
    never left.

    ``content=`` and never ``json=``: ``json=`` would re-encode the payload and the signature
    would cover bytes that never went on the wire (#1441's defect class). The caller
    serializes once and hands those exact bytes here.

    **Destination policy is the seam's** (GH #1802, GH #1890). This used to open a bare
    ``AsyncClient`` of its own, so the destination was validated ONCE at the caller's fire time
    and never again when the socket opened — a TOCTOU, plus no redirect refusal, no port
    allowlist, no ``trust_env=False`` and no body cap.
    :func:`~src.core.security.outbound_http.asend` makes all six the same decision every
    other outbound call in this application makes: it resolves the host ONCE inside
    ``EgressPolicy.resolve_for_dial`` and pins the connection to that address, so there is
    no second resolution left to disagree with the first. ``CounterpartyUrl`` marks the
    provenance — the URL came off a buyer's registration — with no ``field``, because this
    module is handed a URL and never the request document a locator would point into.

    The signature is applied through the seam's per-attempt ``sign=`` hook rather than
    precomputed here. That is what keeps this a SIGNED dial without a client of its own:
    the hook fires inside ``asend`` after ``client.build_request``, over
    ``request.content`` — the exact bytes httpx will transmit, which are ``body`` — and over
    the post-normalization ``request.url``, so signed bytes and wire bytes are one object
    rather than two that agree. Each attempt gets its own call and therefore its own RFC
    9421 ``nonce``, which is why the hook exists at all.

    ``max_attempts=1``: a challenge is a handshake inside the request cycle, budgeted by the
    caller's 2.0s ceiling, and it was exactly one POST before this moved onto the seam.
    BR-RULE-029's 1s/2s backoff between three attempts would blow that budget several times
    over for a subscriber that is not going to answer.

    There is NO silent downgrade. ``strategy`` is required and non-optional, so an
    unsignable challenge cannot reach here at all — :func:`adcp_challenge_signer` returns
    ``None`` and the caller turns that into "not proven" without dialling. A signer that
    raises mid-attempt escapes ``asend`` (it is not in the retryable set), and a refused
    destination raises ``OutboundRequestBlocked`` from ``resolve_for_dial`` before any
    request is built — so on both paths nothing is sent, rather than something being sent
    unsigned.

    A non-2xx answer is projected back onto :class:`ChallengeAnswer` rather than allowed to
    escape as ``OutboundDeliveryFailed``, so the caller keeps grading the status itself and
    still logs *which* status the endpoint answered. Its ``content`` is empty on that path
    and honestly so: the seam surfaces no body for an undelivered request (AdCP 3.1.1
    ``security.mdx`` point 6 — nothing derived from the origin's response rides out on a
    failure), and the echo check never reads it, because a non-2xx has already failed the
    2xx gate that precedes it. A transport failure has no status to project and is
    re-raised unchanged.
    """
    try:
        result = await asend(
            url,
            method="POST",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
            max_attempts=1,
            provenance=CounterpartyUrl(field=None),
            sign=strategy.build_auth_headers,
        )
    except OutboundDeliveryFailed as exc:
        if exc.http_status is None:
            raise
        return ChallengeAnswer(status_code=exc.http_status, content=b"")
    return ChallengeAnswer(status_code=result.http_status, content=result.content)

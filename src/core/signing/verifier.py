"""THE inbound RFC 9421 verifier, called by ``_resolve_identity``. #1291 B1-B4.

Core invariant
--------------
A signature is a CREDENTIAL, and this is the function the one reader of a credential calls
to read it. It decides every inbound signature outcome by *choosing whether to call* the
SDK's unconditional ``verify_request_signature`` and whether to swallow its exception. It
never reimplements a checklist step, never reorders one, and never reads a posture from
anywhere but the single :class:`~src.core.signing.posture.RequestSigningPosture` the
resolver's own tenant row resolves to.

Where it sits, and why that changed
-----------------------------------
Before #1721 this was an ASGI middleware that read the ``Signature`` headers, resolved its
own tenant, resolved its own principal, and SENT its own 401 — because there was no single
place downstream that held all three. #1721 built that place. ``_resolve_identity`` reads
the headers once, loads the tenant once and resolves the principal once, so the verifier
becomes a function it calls with what it already has:

* the posture comes from the tenant row the resolver LOADED, so the duplicated tenant lookup
  (``_detect_tenant_for_posture``) and the swallow-everything failure mode around it are
  gone;
* the composition rule's "the caller presents no other credential the verifier accepts"
  reads the principal the resolver already resolved, rather than a second token lookup;
* a refusal is RAISED, not sent. It leaves as ``AdCPRequestSignatureError``, ``invoke_tool``
  catches it, ``failure_response`` renders it, and ``AuthChallengeResponder`` — the ONE
  renderer — lifts it to 401 and writes ``WWW-Authenticate: Signature error="<code>"`` from
  the code it reads off the finished body. The old docstring's "a raise here becomes a 500
  in ServerErrorMiddleware" was true at the ASGI layer and is false here, where the
  boundary's cascade exists.

The operation this request invokes is likewise no longer DERIVED from the body. The boundary
resolved ``TOOLS[tool_name]`` before calling the resolver, so the AdCP-namespace name is
known exactly, on all three transports, with no per-transport parse and no "unnameable
request" to fail closed on.

What the boundary cannot see — READ THIS BEFORE TRUSTING THE COVERAGE
---------------------------------------------------------------------
Everything above holds for requests that reach ``invoke_tool``. Requests that do not are NOT
verified here, and there are two classes of them:

* **the JSON-RPC protocol-method namespace.** ``protocol_methods_required_for`` /
  ``_warn_for`` / ``_supported_for`` grade the ENVELOPE's ``method`` — ``tasks/cancel``,
  ``tasks/get``, ``tasks/pushNotificationConfig/set``. Those methods are answered by the
  a2a-sdk's own handlers and by FastMCP's session machinery; none of them calls the
  boundary. :meth:`~src.core.signing.posture.RequestSigningPosture.bucket_for` implements the
  matching correctly and nothing reaches it with a protocol method, so a tenant declaring
  those buckets today gets no enforcement.
* **the webhook-credential escalation on that same namespace.**
  ``tasks/pushNotificationConfig/set`` registers a webhook AND its credentials with no skill
  invocation anywhere in sight, which is exactly the shape security.mdx @ v3.1.1 :1462-1465
  requires a signature for. On the AdCP namespace the escalation IS enforced
  (:mod:`src.core.signing.webhook_credentials`, read off the validated request); on the
  protocol namespace it is not.

Both are a consequence of moving verification downstream of transport unwrapping, and the
fix is not a second verifier: it is for those handlers to reach the same boundary. Recorded
rather than hidden, because a posture that advertises ``protocol_methods_required_for`` and
enforces nothing is the silent-unverified failure this whole area exists to remove.

Spec grounding: AdCP 3.1.1 via ``adcp==6.6.0``;
``v3.1.1:docs/building/by-layer/L1/security.mdx`` (there is no ``dist/docs/3.1.1/`` at that
tag — ``dist/docs/`` stops at 3.1.0) and
``v3.1.1:dist/compliance/3.1.1/universal/signed-requests.yaml`` (12 positive / 28 negative
vectors, graded on 2xx / 401 + the ``WWW-Authenticate`` code byte-for-byte).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from adcp.signing.agent_resolver import AgentResolution, AgentResolverError, resolve_agent
from adcp.signing.canonical import split_structured_field
from adcp.signing.errors import (
    REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON,
    REQUEST_SIGNATURE_BRAND_JSON_AMBIGUOUS,
    REQUEST_SIGNATURE_BRAND_JSON_MALFORMED,
    REQUEST_SIGNATURE_BRAND_JSON_UNREACHABLE,
    REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING,
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_JWKS_UNAVAILABLE,
    REQUEST_SIGNATURE_JWKS_UNTRUSTED,
    REQUEST_SIGNATURE_REQUIRED,
    SignatureVerificationError,
)
from adcp.signing.jwks import JwksResolver, StaticJwksResolver
from adcp.signing.replay import ReplayStore
from adcp.signing.revocation import RevocationChecker
from adcp.signing.verifier import (
    VerifiedSigner,
    VerifierCapability,
    VerifyOptions,
    parse_signature_input_header,
    verify_request_signature,
)

from src.core.config import CounterpartyRegistryEntry, SigningSettings, get_settings
from src.core.database.database_session import get_db_session
from src.core.database.repositories.replay_nonce import ReplayNonceRepository
from src.core.exceptions import adcp_error_for
from src.core.metrics import record_request_unsigned, record_signature_failed, record_signature_verified
from src.core.schemas import Principal
from src.core.signing.canonical import malformed_authority_reason, reject_malformed_target
from src.core.signing.capture import HttpExchange, SignatureSubject
from src.core.signing.posture import PostureBucket, RequestSigningPosture, posture_for_tenant
from src.core.signing.replay_store import PostgresReplayStore
from src.core.signing.revocation import checker_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.tenant_context import TenantContext

logger = logging.getLogger(__name__)

#: Process-level ``{agent_url: AgentResolution}``. The WHOLE resolution is kept, not just the
#: JWKS: ``expected_key_origins`` comes from it and is mandatory on every verify for
#: brand-json-sourced keys. Entries expire by ``agent_resolution_ttl_seconds`` against
#: ``AgentResolution.fetched_at``.
AGENT_RESOLUTION_CACHE: dict[str, AgentResolution] = {}

#: The purpose key under the counterparty's ``identity.key_origins`` map.
_SIGNING_PURPOSE = "request_signing"


@dataclass(frozen=True)
class _ResolutionFailure:
    """When a counterparty's walk last failed, and which spec code it mapped to.

    The code travels WITH the failure (rather than being recomputed) so a request arriving
    inside the refetch cooldown answers the SAME discovery code as the request that triggered
    it, instead of silently degrading to the generic ``key_unknown`` once the failure is no
    longer fresh.
    """

    at: float
    code: str


#: ``{agent_url: last failure}``. Without it, every signed request from a counterparty with a
#: broken brand.json starts a fresh 3-hop outbound walk.
_RESOLUTION_FAILURES: dict[str, _ResolutionFailure] = {}


@dataclass(frozen=True)
class _CounterpartyResolution:
    """A resolved (or unresolved) counterparty, tagged with WHERE it came from."""

    resolution: AgentResolution | None
    source: Literal["walk", "registry"]


class _BrandJsonJwksResolver(StaticJwksResolver):
    """A resolver that tells the verifier its keys came from the brand.json walk.

    The SDK engages the spec's step-7 key-origin consistency check ONLY for resolvers
    advertising ``jwks_source = "brand_json"`` and exposing the resolved ``jwks_uri``; a plain
    ``StaticJwksResolver`` is treated as a publisher-pinned tuple and skips the check with
    nothing but a warning. Declaring the conformance is documented adopter API.
    """

    jwks_source = "brand_json"

    def __init__(self, jwks: dict[str, Any], *, jwks_uri: str) -> None:
        super().__init__(jwks)
        self.jwks_uri = jwks_uri


class _FailedDiscoveryJwksResolver:
    """Raise the discovery failure's MAPPED code from ``__call__``, not the generic ``key_unknown``.

    Raising happens INSIDE ``__call__``, which the SDK's checklist invokes at step 7 — AFTER
    steps 1-6 already ran on their own merits. Raising at ``VerifyOptions`` CONSTRUCTION time
    instead would let a discovery failure outrank an earlier checklist step, reordering a
    graded artifact: the conformance suite fixes which step each negative vector fails at, and
    step 1 is not negotiable against a step-7 concern.
    """

    def __init__(self, code: str) -> None:
        self._code = code

    def __call__(self, keyid: str) -> dict[str, Any] | None:
        raise SignatureVerificationError(self._code, step=7, message=f"counterparty discovery failed: {self._code}")


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def verify_inbound_signature(
    subject: SignatureSubject,
    *,
    headers: Mapping[str, str],
    tenant: TenantContext | None,
    principal: Principal | None,
) -> VerifiedSigner | None:
    """Read the signature credential this request presents, if any. THE verifier entry.

    Returns the verified signer when a signature was PRESENTED and ACCEPTED, and ``None``
    when the request presented none or when this seller's posture does not grade it. Raises
    :class:`~src.core.exceptions.AdCPRequestSignatureError` when it refuses.

    SYNCHRONOUS on purpose. ``_resolve_identity`` is synchronous and the boundary already
    runs it in a worker thread because it hits the database; the counterparty walk is blocking
    I/O, which is exactly what that thread is for. The SDK's ``resolve_agent`` is the sync
    half of the same discovery its ``async_resolve_agent`` does, so there is no event-loop
    bridge and no second loop anywhere in this path.

    *principal* is the caller the BEARER resolved, and it is read for two different things.
    Whether it is None at all is the third term of the spec's three-way AND at :1224 — "…AND
    the caller presents no other credential the verifier accepts" — and the only thing that
    separates the 401 branch from the pass-through branch on an unsigned request. Its
    ``agent_url`` is the key-resolution INPUT: the spec's § "agent_url derivation" forbids
    taking the signer's agent URL from a header or a body field, because that would let the
    signer choose which brand.json (and therefore which key set) it is verified against, so
    the only legitimate source is what onboarding recorded.
    """
    config = get_settings().signing
    if not config.verifier_enabled:
        # The kill switch. No posture is read and no bucket is graded, so a rollback is a flag
        # flip: every request resolves identity from its bearer alone, exactly as it did
        # before this feature existed.
        return None

    posture = posture_for_tenant(tenant)
    bucket = _bucket_for(posture, subject)
    exchange = subject.exchange
    agent_url = principal.agent_url if principal is not None else None

    if exchange is None or not exchange.presents_signature():
        _refuse_unsigned_if_required(posture, subject, bucket, authenticated=principal is not None)
        record_request_unsigned(subject.operation, "absent")
        return None

    if agent_url is None and principal is not None:
        # Onboarding recorded no agent URL for this counterparty, so there is no brand.json to
        # walk and therefore no key. The checklist still runs — a malformed signature must
        # still be refused (:1226) — and reaches ``request_signature_key_unknown`` on its
        # merits at step 7.
        logger.warning(
            "Principal %r signed a request but has no agent_url on record; no signing key can be resolved for it",
            principal.principal_id,
        )

    return _verify_signed(
        posture, subject, exchange, bucket=bucket, headers=headers, agent_url=agent_url, config=config
    )


def _bucket_for(posture: RequestSigningPosture, subject: SignatureSubject) -> PostureBucket:
    """The enforcement bucket for this request, with the credential escalation applied.

    ``bucket_for`` is asked for the AdCP-operation namespace ONLY: ``protocol_method`` is not
    passed, because the envelope that reached the boundary named an AdCP operation. That is
    the cross-namespace rule (:1053) expressed structurally rather than checked — there is no
    value here that COULD be graded against ``protocol_methods_*``.

    ":1375 regardless of ``required_for`` membership" promotes the REQUEST, not one branch. It
    is applied HERE, before the signed/unsigned split, which is what keeps the escalation from
    being bypassable by simply ATTACHING signature headers: a request whose bucket stayed
    ``none`` is waved through unverified on the signed path, so an escalation enforced only on
    the unsigned branch would refuse the honest unsigned registration and admit the same
    registration carrying a junk ``Signature``.
    """
    bucket = posture.bucket_for(subject.operation)
    if subject.registers_credentials and posture.supported:
        return "required"
    return bucket


def _refuse_unsigned_if_required(
    posture: RequestSigningPosture,
    subject: SignatureSubject,
    bucket: PostureBucket,
    *,
    authenticated: bool,
) -> None:
    """The composition rule, security.mdx @ v3.1.1 :1268-1271.

    The SDK cannot decide this — its ``_precheck_presence`` raises
    ``request_signature_required`` on the absent branch UNCONDITIONALLY, which is the strict
    reading the spec normatively rejects. :1289 names the failure that reading produces ("a
    seller enabling ``required_for`` for operational monitoring would inadvertently 401 every
    bearer-authed buyer"), and this agent is bearer-authenticated on essentially every AdCP
    request, so the strict reading would reject nearly all production traffic the moment a
    tenant populates ``required_for``.

    The webhook-credential escalation sits OUTSIDE that rule, deliberately: the rule exists
    BECAUSE the registering caller is normally bearer-authed and an on-path mutator can inject
    or strip the ``authentication`` block, so "valid bearer ⇒ don't reject" would defeat it
    entirely (:1462). It is already folded into *bucket*, so the only thing this needs to know
    is whether the escalation is what put the request there.
    """
    escalated = subject.registers_credentials and posture.supported
    if bucket != "required" or (authenticated and not escalated):
        return
    logger.info(
        "Refusing an unsigned request: operation=%r bucket=%r authenticated=%s escalated=%s",
        subject.operation,
        bucket,
        authenticated,
        escalated,
    )
    _refuse(SignatureVerificationError(REQUEST_SIGNATURE_REQUIRED, step=0), subject.operation)


def _verify_signed(
    posture: RequestSigningPosture,
    subject: SignatureSubject,
    exchange: HttpExchange,
    *,
    bucket: PostureBucket,
    headers: Mapping[str, str],
    agent_url: str | None,
    config: SigningSettings,
) -> VerifiedSigner | None:
    """At least one signature header present: run the pre-check, then the checklist."""
    operation = subject.operation

    # ``bucket_for`` collapses TWO situations into ``none``: a seller declaring
    # ``supported: false``, which is not a verifier at all, and ``supported: true`` with the
    # operation in none of the three lists, which IS one. Only the second is bound by the
    # spec's pre-check (:1226, "even for operations not in required_for"), and the storyboard
    # gates all 28 negative vectors on ``request_signing.supported: true`` alone. So the gate
    # reads ``supported``, never the bucket — reading the bucket would keep the defect for
    # every ``supported: true`` operation outside the lists, the larger half.
    if not posture.supported:
        record_request_unsigned(operation, "ignored")
        return None
    if exchange.over_cap:
        logger.warning("Signed request body exceeded %d bytes; refusing", config.max_signed_body_bytes)
        _refuse(
            SignatureVerificationError(
                REQUEST_SIGNATURE_HEADER_MALFORMED,
                step=1,
                message="the request body is larger than this agent will digest",
            ),
            operation,
        )
    if not exchange.complete:
        # The client disconnected mid-body. There is nothing to verify; the handler will
        # unwind on its own. The counter still fires so the series does not vanish.
        record_request_unsigned(operation, "ignored")
        return None

    # PRE-CHECK ONLY for the narrowed ``none`` bucket. Skipping ``_resolution_for`` is the
    # load-bearing half: it runs AHEAD of the SDK and is gated by no step, so leaving it in
    # would let anyone name an operation, attach two plausible headers, and force a three-hop
    # outbound walk whose result is then discarded.
    precheck_only = bucket == "none"
    try:
        # Step 1 first, and over the RAW header list: the shapes it refuses are invisible once
        # the headers collapse into a dict, and refusing them here also spares an unresolved
        # counterparty a three-hop outbound walk on a request that cannot be accepted.
        _strict_header_precheck(exchange.raw_headers)
        if precheck_only:
            counterparty = _CounterpartyResolution(None, source="registry")
        else:
            # The layer's target-URI gate, in FRONT of the SDK verifier: the pinned 6.6.0
            # verifier canonicalizes internally without the fixes the vendored copy carries,
            # so the layer gates the effective request URI with its own comparer semantics
            # first. Every divergence is thereby either refused here with the graded code, or
            # fails CLOSED inside the SDK as a signature-base mismatch — never accepted.
            #
            # NOT hoisted above the ``none`` branch: the spec's pre-check bullets cover the
            # unsigned ``required_for`` case and the two header rules and say NOTHING about
            # target-URI malformation, so hoisting it would make ``none`` refuse beyond the
            # requirement.
            reject_malformed_target(exchange.url)
            counterparty = _resolution_for(agent_url, config, keyid=_parse_keyid(headers))
        signer = _run_verifier(
            exchange=exchange,
            headers=headers,
            capability=posture.to_verifier_capability(),
            operation=operation,
            bucket=bucket,
            resolution=counterparty.resolution,
            agent_url=agent_url,
            config=config,
            precheck_only=precheck_only,
        )
    except SignatureVerificationError as exc:
        return _handle_rejection(exc, operation, bucket)

    record_signature_verified(operation, signer.key_id)
    return signer


def _handle_rejection(exc: SignatureVerificationError, operation: str, bucket: PostureBucket) -> VerifiedSigner | None:
    """The ONE owner of rejection policy: the spec's two phases, as one branch.

    AdCP 3.1.1 ``security.mdx`` puts header well-formedness in a PRE-CHECK above the operation
    bucket (:1226) and signature validity in a checklist inside it (:1273 scoping ``warn_for``
    to signed-but-invalid). The SDK collapses presence and parse into a single call, so the
    phase boundary cannot be drawn by ordering two calls. It is drawn on WHICH EXCEPTION
    bypasses the warn arm — ``is_precheck`` below.

    The PAIR, never the bare code: ``request_signature_header_malformed`` is raised at five
    different steps, and steps 2/5/6/8 are checklist failures on a WELL-FORMED header, which
    :1273 keeps warn-suppressible.

    Warn mode is OURS: ``VerifierCapability`` carries 4 of ``request_signing``'s 8 properties
    and 2 of its 6 operation buckets, so handing ``warn_for`` to the SDK would silently drop
    it. It is implemented the only way it can be — call the verifier, catch, emit the metric,
    continue — and it is observable on the WIRE (200 where ``supported_for`` answers 401), not
    merely in a counter.
    """
    is_precheck = exc.code == REQUEST_SIGNATURE_HEADER_MALFORMED and exc.step == 1
    # Suppressed for exactly one case: the narrowed ``none`` bucket verifies against an EMPTY
    # resolver, so its step-7 ``key_unknown`` is ENGINEERED by us and would otherwise be
    # indistinguishable from a real key-resolution failure in the same series. A step-1
    # malformation there is genuine, so ``is_precheck`` keeps it.
    if bucket != "none" or is_precheck:
        record_signature_failed(operation, exc.code)
    if is_precheck:
        _refuse(exc, operation, recorded=True)
    if bucket == "warn":
        logger.warning(
            "Request signature failed in warn mode (not refusing): code=%s step=%s operation=%r",
            exc.code,
            exc.step,
            operation,
        )
        return None
    if bucket == "none":
        # Pass-through, and the series stays alive: the request genuinely IS ignored (it
        # reached no checklist), and a metric that vanishes at a deploy reads as traffic
        # stopping.
        record_request_unsigned(operation, "ignored")
        return None
    _refuse(exc, operation, recorded=True)


def _refuse(exc: SignatureVerificationError, operation: str, *, recorded: bool = False) -> NoReturn:
    """Turn the SDK's typed rejection into the AdCP failure the boundary renders.

    THE translation, and the one place the graded code crosses from the SDK's taxonomy into
    this seller's error vocabulary. ``adcp_error_for``'s written-out table resolves the wire
    string to the CLASS that raises it; a code the SDK raises that this seller does not
    classify is a KeyError at that table rather than a 500 three frames later.

    ``adcp_error_for`` does the translating, and that is not a detour: it is the ONE
    normalizer from an untyped exception to a typed one (docs/design/error-architecture.md
    § "An error names its code by its class"), and the SDK's refusal is an untyped exception
    like any other. Its branch reads the written-out ``_SIGNATURE_ERROR_BY_CODE`` table and
    carries the SDK exception on ``internal_detail``.

    This used to read ``CODE_BY_VALUE[exc.code]`` and pass the result as ``error_code=`` —
    the one raise site in the tree that named its own code, and the reason the invariant on
    ``AdCPSalesAgentError.__new__`` had an exception in it. Nothing names a code now.

    The SDK's exception goes to ``internal_detail``: it carries the checklist step and a
    diagnostic sentence, which are server-log facts. AdCP 3.1.1 ``transport-errors.mdx``
    § Security Considerations forbids them reaching the buyer, and on #1721 there is nowhere
    for them to leak to — ``AdCPSalesAgentError`` has no ``message`` parameter at all.
    """
    if not recorded:
        record_signature_failed(operation, exc.code)
    raise adcp_error_for(exc) from exc


# ---------------------------------------------------------------------------
# The counterparty's key material
# ---------------------------------------------------------------------------


def build_registry_resolution(entry: CounterpartyRegistryEntry) -> AgentResolution:
    """The :class:`AgentResolution` a configured counterparty registry entry projects to.

    Same shape the brand.json walk produces — ``jwks_uri`` and ``key_origins`` consistent with
    each other — so :func:`_jwks_resolver` marks it ``brand_json`` and the spec's step-7
    key-origin consistency check stays engaged for a registry-resolved counterparty exactly as
    it is for a walked one. The four subscripts cannot raise ``KeyError``:
    :class:`~src.core.config.CounterpartyRegistryEntry` is the setting's type, so an entry
    missing one is refused at config load, where an operator sees it.
    """
    agent_url = entry["agent_url"]
    jwks_uri = entry["jwks_uri"]
    key_origin = entry["key_origin"]
    return AgentResolution(
        agent_url=agent_url,
        brand_json_url=f"{key_origin}/.well-known/brand.json",
        agent_entry={"type": "sales", "url": agent_url, "jwks_uri": jwks_uri},
        jwks_uri=jwks_uri,
        jwks=entry["jwks"],
        fetched_at=time.time(),
        key_origins={_SIGNING_PURPOSE: key_origin},
    )


def _parse_keyid(headers: Mapping[str, str]) -> str | None:
    """The keyid named in the (unverified) ``Signature-Input`` header, or None.

    Used ONLY to look up the counterparty registry fallback below — the signature must still
    cryptographically verify against whatever key this resolves to, so reading an unverified
    header here is not a bypass; it is exactly as safe as any other keyid-based key lookup
    (the SDK's own checklist resolves a JWKS entry off the same unverified field).
    """
    raw = headers.get("signature-input")
    if not raw:
        return None
    try:
        labels = parse_signature_input_header(raw)
    except ValueError:
        return None
    for label in labels.values():
        keyid = label.params.get("keyid")
        if isinstance(keyid, str):
            return keyid
    return None


def _resolution_for(
    agent_url: str | None, config: SigningSettings, *, keyid: str | None = None
) -> _CounterpartyResolution:
    """The counterparty's cached resolution, resolving on a cold entry, tagged by source.

    ``agent_url -> capabilities -> identity.brand_json_url -> brand.json agents[] -> jwks_uri
    -> JWKS`` is a three-hop outbound walk, so it is done once per counterparty per TTL and
    never per request.

    A failure is not a rejection — it returns whatever is cached (possibly nothing) and lets
    the checklist decide, carrying the MAPPED discovery code alongside it in
    :data:`_RESOLUTION_FAILURES` so :func:`_jwks_resolver` can raise the spec-assigned code at
    step 7 instead of the generic ``key_unknown``. Mapping a resolver failure straight to a
    401 HERE would make an unreachable counterparty outrank a malformed signature, which is
    the wrong error and the wrong step.

    The SSRF pin stays at the SDK default: the walk follows a URL that ultimately came from a
    counterparty document, and ``allow_private_destinations`` is a test argument.

    The registry fallback: when *agent_url* is falsy (no bearer resolved a principal, or an
    onboarded principal recorded none — exactly what a bearer-less conformance runner
    produces), *keyid* is looked up in ``config.counterparty_registry``. This branch is the
    ONLY place the registry is consulted. A principal that DOES carry an ``agent_url`` never
    reaches it, even when that walk fails below and returns ``cached`` (possibly ``None``) —
    the registry is a fallback for a walk with no INPUT, never an override for a walk that
    FAILED, or a counterparty with a briefly unreachable brand.json would be silently
    re-identified from config.
    """
    if not agent_url:
        if keyid is not None:
            entry = config.counterparty_registry.get(keyid)
            if entry is not None:
                return _CounterpartyResolution(build_registry_resolution(entry), source="registry")
        return _CounterpartyResolution(None, source="registry")

    now = time.time()
    cached = AGENT_RESOLUTION_CACHE.get(agent_url)
    if cached is not None and now - cached.fetched_at <= config.agent_resolution_ttl_seconds:
        return _CounterpartyResolution(cached, source="walk")
    failure = _RESOLUTION_FAILURES.get(agent_url)
    if failure is not None and now - failure.at < config.agent_resolution_refetch_cooldown_seconds:
        return _CounterpartyResolution(cached, source="walk")

    try:
        resolution = resolve_agent(
            agent_url,
            # The agents that sign requests TO a sales agent are the buy side, so the
            # brand.json entry to match is the counterparty's buying agent.
            agent_type=config.counterparty_agent_type,
        )
    except AgentResolverError as exc:
        mapped = _map_agent_resolver_error(exc)
        _RESOLUTION_FAILURES[agent_url] = _ResolutionFailure(at=now, code=mapped)
        logger.warning("Could not resolve signing keys for counterparty %r (%s): %s", agent_url, exc.code, exc)
        return _CounterpartyResolution(cached, source="walk")

    AGENT_RESOLUTION_CACHE[agent_url] = resolution
    _RESOLUTION_FAILURES.pop(agent_url, None)
    return _CounterpartyResolution(resolution, source="walk")


def _map_agent_resolver_error(exc: AgentResolverError) -> str:
    """``AgentResolverError.code`` -> the ``request_signature_*`` code security.mdx assigns it.

    ``adcp/signing/errors.py``'s "brand.json discovery chain" block gives each hop of the
    3-hop walk its own code precisely so a caller can tell a retryable transport failure
    (``*_unreachable``) apart from a misconfiguration (``*_missing`` / ``*_malformed``) —
    collapsing all of them onto ``request_signature_key_unknown`` erases that distinction and
    gives a counterparty with a briefly unreachable capabilities endpoint the wrong diagnosis
    AND the wrong retry advice.
    """
    if exc.code == "invalid_agent_url":
        # Trust-boundary rejection (URL wouldn't canonicalize / SSRF-banned host) — matches
        # the SDK's own reference choice for this ONE code, even though that function's
        # blanket JWKS_UNAVAILABLE treatment of everything else is the disease this fixes.
        return REQUEST_SIGNATURE_JWKS_UNTRUSTED
    if exc.code == "capabilities_unreachable":
        return REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE
    if exc.code in ("capabilities_invalid", "brand_json_url_missing"):
        # Step 2 reads identity.brand_json_url off the capabilities body; a body we cannot
        # parse at all means that field was never obtainable either.
        return REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING
    if exc.code == "jwks_fetch_failed":
        return REQUEST_SIGNATURE_JWKS_UNAVAILABLE
    if exc.code == "brand_json_resolution_failed":
        return _map_brand_json_resolver_error(exc.__cause__)
    logger.warning("Unmapped AgentResolverError code %r; defaulting to JWKS_UNAVAILABLE", exc.code)
    return REQUEST_SIGNATURE_JWKS_UNAVAILABLE


def _map_brand_json_resolver_error(cause: BaseException | None) -> str:
    """``BrandJsonResolverError.code`` -> the ``request_signature_*`` code security.mdx assigns it."""
    code = getattr(cause, "code", None)
    if code in ("fetch_failed", "redirect_loop", "redirect_depth_exceeded"):
        return REQUEST_SIGNATURE_BRAND_JSON_UNREACHABLE
    if code in ("invalid_body", "schema_invalid", "invalid_house", "invalid_url"):
        return REQUEST_SIGNATURE_BRAND_JSON_MALFORMED
    if code == "agent_ambiguous":
        return REQUEST_SIGNATURE_BRAND_JSON_AMBIGUOUS
    if code == "agent_not_found":
        return REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON
    if code == "jwks_origin_mismatch":
        # Fires when agent.url's origin != brand.json's origin and the agents[] entry declares
        # no explicit jwks_uri — genuinely "no valid jwks_uri could be derived" rather than a
        # key-origin consistency failure on an already-resolved key (that check is separate,
        # step 7, and runs on a resolution this failure never produces).
        return REQUEST_SIGNATURE_JWKS_UNAVAILABLE
    logger.warning("Unmapped BrandJsonResolverError code %r; defaulting to JWKS_UNAVAILABLE", code)
    return REQUEST_SIGNATURE_JWKS_UNAVAILABLE


def _jwks_resolver(resolution: AgentResolution | None, *, agent_url: str | None = None) -> Any:
    """The resolver handed to the checklist.

    With a resolution: a brand-json-marked resolver, which is what engages the step-7
    key-origin check. Without one: if a walk was attempted for *agent_url* and failed, a
    resolver that raises the mapped discovery code from its own ``__call__``; otherwise a plain
    empty resolver, so the checklist answers ``request_signature_key_unknown`` at step 7 on its
    own. Marking the empty resolver ``brand_json`` would instead make the SDK warn about a
    missing ``expected_key_origins`` map that by definition cannot exist.
    """
    if resolution is not None:
        return _BrandJsonJwksResolver(resolution.jwks, jwks_uri=resolution.jwks_uri)
    if agent_url is not None:
        failure = _RESOLUTION_FAILURES.get(agent_url)
        if failure is not None:
            return _FailedDiscoveryJwksResolver(failure.code)
    return StaticJwksResolver({})


# ---------------------------------------------------------------------------
# The checklist
# ---------------------------------------------------------------------------


def _run_verifier(
    *,
    exchange: HttpExchange,
    headers: Mapping[str, str],
    capability: VerifierCapability,
    operation: str,
    bucket: PostureBucket,
    resolution: AgentResolution | None,
    agent_url: str | None,
    config: SigningSettings,
    precheck_only: bool,
) -> VerifiedSigner:
    """Run the SDK checklist over one database session — or the pre-check over none.

    One session: the replay store's ``at_capacity`` / ``seen`` / ``remember`` are synchronous
    and the verifier calls them inline, so a single checkout serves all three. The SDK's own
    call order is what keeps the two spec ordering invariants — capacity before crypto verify,
    replay claim after it — and this function preserves them by not reordering anything.

    All four key-origin fields are passed: ``expected_key_origins``, ``agent_url``,
    ``signing_purpose`` and ``posture``. Omitting the first turns a mandatory check into a
    ``UserWarning``. ``expected_key_origins`` is ``resolution.key_origins or {}`` rather than
    the map itself: passing ``None`` (what a counterparty advertising no ``identity.key_origins``
    map produces) tells the SDK the ADOPTER never threaded the map through and makes it
    silently SKIP the check with a warning — the shared-tenancy defense the check exists for
    then ships silently off. An empty dict is what makes the SDK run the check and correctly
    refuse with ``request_signature_key_origin_missing``.

    Called with method/url/headers/body rather than through ``verify_starlette_request``,
    because that wrapper derives the URL from the scope and would discard the explicit
    ``X-Forwarded-Proto`` derivation :func:`~src.core.signing.capture._target_uri` makes.

    Revocation (step 9) is wired through ``revocation_checker`` and ONLY that hook.
    ``revocation_list`` is the staleness-only branch, so passing both would be two sources for
    one decision. Step 9 runs before crypto verify because the SDK calls it that way; nothing
    here may reorder it.
    """
    with _verifier_dependencies(resolution, agent_url=agent_url, config=config, precheck_only=precheck_only) as deps:
        jwks_resolver, replay_store, revocation_checker = deps
        options = VerifyOptions(
            now=time.time(),
            capability=capability,
            operation=operation,
            jwks_resolver=jwks_resolver,
            replay_store=replay_store,
            max_skew_seconds=config.max_skew_seconds,
            max_window_seconds=config.max_window_seconds,
            agent_url=resolution.agent_url if resolution is not None else None,
            expected_key_origins=(resolution.key_origins or {}) if resolution is not None else None,
            signing_purpose=_SIGNING_PURPOSE,
            posture=bucket,
            revocation_checker=revocation_checker,
        )
        return verify_request_signature(
            method=exchange.method, url=exchange.url, headers=headers, body=exchange.body, options=options
        )


@contextmanager
def _verifier_dependencies(
    resolution: AgentResolution | None,
    *,
    agent_url: str | None,
    config: SigningSettings,
    precheck_only: bool,
) -> Iterator[tuple[JwksResolver, ReplayStore | None, RevocationChecker | None]]:
    """The three dependencies the checklist needs, or the null trio for a pre-check.

    ONE ``VerifyOptions`` construction is built on this, deliberately: two literals would let
    the pre-check path and the verified path drift apart, which is the class of duplication
    this seam exists to remove.

    The null trio is what makes the narrowed ``none`` bucket safe to pre-check. The SDK
    reaches ``jwks_resolver`` after every step-1 raise has fired, so an empty resolver stops
    execution there — no signature base, no crypto, no outbound, and no database session at
    all. ``VerifyOptions`` declares ``replay_store`` and ``revocation_checker`` as optional, so
    the nulls are the SDK's own contract rather than an assumption about it.

    WRAPS the verify call rather than merely preceding it: the replay store's three methods
    are synchronous and the verifier calls them inline, so the session has to outlive the
    construction.
    """
    if precheck_only:
        yield StaticJwksResolver({}), None, None
        return
    with get_db_session() as session:
        yield (
            _jwks_resolver(resolution, agent_url=agent_url),
            PostgresReplayStore(ReplayNonceRepository(session), config),
            checker_for(resolution, config),
        )


# ---------------------------------------------------------------------------
# Checklist step 1, over the raw wire headers
# ---------------------------------------------------------------------------


def _reject_headers(message: str) -> NoReturn:
    """The one rejection the pre-parse gate raises — the SDK's own type and constant."""
    raise SignatureVerificationError(REQUEST_SIGNATURE_HEADER_MALFORMED, step=1, message=message)


def _duplicate_dictionary_key(value: str, marker: str) -> str | None:
    """Why one RFC 8941 Dictionary value is ambiguous, or ``None``. *marker* opens a member.

    Multiple DISTINCT keys are legal and must stay legal —
    ``positive/004-multiple-signature-labels`` ships ``sig1`` and ``sig2`` in both signature
    headers and is a vector we must ACCEPT. Only a REPEATED key is the ambiguity: RFC 8941
    §3.2 permits "rejecting the input" or "retaining only the last value", and the second
    option would let a proxy smuggle a weaker covered-component set, or a different signature,
    past a verifier that read the first.

    An entry this cannot parse yields ``None`` on purpose: an unparseable header is the SDK's
    to code (``negative/011``, ``negative/024``), and pre-empting it here would change a graded
    artifact that is already correct.

    ONE function for both signature headers rather than two near-identical loops: they differ
    only in what opens a member — ``=(`` an inner list, ``=:`` a byte sequence — and two copies
    is two chances for one of them to stop rejecting (CLAUDE.md, DRY).
    """
    seen: set[str] = set()
    for entry in split_structured_field(value, ","):
        at = entry.find(marker)
        if at < 0:
            return None
        key = entry[:at].strip()
        if key in seen:
            return f"the dictionary key {key!r} appears twice, which RFC 8941 §3.2 leaves ambiguous"
        seen.add(key)
    return None


def _duplicate_signature_input_label(value: str) -> str | None:
    """``Signature-Input`` members are ``<label>=(<covered components>)``."""
    return _duplicate_dictionary_key(value, "=(")


def _duplicate_signature_label(value: str) -> str | None:
    """``Signature`` members are ``<label>=:<base64>:``.

    The CREDENTIAL CARRIER, and the row that was missing. Every other header the gate reads
    was here; the one holding the signature bytes was not, so a second ``Signature`` line was
    the one repeat nothing refused — and the value that then reached the checklist was
    whichever line the transport's own header container happened to keep. ``joined_headers``
    now makes the repeat visible as one comma-joined value, and this rule is what refuses it,
    at checklist step 1, with the same code on every transport.

    ``find("=:")`` takes the FIRST occurrence, which is the label separator; base64 padding
    before the closing colon (``sig1=:AAA=:``) cannot be mistaken for it.
    """
    return _duplicate_dictionary_key(value, "=:")


def _multi_valued_content_type(value: str) -> str | None:
    """Why one ``Content-Type`` value is ambiguous, or ``None``.

    ``Content-Type`` is not a List-Structured-Field — it has one canonical value with optional
    parameters — so a second value (from a buggy client, or a proxy appending one) leaves the
    field's meaning relative to the signature base undefined.
    """
    if "," in value:
        return "the field is multi-valued, and it is not a List-Structured-Field"
    return None


def _duplicate_digest_algorithm(value: str) -> str | None:
    """Why one ``Content-Digest`` value is ambiguous, or ``None``.

    RFC 9530 §2 makes the field a Dictionary keyed by digest algorithm; two members of the SAME
    algorithm are a parser-differential, because signer and verifier may disagree about which
    value entered the base — true even if the two digests match. Distinct algorithms
    (``sha-256`` plus ``sha-512``) are legal.

    Splitting on ``,`` is sound for this field specifically: members are ``<alg>=:<base64>:``
    and the base64 alphabet contains no comma, so no member can hide a separator.
    """
    seen: set[str] = set()
    for member in value.split(","):
        algorithm = member.split("=", 1)[0].strip().lower()
        if not algorithm:
            continue
        if algorithm in seen:
            return f"the digest algorithm {algorithm!r} appears twice (RFC 9530 §2)"
        seen.add(algorithm)
    return None


#: ``header -> what makes ONE of its values malformed``. A table rather than five
#: near-identical blocks: each rule is the same shape (read a value, name a reason or pass),
#: and five copies is five chances for one of them to stop rejecting.
#:
#: ``host`` shares :func:`~src.core.signing.canonical.malformed_authority_reason` with the
#: canonicalization seam so the authority rule has exactly ONE definition. Only the CODE
#: differs by caller — checklist step 1 here, ``request_target_uri_malformed`` there — and
#: that difference is deliberate (``negative/026`` grades the former).
_MALFORMED_VALUE_RULES: tuple[tuple[bytes, Callable[[str], str | None]], ...] = (
    (b"signature", _duplicate_signature_label),
    (b"signature-input", _duplicate_signature_input_label),
    (b"content-type", _multi_valued_content_type),
    (b"content-digest", _duplicate_digest_algorithm),
    (b"host", malformed_authority_reason),
)

#: Headers that MUST arrive on exactly one line when a signature covers the request. Not a
#: style rule: a mapping cannot hold two lines of one name, so SOMETHING has to decide what a
#: repeat means, and until :func:`~src.core.signing.capture.joined_headers` existed the three
#: transports each decided differently from their own container — first line on REST and A2A,
#: last on MCP. The join makes the repeat visible; this list is what refuses it, on the fields
#: where a silently-chosen line changes what was verified.
#:
#: DERIVED from the table above, so a field gains both rules at once. ``signature`` was the
#: field that had neither: the header carrying the credential itself was the one repeat
#: nothing checked.
_SINGLE_LINE_SIGNED_HEADERS: tuple[bytes, ...] = tuple(name for name, _rule in _MALFORMED_VALUE_RULES)


def _strict_header_precheck(raw_headers: tuple[tuple[bytes, bytes], ...]) -> None:
    """Checklist step 1 over the RAW header list, before the SDK sees anything.

    Runs against the ``list[tuple[bytes, bytes]]`` the capture recorded and NOT against the
    collapsed dict, because the collapse is the attack. The conformance vectors express
    "multi-valued" as one comma-joined value, so a gate written over the dict would pass all
    four of them while missing the threat their ``$comment``s actually describe: a SECOND
    header LINE, which last-wins before any check runs.

    Why OURS and not the SDK's: measured against ``adcp==6.6.0``, the SDK answers these four
    vectors with the wrong graded artifact — ``request_signature_components_incomplete`` for
    ``negative/021`` (its RFC 8941 parser last-wins on the duplicate dictionary key) and
    ``request_signature_invalid`` for ``022``/``023``/``026`` (no single-value check on a
    covered non-list field, no RFC 9530 duplicate-algorithm check, no A-label enforcement
    anywhere on the authority path). Filed upstream as SDK divergence #6.

    Spec grounding, stated honestly rather than implied:

    * the non-ASCII authority and the malformed-authority family are well grounded —
      ``url-canonicalization.mdx`` steps 2-3, MUST-reject, shared with
      :mod:`src.core.signing.canonical` so the rule has ONE definition;
    * ``negative/023`` rests on RFC 9530 §2's Dictionary semantics;
    * ``negative/021`` and ``negative/022`` are grounded ONLY in the vectors' own ``$comment``
      plus checklist step 1's bare "Reject if malformed" — no prose enumerates them. Do not
      cite prose support that is not there.

    Deliberately NARROW. A gate that rejected traffic the spec requires us to ACCEPT would be
    worse than the bug it closes, so anything it cannot prove malformed is handed to the SDK
    unchanged — including a ``Signature-Input`` it cannot parse, whose codes the SDK already
    gets right.
    """
    lines: dict[bytes, list[str]] = {}
    for raw_name, raw_value in raw_headers:
        lines.setdefault(raw_name.lower(), []).append(raw_value.decode("latin-1"))

    for name in _SINGLE_LINE_SIGNED_HEADERS:
        if len(lines.get(name, ())) > 1:
            _reject_headers(
                f"{name.decode()!r} arrived on {len(lines[name])} header lines; a covered field must have one"
            )

    for name, reason_for in _MALFORMED_VALUE_RULES:
        for value in lines.get(name, ()):
            reason = reason_for(value)
            if reason is not None:
                _reject_headers(f"{name.decode()!r} is malformed: {reason} — received {value!r}")

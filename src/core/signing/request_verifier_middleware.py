"""The ONE inbound RFC 9421 verifier — a pure-ASGI middleware over MCP, A2A and REST.

#1291 B1 (salesagent-z6nr.12).

Core invariant
--------------
Exactly one middleware, registered inside :class:`~src.core.auth_middleware.UnifiedAuthMiddleware`
and scoped by an allowlist of the three AdCP surfaces, decides every inbound signature
outcome — and it decides it by *choosing whether to call* the SDK's unconditional
``verify_request_signature`` and whether to swallow its exception. It never
reimplements a checklist step, never reorders one, and never reads a posture from
anywhere but the single per-request :class:`~src.core.signing.posture.RequestSigningPosture`
that D1 will also serialize to the wire.

``src/app.py`` is a single composition root — the MCP app is a Mount, the A2A routes
are appended to ``app.routes``, and the REST router is included — so one middleware
here really does cover all three transports. Three per-transport hooks would be the
duplication class CLAUDE.md's DRY invariant exists to prevent, and they would drift the
moment one transport's error mapping changed.

Why pure ASGI and not ``BaseHTTPMiddleware``
--------------------------------------------
``BaseHTTPMiddleware`` has the ContextVar propagation bug (Starlette #1729) and, more
importantly here, it sits on the RESPONSE path: it wraps the downstream app in a task
and pumps its response through a queue, which is what breaks MCP's streamable-HTTP and
SSE responses. This class touches the request and then gets out of the way — on the
pass-through and the warn paths it does not observe the response at all.

Placement (R-H2 — the half that actually breaks verification)
--------------------------------------------------------------
Execution order is CORS -> UnifiedAuth -> **verifier** -> RestCompat -> a2a messageId
-> router, and both halves are load-bearing:

* INSIDE ``UnifiedAuthMiddleware``, because the spec's composition rule decides the
  ``required_for`` rejection on whether the caller's bearer resolves to a principal we
  accept;
* OUTSIDE ``RestCompatMiddleware`` and the a2a messageId middleware, because BOTH
  REWRITE THE REQUEST BODY. ``RestCompatMiddleware`` sets ``request._body`` to
  normalized JSON for POST ``/api/v1/{products,media-buys,creatives/sync}``, and
  Starlette's ``_CachedRequest.wrapped_receive`` hands those bytes verbatim downstream —
  so a verifier placed inside it would hash bytes the signer never signed, and the
  collision lands on ``/api/v1/media-buys`` = ``create_media_buy``, the spend-committing
  operation the spec pushes toward ``covers_content_digest: "required"``.

``tests/unit/test_architecture_request_signature_middleware.py`` pins the order
structurally; ``tests/integration/test_request_signature_middleware.py`` grades the
body-bytes property behaviorally.

Per-request sequence
--------------------
1. Non-HTTP scope, non-AdCP path, or the kill switch off -> pass through untouched.
   The allowlist (not a denylist) is what permanently exempts A3's trust-root
   documents: a verifier in front of ``/.well-known/jwks.json`` is a bootstrap
   deadlock, because that document is how a counterparty obtains the key it would need
   in order to sign. ``tests/unit/test_architecture_request_signature_middleware.py``
   ties the allowlist to ``app.routes`` so a NEW AdCP surface cannot ship silently
   unverified (R-M4).
2. Buffer the body and NAME the request (#1291 B2, ``src/core/signing/operations.py``).
   Two of the three transports carry the operation IN the body and the webhook
   escalation lives there on all three, so the name cannot precede the bytes; the pair
   is skipped entirely while no tenant CAN declare a posture. R-H3's "the ``none``
   bucket costs nothing" survives for the half that means ``supported: false`` — a
   seller that does not verify at all. The other half, ``supported: true`` with the
   operation in no list, is a VERIFIER and the spec's pre-check binds it "even for
   operations not in ``required_for``" (``security.mdx`` :1226), so it costs a bounded
   in-memory header parse against an empty resolver. Never more: no key resolution, no
   crypto, no outbound walk, no session. The buffer is bounded
   and its replay is lossless in every exit, including over-cap — an unsigned request
   must reach its handler with every byte.
3. Thread hop #1: resolve the tenant -> its posture -> the bucket for that name, and
   (only when the decision needs it) the bearer -> ``Principal`` -> ``agent_url``. A
   request nothing could name is promoted to the strictest bucket the posture declares,
   so an unreadable shape cannot buy an exemption.
4. Signature headers absent -> the composition rule and the webhook escalation (below).
   Present -> the checklist, over the already-buffered bytes.
5. Async: the counterparty's :class:`~adcp.signing.agent_resolver.AgentResolution` from
   the process cache, resolved on a cold entry. The WHOLE resolution is cached — jwks +
   jwks_uri + key_origins — because ``expected_key_origins`` must be handed to every
   verify or the spec's step-7 key-origin check silently no-ops with a warning (R-M2).
6. Thread hop #2: the Postgres replay store and the synchronous
   ``verify_request_signature`` over ONE session checkout, per A4's wiring contract —
   except on the narrowed ``none`` pre-check, which runs the same call against the null
   dependency trio and checks out NO session at all
   (``src/core/signing/replay_store.py``). Called directly with method/url/headers/body
   rather than through ``verify_starlette_request`` (R-M3), because the wrapper derives
   the URL from the scope and would discard the explicit ``X-Forwarded-Proto``
   derivation.

Three-way header pre-check
--------------------------
Both headers absent, exactly one present, and both present are three different
outcomes, and only the third is a question the SDK can be asked: its
``_precheck_presence`` raises ``request_signature_required`` on the absent branch
UNCONDITIONALLY, which is the strict reading the spec normatively rejects. So:

* both absent -> the composition rule (``security.mdx`` @ v3.1.1 :1268-1271). An
  UNAUTHENTICATED request to a ``required_for`` operation is rejected; an unsigned but
  otherwise authenticated one MUST NOT be rejected for the missing signature. :1289
  names the failure the strict reading produces — "a seller enabling ``required_for``
  for operational monitoring would inadvertently 401 every bearer-authed buyer" — and
  salesagent is bearer-authenticated on every AdCP request, so the strict reading would
  reject essentially all production traffic the moment D1 populates ``required_for``.
* exactly one present -> hand it to the SDK, which raises
  ``request_signature_header_malformed``. A malformed signature blocks the bearer
  fallback regardless of the bucket (:1226, :1271): a present-but-broken signature
  signals signer intent and must not downgrade silently.
* both present -> the checklist runs.

One rejection sits OUTSIDE that rule, deliberately: security.mdx :1462-1465 requires a
signature on any request registering webhook credentials
(``push_notification_config.authentication`` or any
``accounts[].notification_configs[].authentication``), :1375 restating it as a trigger
"regardless of ``required_for`` membership". The composition rule's exemption cannot
apply — the rule exists BECAUSE the registering caller is normally bearer-authed and an
on-path mutator can inject or strip that block, so "valid bearer ⇒ don't reject" would
defeat it entirely. Compliance vector 027 cannot discriminate the two readings (its
bearer resolves to no principal, so it 401s under both);
``tests/integration/test_request_signature_operations.py`` grades the authenticated
case that does.

Rejections are a TRANSPORT-layer 401 carrying
``WWW-Authenticate: Signature error="<code>"`` (realm intentionally omitted), built by
the SDK's ``unauthorized_response_headers``. They sit at the ASGI boundary, outside the
``AdCPError`` -> wire-envelope cascade, and are SENT rather than raised: FastAPI's
exception handlers are inner, so a raise here becomes a 500 in ``ServerErrorMiddleware``.
Every code is spec-defined; this module invents none.

Spec grounding: AdCP 3.1.1 via ``adcp==6.6.0``;
``v3.1.1:docs/building/by-layer/L1/security.mdx`` (there is no ``dist/docs/3.1.1/`` at
that tag — ``dist/docs/`` stops at 3.1.0) and
``v3.1.1:dist/compliance/3.1.1/universal/signed-requests.yaml`` (12 positive / 28
negative vectors, graded on 2xx / 401 + the ``WWW-Authenticate`` code byte-for-byte).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from adcp.signing.agent_resolver import AgentResolution, AgentResolverError, async_resolve_agent
from adcp.signing.brand_authz import BrandAuthorizationResult, BrandJsonAuthorizationResolver
from adcp.signing.canonical import split_structured_field
from adcp.signing.errors import (
    REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON,
    REQUEST_SIGNATURE_BRAND_JSON_AMBIGUOUS,
    REQUEST_SIGNATURE_BRAND_JSON_MALFORMED,
    REQUEST_SIGNATURE_BRAND_JSON_UNREACHABLE,
    REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING,
    REQUEST_SIGNATURE_BRAND_ORIGIN_MISMATCH,
    REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_JWKS_UNAVAILABLE,
    REQUEST_SIGNATURE_JWKS_UNTRUSTED,
    REQUEST_SIGNATURE_REQUIRED,
    SignatureVerificationError,
)
from adcp.signing.etld import host_from
from adcp.signing.jwks import JwksResolver, StaticJwksResolver
from adcp.signing.middleware import unauthorized_response_headers
from adcp.signing.replay import ReplayStore
from adcp.signing.revocation import RevocationChecker
from adcp.signing.verifier import (
    VerifiedSigner,
    VerifierCapability,
    VerifyOptions,
    parse_signature_input_header,
    verify_request_signature,
)
from starlette.responses import Response

from src.core.auth_context import AUTH_CONTEXT_STATE_KEY
from src.core.config import CounterpartyRegistryEntry, SigningConfig, get_config
from src.core.database.database_session import get_db_session
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.replay_nonce import ReplayNonceRepository
from src.core.http_utils import headers_from_asgi_scope, path_from_asgi_scope
from src.core.metrics import record_request_unsigned, record_signature_failed, record_signature_verified

# ``_detect_tenant`` is the Host/x-adcp-tenant resolution ladder every transport
# boundary already uses, and it is the right call here precisely because it never
# reads the token: it cannot 401 for an auth reason. ``resolve_identity`` would —
# it validates the credential and raises, which at this layer would turn an auth
# failure into a signature rejection.
from src.core.resolved_identity import _detect_tenant, _extract_auth_token
from src.core.signing.operations import (
    UNNAMED_OPERATION,
    OperationResolver,
    RegistryOperationResolver,
    ResolvedOperation,
    is_adcp_surface,
)
from src.core.signing.posture import (
    PostureBucket,
    PostureTenant,
    RequestSigningPosture,
    posture_for_tenant,
    request_signing_is_declarable,
)
from src.core.signing.replay_store import PostgresReplayStore
from src.core.signing.revocation import checker_for
from src.core.signing_contract.canonical import malformed_authority_reason, reject_malformed_target

logger = logging.getLogger(__name__)

Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

#: Process-level ``{agent_url: AgentResolution}``. The WHOLE resolution is kept, not
#: just the JWKS: ``expected_key_origins`` comes from it and is mandatory on every
#: verify for brand-json-sourced keys (R-M2). Entries expire by
#: ``SigningConfig.agent_resolution_ttl_seconds`` against ``AgentResolution.fetched_at``.
AGENT_RESOLUTION_CACHE: dict[str, AgentResolution] = {}

#: Process-level ``{brand_json_url: BrandJsonAuthorizationResolver}`` (#1291 hksr,
#: Tier 3). Keyed by ``brand_json_url`` rather than ``agent_url`` because multiple
#: agents can share one brand.json; built once per document and reused, mirroring
#: :data:`AGENT_RESOLUTION_CACHE`'s reuse pattern. The resolver's own internal
#: ``_BrandJsonFetcher`` handles staleness/refresh on subsequent ``.check()`` calls.
_BRAND_AUTHZ_RESOLVER_CACHE: dict[str, BrandJsonAuthorizationResolver] = {}


@dataclass(frozen=True)
class _ResolutionFailure:
    """When a counterparty's walk last failed, and which spec code it mapped to.

    The code travels WITH the failure (rather than being recomputed) so a
    request arriving inside the refetch cooldown answers the SAME discovery
    code as the request that triggered it, instead of silently degrading to
    the generic ``key_unknown`` once the failure is no longer fresh.
    """

    at: float
    code: str


#: ``{agent_url: last failure}``. Without it, every signed request from a
#: counterparty with a broken brand.json starts a fresh 3-hop outbound walk.
_RESOLUTION_FAILURES: dict[str, _ResolutionFailure] = {}

#: The purpose key under the counterparty's ``identity.key_origins`` map.
_SIGNING_PURPOSE = "request_signing"


@dataclass(frozen=True)
class _CounterpartyResolution:
    """A resolved (or unresolved) counterparty, tagged with WHERE it came from.

    The tag is load-bearing, not descriptive (#1291 hksr / B4 interaction):
    Tier 3 (brand authorization, below) is scoped to brand-json-WALKED
    counterparties only — there is no brand.json to authorize a
    registry-sourced counterparty (B4, salesagent-z6nr.15) against, since its
    resolution never came from a real, fetchable document.

    "Was this built by ``build_registry_resolution``?" is NOT a safe proxy for
    this distinction: ``tests/helpers/signing.counterparty_key`` deliberately
    seeds a WALKED counterparty's cache entry through that same constructor
    (its own docstring explains why — one production shape, not two), so a
    constructor-based check would silently exempt every counterparty the
    signing test suites seed, not just B4's registry fallback.
    """

    resolution: AgentResolution | None
    source: Literal["walk", "registry"]


class _BrandJsonJwksResolver(StaticJwksResolver):
    """A resolver that tells the verifier its keys came from the brand.json walk.

    The SDK engages the spec's step-7 key-origin consistency check ONLY for resolvers
    advertising ``jwks_source = "brand_json"`` and exposing the resolved ``jwks_uri``;
    a plain :class:`~adcp.signing.jwks.StaticJwksResolver` is treated as a
    publisher-pinned tuple and skips the check with nothing but a warning. Declaring
    the conformance is documented adopter API (``adcp.signing.BrandSourcedJwksResolver``).
    """

    jwks_source = "brand_json"

    def __init__(self, jwks: dict[str, Any], *, jwks_uri: str) -> None:
        super().__init__(jwks)
        self.jwks_uri = jwks_uri


@dataclass(frozen=True)
class _RequestContext:
    """What thread hop #1 resolved: the seller's posture and the caller's identity."""

    posture: RequestSigningPosture
    bucket: PostureBucket
    tenant_id: str | None
    principal_id: str | None
    agent_url: str | None

    @property
    def authenticated(self) -> bool:
        """Whether the bearer resolved to a principal this deployment accepts.

        This is the third term of the spec's three-way AND at :1224 — "…AND the caller
        presents no other credential the verifier accepts" — and the only thing that
        separates the 401 branch from the pass-through branch on an unsigned request.
        """
        return self.principal_id is not None


@dataclass(frozen=True)
class _BufferedBody:
    """A fully-read request body plus a ``receive`` that replays it downstream."""

    body: bytes
    receive: Receive
    complete: bool
    over_cap: bool


class RequestSignatureMiddleware:
    """Verify inbound RFC 9421 signatures on the AdCP surfaces. See the module docstring."""

    def __init__(self, app: Any, *, operation_resolver: OperationResolver | None = None) -> None:
        self.app = app
        # B2 (``salesagent-z6nr.13``) swapped the inert seam for the registry-derived
        # resolver, so the default is the production one and the argument stays for
        # tests that want naming out of the picture.
        self._operations: OperationResolver = operation_resolver or RegistryOperationResolver()

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        config = get_config().signing
        if scope["type"] != "http" or not config.verifier_enabled or not _is_adcp_surface(scope):
            await self.app(scope, receive, send)
            return

        headers = headers_from_asgi_scope(scope)
        signed = "signature" in headers or "signature-input" in headers
        buffered, resolved = await self._name_request(config, scope, headers, receive, body_needed=signed)

        if resolved.signature_forced:
            # security.mdx @ v3.1.1 :1464 — "Sellers MUST log every request that arrives with
            # a non-empty ``authentication`` block." Per REQUEST, and unqualified by posture:
            # :1465 sends a seller that cannot ENFORCE the signing requirement here rather
            # than exempting it, so this fires whatever this tenant declared.
            #
            # HERE, and not in ``_credentials_force_a_signature`` (:702), which is
            # ``resolved.signature_forced and posture.supported`` — unreachable for exactly
            # the ``supported: false`` seller :1465 routes to the log. The posture is not
            # readable at this point either (``_resolve_request_context`` runs below), so the
            # message deliberately does not name it.
            #
            # The outbound twin is ``_log_legacy_registration``
            # (``webhook_sender_factory.py``), which observes OUR sender choosing a legacy
            # mode. Different direction, different event; that one does not discharge this.
            logger.warning(
                "Inbound request carries a non-empty webhook authentication block "
                "(push_notification_config or accounts[].notification_configs): operation=%r "
                "signed=%s. Legacy HMAC was selected by the buyer rather than RFC 9421 — alarm "
                "on this if the buyer expected 9421 (security.mdx @ v3.1.1 :1464).",
                resolved.operation or resolved.protocol_method or "<unnamed>",
                signed,
            )

        # The hop is unconditional even though the resolver often does no I/O at all
        # (see below): whether it blocks depends on the posture it reads, and the event
        # loop must not be the thing that finds out it guessed wrong.
        context = await asyncio.to_thread(
            _resolve_request_context,
            headers=headers,
            token=_bearer_token(scope, headers),
            resolved=resolved,
            signed=signed,
        )

        if not signed:
            await self._handle_unsigned(context, resolved, scope, buffered, send)
            return

        # NO ``none`` short-circuit here. The spec's pre-check binds every VERIFIER,
        # "even for operations not in ``required_for``" (:1226), so a signed request
        # whose operation buckets as ``none`` must still clear header well-formedness.
        # ``_handle_signed`` owns that decision now, and gates it on
        # ``posture.supported`` — a seller declaring ``supported: false`` is not a
        # verifier at all and keeps its conformant pass-through.
        await self._handle_signed(context, resolved, headers, config, scope, buffered, send)

    async def _name_request(
        self,
        config: SigningConfig,
        scope: dict[str, Any],
        headers: Mapping[str, str],
        receive: Receive,
        *,
        body_needed: bool,
    ) -> tuple[_BufferedBody, ResolvedOperation]:
        """Buffer the body and NAME the request — the step B2 put ahead of the posture.

        Two of the three transports carry the operation in the body and the payload
        escalation (security.mdx :1462-1465) lives in the body on all three, so naming
        cannot happen before buffering. Both are skipped while no tenant CAN declare a
        posture (:func:`~src.core.signing.posture.request_signing_is_declarable`), which
        is R-H3's rule restated one step earlier: under an undeclarable block every
        bucket is ``none`` and neither the name nor the bytes could change an outcome.

        The gate skips the BUFFER and the RESOLVE, never the pass-through (refinement
        R-H1): ``posture_for_tenant`` is still consulted unconditionally, because it is
        the seam the tests substitute — short-circuiting ahead of it would make the
        whole enforcement ladder pass vacuously.

        ``body_needed`` keeps the signed path buffered even under an undeclarable
        block. It costs a bounded read on a request that already carries signature
        headers, and it buys the property structurally rather than as an invariant
        spanning two modules: the checklist can never be handed an empty body because
        the buffer was skipped. The three expensive things R-H3 named — the tenant
        read, the principal read and the Ed25519 verify — all still sit behind the
        bucket.
        """
        if not (body_needed or request_signing_is_declarable()):
            return _unbuffered(receive), UNNAMED_OPERATION

        buffered = await _buffer_body(receive, config.max_signed_body_bytes)
        # An over-cap body is truncated for NAMING purposes only (the replay downstream
        # is lossless either way), and a truncated body names nothing -> unresolvable ->
        # the strictest bucket the posture declares. Fails closed.
        return buffered, self._operations.resolve(scope, headers, buffered.body)

    # -- branches ----------------------------------------------------------

    async def _handle_unsigned(
        self,
        context: _RequestContext,
        resolved: ResolvedOperation,
        scope: dict[str, Any],
        buffered: _BufferedBody,
        send: Send,
    ) -> None:
        """No signature headers: the composition rule (security.mdx :1268-1269).

        The SDK cannot decide this — ``_precheck_presence`` raises on the absent branch
        whatever the bucket says — so the rejection is built here from the SDK's own
        error type and code constant.

        Everything downstream of here gets ``buffered.receive``: the body has already
        been drained by the time this runs, so forwarding the raw channel would hand
        the app an exhausted one (R-H2).
        """
        operation = resolved.operation
        if context.bucket == "required" and not context.authenticated:
            await self._reject_unsigned(
                f"operation {operation!r} requires a signature and the caller presented "
                "no other credential this agent accepts",
                operation,
                scope,
                buffered,
                send,
            )
            return

        # security.mdx :1462-1465 — webhook credentials in the payload force a
        # signature "regardless of required_for membership" (:1375), and NOT subject to
        # the composition rule above: the rule exists precisely because the registering
        # request is normally bearer-authed and an on-path mutator can inject or strip
        # the ``authentication`` block, so exempting authenticated callers would defeat
        # it entirely.
        if _credentials_force_a_signature(context.posture, resolved):
            await self._reject_unsigned(
                "the request registers webhook credentials (push_notification_config or "
                "accounts[].notification_configs authentication), which this agent requires "
                "to be signed (security.mdx :1462-1465)",
                operation,
                scope,
                buffered,
                send,
            )
            return

        record_request_unsigned(operation, "absent")
        await self.app(scope, buffered.receive, send)

    async def _reject_unsigned(
        self,
        message: str,
        operation: str,
        scope: dict[str, Any],
        buffered: _BufferedBody,
        send: Send,
    ) -> None:
        """The one ``request_signature_required`` rejection both unsigned rules emit."""
        record_signature_failed(operation, REQUEST_SIGNATURE_REQUIRED)
        await _reject(
            SignatureVerificationError(REQUEST_SIGNATURE_REQUIRED, step=0, message=message),
            scope,
            buffered.receive,
            send,
        )

    async def _handle_signed(
        self,
        context: _RequestContext,
        resolved: ResolvedOperation,
        headers: Mapping[str, str],
        config: SigningConfig,
        scope: dict[str, Any],
        buffered: _BufferedBody,
        send: Send,
    ) -> None:
        """At least one signature header present: run the pre-check, then the checklist."""
        operation = resolved.operation
        # ``bucket_for`` collapses TWO situations into ``none``: a seller declaring
        # ``supported: false``, which is not a verifier at all, and ``supported: true``
        # with the operation in none of the three lists, which IS one. Only the second is
        # bound by the spec's pre-check, and the storyboard gates all 28 negative vectors
        # on ``request_signing.supported: true`` alone. So the gate reads ``supported``,
        # never the bucket — reading the bucket would keep the defect for every
        # ``supported: true`` operation outside the lists, the larger half.
        if not context.posture.supported:
            record_request_unsigned(operation, "ignored")
            await self.app(scope, buffered.receive, send)
            return
        if buffered.over_cap:
            logger.warning("Signed request body exceeded %d bytes; rejecting", config.max_signed_body_bytes)
            await Response(status_code=413)(scope, buffered.receive, send)
            return
        if not buffered.complete:
            # The client disconnected mid-body. There is nothing to verify and nothing
            # to answer; hand the disconnect downstream and let the app unwind.
            # The narrowed ``none`` bucket still counts here: this is one of its
            # pass-through exits, and the counter it used to get upstream (before the
            # phase split moved the decision into this method) must not disappear.
            if context.bucket == "none":
                record_request_unsigned(operation, "ignored")
            await self.app(scope, buffered.receive, send)
            return

        try:
            # Step 1 first, and over the RAW header list: the shapes it refuses are
            # invisible once the headers collapse into a dict, and refusing them here
            # also spares an unresolved counterparty a three-hop outbound walk on a
            # request that cannot be accepted. ``_resolution_for`` never raises this
            # exception — it is inside the block so there is ONE handler, not two.
            _strict_header_precheck(scope)
            # The layer's target-URI gate, in front of the SDK verifier (the
            # "boundary wrap" of salesagent-z6nr.33): the SDK's 6.6.0 verifier
            # canonicalizes internally WITHOUT the vendored upstream fixes, so the
            # layer gates the effective request URI with its own comparer semantics
            # first. Every divergence between the vendored and the SDK canonicalizer
            # is thereby either rejected here with the graded code, or fails CLOSED
            # inside the SDK as a signature-base mismatch — never accepted. On the
            # ASGI path the server has already resolved most malformed shapes, so
            # this gate is defense-in-depth, not a hot path.
            url = _verify_url(scope, headers)
            # PRE-CHECK ONLY for the narrowed ``none`` bucket. The spec's pre-check
            # bullets cover the ``required_for`` unsigned case and the two header rules
            # and say NOTHING about target-URI malformation, so hoisting
            # ``reject_malformed_target`` would make ``none`` reject beyond the
            # requirement. Skipping ``_resolution_for`` is the load-bearing half: it runs
            # AHEAD of the SDK and is gated by no step, so leaving it in would let anyone
            # name an unknown operation, attach two plausible headers, and force a
            # three-hop outbound walk whose result is then discarded.
            precheck_only = context.bucket == "none"
            if precheck_only:
                counterparty = _CounterpartyResolution(None, source="registry")
            else:
                reject_malformed_target(url)
                counterparty = await _resolution_for(context.agent_url, config, keyid=_parse_keyid(headers))
            signer = await asyncio.to_thread(
                _run_verifier,
                method=scope.get("method", "GET"),
                url=url,
                headers=headers,
                body=buffered.body,
                capability=context.posture.to_verifier_capability(),
                operation=operation,
                bucket=context.bucket,
                resolution=counterparty.resolution,
                agent_url=context.agent_url,
                config=config,
                precheck_only=precheck_only,
            )
            # Tier 3 (#1291 hksr): a valid signature proves WHO signed, never that the
            # signer may act for the brand. Only for brand-json-WALKED counterparties —
            # B4's registry fallback (source == "registry") has no real brand.json to
            # check against and must never reach this call.
            if counterparty.source == "walk" and counterparty.resolution is not None:
                await _check_brand_authorization(counterparty.resolution, config)
        except SignatureVerificationError as exc:
            await self._handle_rejection(exc, context, operation, scope, buffered.receive, send)
            return

        record_signature_verified(operation, signer.key_id)
        await self.app(scope, buffered.receive, send)

    async def _handle_rejection(
        self,
        exc: SignatureVerificationError,
        context: _RequestContext,
        operation: str,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        """The ONE owner of rejection policy: the spec's two phases, as one branch.

        AdCP 3.1.1 ``security.mdx`` puts header well-formedness in a PRE-CHECK above the
        operation bucket and signature validity in a checklist inside it (:1226, and
        :1273 scoping ``warn_for`` to signed-but-invalid). The SDK collapses presence and
        parse into a single call (``verifier.py:185-213``), so the phase boundary cannot
        be drawn by ordering two calls. It is drawn on WHICH EXCEPTION bypasses the warn
        arm — ``is_precheck`` below — and it lives here rather than around the funnel so
        the metric above it still fires for the class it makes newly rejectable.

        Warn mode is OURS: ``VerifierCapability`` carries 4 of ``request_signing``'s 8
        properties and 2 of its 6 operation buckets, so handing ``warn_for`` to the SDK
        would silently drop it. It is implemented the only way it can be — call the
        verifier, catch, emit the metric, continue — and it is observable on the WIRE
        (200 where ``supported_for`` answers 401), not merely in a counter.
        """
        # The pre-check family, and the only failure class that rejects in EVERY bucket.
        # The pair, never the bare code: ``request_signature_header_malformed`` is raised
        # at five different steps, and steps 2/5/6/8 are checklist failures on a
        # WELL-FORMED header, which :1273 keeps warn-suppressible.
        is_precheck = exc.code == REQUEST_SIGNATURE_HEADER_MALFORMED and exc.step == 1
        # Suppressed for exactly one case: the narrowed ``none`` bucket verifies against
        # an EMPTY resolver, so its step-7 ``key_unknown`` is ENGINEERED by us and would
        # otherwise be indistinguishable from a real key-resolution failure in the same
        # series. A step-1 malformation there is genuine, so ``is_precheck`` keeps it —
        # without that limb A1 would return below and the request would 401 silently.
        if context.bucket != "none" or is_precheck:
            record_signature_failed(operation, exc.code)
        if is_precheck:
            await _reject(exc, scope, receive, send)
            return
        if context.bucket == "warn":
            logger.warning(
                "Request signature failed in warn mode (not rejecting): code=%s step=%s "
                "operation=%r principal=%r tenant=%r",
                exc.code,
                exc.step,
                operation,
                context.principal_id,
                context.tenant_id,
            )
            await self.app(scope, receive, send)
            return
        if context.bucket == "none":
            # Pass-through, and the series stays alive: the request genuinely IS ignored
            # (it reached no checklist), and a metric that vanishes at a deploy reads as
            # traffic stopping.
            record_request_unsigned(operation, "ignored")
            await self.app(scope, receive, send)
            return
        await _reject(exc, scope, receive, send)


# ---------------------------------------------------------------------------
# Path scoping
# ---------------------------------------------------------------------------


def _is_adcp_surface(scope: Mapping[str, Any]) -> bool:
    """Whether this request targets one of the AdCP protocol surfaces.

    ADAPTS the ASGI scope to a path and asks the ONE published predicate
    (:func:`src.core.signing.is_adcp_surface`, defined beside
    ``ADCP_SURFACE_PREFIXES`` in :mod:`src.core.signing.operations`). It holds no
    boundary rule of its own on purpose: a second copy here is what let the segment
    boundary be rewritten while the structural guard graded its own intact copy.
    """
    return is_adcp_surface(path_from_asgi_scope(scope))


# ---------------------------------------------------------------------------
# Phase 1 — posture and caller identity (one thread hop)
# ---------------------------------------------------------------------------


def _bearer_token(scope: Mapping[str, Any], headers: Mapping[str, str]) -> str | None:
    """The caller's token, from the auth context ``UnifiedAuthMiddleware`` already set.

    Falls back to extracting it from the headers with the same shared rule, so that a
    verifier accidentally moved OUTSIDE the auth middleware degrades to doing the work
    twice rather than to treating every caller as unauthenticated — which would 401
    real traffic under ``required_for``.
    """
    auth_context = (scope.get("state") or {}).get(AUTH_CONTEXT_STATE_KEY)
    if auth_context is not None:
        return auth_context.auth_token
    return _extract_auth_token(dict(headers))[0]


def _fail_closed_bucket(posture: RequestSigningPosture, bucket: PostureBucket) -> PostureBucket:
    """Promote a request nothing could NAME to the strictest bucket *posture* declares.

    Plan step 5's anti-bypass: without it, an attacker who finds a shape the resolver
    cannot read gets ``bucket_for("", None)`` = ``none`` and skips enforcement
    entirely — the silent-unverified failure this whole ticket exists to remove.

    ``required`` only when the tenant actually declares a required bucket; an agent
    that requires nothing must not start requiring signatures for malformed traffic.
    A tenant that supports nothing (``supported: false``) keeps its ``none``, because
    an agent that does not verify signatures cannot demand one.
    """
    if not posture.supported:
        return bucket
    if posture.required_for or posture.protocol_methods_required_for:
        return "required"
    return bucket


def _credentials_force_a_signature(posture: RequestSigningPosture, resolved: ResolvedOperation) -> bool:
    """Does this request hand over webhook credentials to a seller that CAN verify?

    security.mdx @ v3.1.1 :1462-1465, restated at :1375 as a trigger that fires
    "regardless of ``required_for`` membership". Bounded by ``supported`` and by nothing
    else, because that is exactly where :1465 draws the line: "Sellers that do not
    support request signing at all have no way to enforce this rule and fall back to the
    log-and-alarm posture — 3.0 migration note, not an exemption."

    A per-OPERATION bucket is NOT that line, and reading it as one is how the A2A
    protocol-configuration channel stayed open after the resolver learned to see it:
    :meth:`~src.core.signing.posture.RequestSigningPosture.bucket_for` grades a request
    named in the PROTOCOL namespace against the ``protocol_methods_*`` trio, which
    defaults to empty lists, so every JSON-RPC method on a signing-capable seller lands
    in ``none``. ``tasks/pushNotificationConfig/set`` is such a method, and it persists
    the credentials it is handed.
    """
    return resolved.signature_forced and posture.supported


def _detect_tenant_for_posture(headers: dict[str, str]) -> tuple[str | None, PostureTenant | None]:
    """The seller's tenant, or ``(None, None)`` if it cannot be read at all.

    ``_detect_tenant`` opens a database session. Since #1291 D1 made ``request_signing``
    declarable that read happens on EVERY AdCP request, and this function runs in an
    ``asyncio.to_thread`` hop with no envelope translation above it (``src/app.py`` mounts
    the verifier under ``UnifiedAuthMiddleware``/CORS only, and ``_reject`` emits a
    signature-specific 401, not a general AdCP error). So an uncaught raise here would
    answer get_products, create_media_buy and everything else with a bare 500 — a
    Postgres blip taking down every protocol surface because signatures are OPTIONAL for
    this deployment.

    Falling back to no tenant means :func:`posture_for_tenant` returns the AGENT-level
    posture, which enforces nothing per-tenant. State the security consequence rather than
    hiding it: for the interval of the failure, a tenant that declared ``required_for``
    stops having it required. That is the same trade ``posture_for_tenant`` already makes
    for an unreadable declaration and for the same reason — the middleware must not INVENT
    enforcement it cannot read, and the loud alternative (401 on every AdCP surface
    whenever the tenant lookup hiccups) converts an infrastructure blip into an outage.
    ``_fail_closed_bucket`` is unaffected: it covers a READABLE posture plus an unnameable
    request.

    A bare ``Exception`` here, deliberately, unlike ``posture_for_tenant``'s
    ``AdCPError``-only catch: this is an INFRASTRUCTURE read whose failure modes are the
    driver's, not ours, and the WARNING carries them.
    """
    try:
        tenant_id, detected = _detect_tenant(headers)
        # PROJECTED at the boundary, not cast (#1879). ``_detect_tenant`` lives in
        # src/core/resolved_identity.py and returns an untyped dict; that function is
        # identity resolution and belongs to #1870, so its signature is not ours to change.
        # Naming the two keys the posture actually reads states the shape HERE, where the
        # value crosses into the signing layer, and leaves the ORM boundary where #1870
        # will find it. A cast would assert the shape; this one carries it.
        tenant = (
            PostureTenant(
                tenant_id=str(detected.get("tenant_id") or ""),
                capability_declarations=detected.get("capability_declarations"),
            )
            if detected
            else None
        )
        return tenant_id, tenant
    except Exception as exc:
        logger.warning(
            "Could not resolve the seller tenant for signature-posture purposes, so no "
            "per-tenant request_signing posture is enforced for this request: %s",
            exc,
        )
        return None, None


def _resolve_request_context(
    *,
    headers: Mapping[str, str],
    token: str | None,
    resolved: ResolvedOperation,
    signed: bool,
) -> _RequestContext:
    """Resolve the seller's posture and, when the decision needs it, the caller.

    Runs in one thread hop because both lookups are synchronous DB work — and does
    NEITHER unless the answer can change an outcome. That is R-H3's rule applied to the
    whole middleware rather than just to the buffering step.

    * the tenant is read only while a posture is DECLARABLE
      (:func:`~src.core.signing.posture.request_signing_is_declarable`). That is True
      since #1291 D1 backed the block, so the read is now paid on every AdCP request —
      the cost R-H3 gated deliberately, now spent on purpose because a declared posture
      is the thing being enforced. It is read through
      :func:`_detect_tenant_for_posture`, which is where the failure mode is decided;
    * the principal is read only when its answer matters: on a signed request that will
      actually be verified (its ``agent_url`` is the key-resolution input), or on an
      unsigned request to a ``required_for`` operation (the composition rule's
      credential test).
    """
    tenant_id, tenant = _detect_tenant_for_posture(dict(headers)) if request_signing_is_declarable() else (None, None)
    posture = posture_for_tenant(tenant)
    bucket = posture.bucket_for(resolved.operation, resolved.protocol_method)
    if not resolved.resolvable:
        bucket = _fail_closed_bucket(posture, bucket)
    if _credentials_force_a_signature(posture, resolved):
        # ":1375 regardless of ``required_for`` membership" promotes the REQUEST, not
        # one branch of one handler. Doing it here is what keeps the escalation from
        # being bypassable by simply ATTACHING signature headers: a request whose bucket
        # stayed ``none`` is waved through unverified below (the "ignored" arm), so an
        # escalation enforced only on the unsigned branch would refuse the honest
        # unsigned registration and admit the same registration carrying a junk
        # ``Signature``. Promoted, the credential-carrying request is verified on the
        # signed path and refused on the unsigned one, which is the whole MUST.
        bucket = "required"

    needs_principal = (signed and bucket != "none") or (not signed and bucket == "required")
    if not needs_principal or not token:
        return _RequestContext(posture=posture, bucket=bucket, tenant_id=tenant_id, principal_id=None, agent_url=None)

    if tenant_id is None:
        # Deferred above, but the credential test must stay tenant-scoped: a token is
        # unique deployment-wide, yet a principal of tenant A must not authenticate
        # against tenant B's virtual host.
        tenant_id = _detect_tenant(dict(headers))[0]

    with get_db_session() as session:
        principal = PrincipalRepository(session).get_by_token(token, tenant_id)
        principal_id = principal.principal_id if principal else None
        agent_url = principal.agent_url if principal else None

    if signed and principal is not None and not agent_url:
        # Plan step 3: no onboarding record of this counterparty's agent URL means no
        # brand.json to walk and therefore no key. The checklist still runs — a
        # malformed signature must still be rejected (:1226) — and reaches
        # ``request_signature_key_unknown`` on its merits at step 7.
        logger.warning(
            "Principal %r (tenant %r) signed a request but has no agent_url on record; "
            "no signing key can be resolved for it",
            principal_id,
            tenant_id,
        )

    return _RequestContext(
        posture=posture, bucket=bucket, tenant_id=tenant_id, principal_id=principal_id, agent_url=agent_url
    )


# ---------------------------------------------------------------------------
# Body buffering
# ---------------------------------------------------------------------------


def _unbuffered(receive: Receive) -> _BufferedBody:
    """The identity element: nothing was read, so the raw channel IS the replay."""
    return _BufferedBody(body=b"", receive=receive, complete=True, over_cap=False)


async def _buffer_body(receive: Receive, max_bytes: int) -> _BufferedBody:
    """Read the body up to *max_bytes*, and return a ``receive`` that replays it whole.

    The downstream app builds its own ``Request`` from the same scope, so the receive
    channel this middleware drains is the SAME one the app will read — the SDK
    wrapper's docstring claim that Starlette caches the body for downstream handlers is
    wrong; that cache lives on the middleware's own ``Request`` instance.

    LOSSLESS in every exit (R-H2). B2 moved this ahead of the signed/unsigned split, so
    it now bounds UNSIGNED traffic too, and the previous over-cap branch discarded the
    chunks it had already consumed — harmless only while its one caller 413'd
    immediately. On the unsigned path that same branch would have been either a 413
    this middleware has never issued on unsigned traffic, or a handler receiving a
    destroyed body. So every byte read is replayed, and ``more_body`` on the replayed
    message tells the app whether to keep reading the rest off the raw channel:

    * whole body read       -> ``complete``, one final message;
    * cap hit on the last chunk -> ``over_cap`` AND ``complete`` — the body is all here,
      it is merely too big to HASH;
    * cap hit mid-body      -> ``over_cap``, ``more_body=True``, remainder streams
      through untouched;
    * client disconnected   -> what arrived, then the disconnect, which must still
      reach the app (a one-shot closure returning a fixed message would swallow it).
    """
    chunks: list[bytes] = []
    size = 0
    trailing: list[MutableMapping[str, Any]] = []
    complete = False
    over_cap = False
    more_body = True

    while more_body:
        message = await receive()
        if message["type"] == "http.disconnect":
            trailing.append(message)
            break
        chunk = bytes(message.get("body", b""))
        chunks.append(chunk)
        size += len(chunk)
        more_body = bool(message.get("more_body", False))
        if size > max_bytes:
            over_cap = True
            complete = not more_body
            break
    else:
        complete = True

    body = b"".join(chunks)
    pending: list[MutableMapping[str, Any]] = []
    if chunks or complete:
        pending.append({"type": "http.request", "body": body, "more_body": not complete})
    pending.extend(trailing)

    async def replay() -> MutableMapping[str, Any]:
        if pending:
            return pending.pop(0)
        return await receive()

    return _BufferedBody(body=body, receive=replay, complete=complete, over_cap=over_cap)


# ---------------------------------------------------------------------------
# Phase 2 — the counterparty's key material
# ---------------------------------------------------------------------------


def build_registry_resolution(entry: CounterpartyRegistryEntry) -> AgentResolution:
    """The :class:`AgentResolution` a configured counterparty registry entry projects to.

    #1291 B4. Same shape the brand.json walk produces below — ``jwks_uri`` and
    ``key_origins`` consistent with each other — so :func:`_jwks_resolver` marks
    it ``brand_json`` and the spec's step-7 key-origin consistency check stays
    engaged for a registry-resolved counterparty exactly as it is for a walked
    one. The four subscripts below cannot raise ``KeyError``:
    :class:`~src.core.config.CounterpartyRegistryEntry` is the parameter's type
    and the setting's, so an entry missing one of them is refused at config
    load, where an operator sees it, and never reaches a request.
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
    """The keyid named in the (unverified) Signature-Input header, or None.

    Used ONLY to look up the counterparty registry fallback below — the
    signature must still cryptographically verify against whatever key this
    resolves to, so reading an unverified header here is not a bypass; it is
    exactly as safe as any other keyid-based key lookup (the SDK's own
    checklist resolves a JWKS entry off the same unverified field).
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


async def _resolution_for(
    agent_url: str | None, config: SigningConfig, *, keyid: str | None = None
) -> _CounterpartyResolution:
    """The counterparty's cached resolution, resolving on a cold entry, tagged by source.

    ``agent_url -> capabilities -> identity.brand_json_url -> brand.json agents[] ->
    jwks_uri -> JWKS`` is a three-hop outbound walk, so it is done once per counterparty
    per TTL and never per request. Awaited HERE, before the synchronous verify hop:
    the walk is async, the checklist is not.

    A failure is not a rejection — it returns whatever is cached (possibly nothing) and
    lets the checklist decide, carrying the MAPPED discovery code alongside it in
    ``_RESOLUTION_FAILURES`` (#1291 hksr) so :func:`_jwks_resolver` can raise the
    spec-assigned code at step 7 instead of the generic ``key_unknown``. Mapping a
    resolver failure straight to a 401 HERE would make an unreachable counterparty
    outrank a malformed signature, which is the wrong error and the wrong step — the
    deferral into the resolver callable is what keeps that ordering correct.

    The SSRF pin stays at the SDK default: the walk follows a URL that ultimately came
    from a counterparty document, and ``allow_private_destinations`` is a test argument
    (``tests/unit/test_architecture_no_private_destinations.py`` fails the build on any
    src/ call site that passes it).

    #1291 B4 — the registry fallback: when *agent_url* is falsy (no bearer resolved a
    principal, or an onboarded principal recorded none — exactly what a bearer-less
    conformance runner produces), *keyid* is looked up in
    ``config.counterparty_registry`` as a FALLBACK key-trust source. This branch is
    the ONLY place the registry is consulted. A principal that DOES carry an
    ``agent_url`` never reaches it, even when that walk fails below and returns
    ``cached`` (possibly ``None``) — the registry is a fallback for a walk with no
    INPUT, never an override for a walk that FAILED, or a counterparty with a
    briefly unreachable brand.json would be silently re-identified from config.
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
        resolution = await async_resolve_agent(
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

    #1291 hksr. ``adcp/signing/errors.py``'s "brand.json discovery chain" block gives
    each hop of the 3-hop walk its own code precisely so a caller can tell a retryable
    transport failure (``*_unreachable``) apart from a misconfiguration
    (``*_missing`` / ``*_malformed`` / ``*_mismatch``) — collapsing all of them onto
    ``request_signature_key_unknown`` (the pre-hksr behavior) erases that distinction
    and gives a counterparty with a briefly unreachable capabilities endpoint the wrong
    diagnosis AND the wrong retry advice.
    """
    if exc.code == "invalid_agent_url":
        # Trust-boundary rejection (URL wouldn't canonicalize / SSRF-banned host) —
        # matches the SDK's own reference verify_from_agent_url choice for this ONE
        # code, even though that function's blanket JWKS_UNAVAILABLE treatment of
        # everything else is exactly the disease this ticket fixes.
        return REQUEST_SIGNATURE_JWKS_UNTRUSTED
    if exc.code == "capabilities_unreachable":
        return REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE
    if exc.code == "capabilities_invalid":
        # Step 2 reads identity.brand_json_url off the capabilities body; a body we
        # cannot parse at all means that field was never obtainable either.
        return REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING
    if exc.code == "brand_json_url_missing":
        return REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING
    if exc.code == "jwks_fetch_failed":
        return REQUEST_SIGNATURE_JWKS_UNAVAILABLE
    if exc.code == "brand_json_resolution_failed":
        return _map_brand_json_resolver_error(exc.__cause__)
    logger.warning("Unmapped AgentResolverError code %r; defaulting to JWKS_UNAVAILABLE", exc.code)
    return REQUEST_SIGNATURE_JWKS_UNAVAILABLE


def _map_brand_json_resolver_error(cause: BaseException | None) -> str:
    """``BrandJsonResolverError.code`` -> the ``request_signature_*`` code security.mdx assigns it.

    Shared by :func:`_map_agent_resolver_error` (reading ``exc.__cause__`` off a
    ``brand_json_resolution_failed`` AgentResolverError) and
    :func:`_map_brand_authorization_reason` (reading ``result.fetch_error`` off a
    ``brand_json_unavailable`` BrandAuthorizationResult) — one mapping, not two copies
    of the same fetch/parse-failure branches.
    """
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
        # Fires when agent.url's origin != brand.json's origin and the agents[] entry
        # declares no explicit jwks_uri — close to origin-mismatch semantics, but is
        # genuinely "no valid jwks_uri could be derived" rather than a key-origin
        # consistency failure on an already-resolved key (that check is separate,
        # step 7, and runs on a resolution this failure never produces).
        return REQUEST_SIGNATURE_JWKS_UNAVAILABLE
    logger.warning("Unmapped BrandJsonResolverError code %r; defaulting to JWKS_UNAVAILABLE", code)
    return REQUEST_SIGNATURE_JWKS_UNAVAILABLE


class _FailedDiscoveryJwksResolver:
    """Raises the discovery failure's MAPPED code from ``__call__``, not the generic ``key_unknown``.

    Constructed by :func:`_jwks_resolver` ONLY when a walk was attempted for this
    ``agent_url`` and failed (a recorded :class:`_ResolutionFailure` exists) — never
    for "no agent_url at all" (B4's registry path with no match), which stays the
    generic ``key_unknown`` via a plain empty :class:`StaticJwksResolver`.

    Raising happens INSIDE ``__call__``, which the SDK's checklist invokes at step 7
    (``verifier.py:255``, ``options.jwks_resolver(keyid)``) — AFTER steps 1-6 already
    ran on their own merits. Raising at VerifyOptions CONSTRUCTION time instead (the
    original, incorrect design) would let a discovery failure outrank an earlier
    checklist step, reordering a graded artifact; the conformance suite fixes which
    step each negative vector fails at, and step 1 is not negotiable against a step-7
    concern. ``tests/integration/test_request_signature_discovery.py``'s
    ``TestDiscoveryFailureDefersToTheChecklist`` is the canary for this.
    """

    def __init__(self, code: str) -> None:
        self._code = code

    def __call__(self, keyid: str) -> dict[str, Any] | None:
        raise SignatureVerificationError(self._code, step=7, message=f"counterparty discovery failed: {self._code}")


def _jwks_resolver(resolution: AgentResolution | None, *, agent_url: str | None = None) -> Any:
    """The resolver handed to the checklist.

    With a resolution: a brand-json-marked resolver, which is what engages the step-7
    key-origin check. Without one: if a walk was attempted for *agent_url* and failed,
    a resolver that raises the mapped discovery code from its own ``__call__``
    (#1291 hksr); otherwise a plain empty resolver, so the checklist answers
    ``request_signature_key_unknown`` at step 7 on its own — unchanged behavior for
    "no agent_url to walk at all" (B4's registry-miss case). Marking the empty
    resolver ``brand_json`` would instead make the SDK warn about a missing
    ``expected_key_origins`` map that by definition cannot exist.
    """
    if resolution is not None:
        return _BrandJsonJwksResolver(resolution.jwks, jwks_uri=resolution.jwks_uri)
    if agent_url is not None:
        failure = _RESOLUTION_FAILURES.get(agent_url)
        if failure is not None:
            return _FailedDiscoveryJwksResolver(failure.code)
    return StaticJwksResolver({})


def _brand_authz_resolver_for(brand_json_url: str) -> BrandJsonAuthorizationResolver:
    """Build-or-reuse the cached Tier-3 resolver for *brand_json_url* (#1291 hksr)."""
    cached = _BRAND_AUTHZ_RESOLVER_CACHE.get(brand_json_url)
    if cached is not None:
        return cached
    resolver = BrandJsonAuthorizationResolver(brand_json_url)
    _BRAND_AUTHZ_RESOLVER_CACHE[brand_json_url] = resolver
    return resolver


def _map_brand_authorization_reason(result: BrandAuthorizationResult) -> str:
    """``BrandAuthorizationReason`` -> the ``request_signature_*`` code security.mdx assigns it.

    Only called on a REFUSED result (``result.authorized`` is False) — the two success
    reasons (``etld1_match``, ``operator_delegation``) never reach here.
    """
    if result.reason in ("agent_not_listed", "agent_type_mismatch"):
        # BrandJsonAuthorizationResolver.check's own docstring: "Spec folds both into
        # request_signature_agent_not_in_brand_json."
        return REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON
    if result.reason == "agent_ambiguous":
        return REQUEST_SIGNATURE_BRAND_JSON_AMBIGUOUS
    if result.reason == "binding_failed":
        # THE trust-pivot case: the agent IS listed in agents[], but fails BOTH
        # eTLD+1 binding AND authorized_operators[] delegation — right key-type,
        # wrong URL.
        return REQUEST_SIGNATURE_BRAND_ORIGIN_MISMATCH
    if result.reason == "brand_json_unavailable":
        return _map_brand_json_resolver_error(result.fetch_error)
    # "brand_domain_invalid" should not occur under correct wiring: brand_domain is
    # derived FROM resolution.brand_json_url (async_resolve_agent already validated it
    # as a URL), not supplied independently. If it ever fires, that is a wiring bug,
    # not a counterparty fault.
    logger.error(
        "brand_domain_invalid for a self-derived brand_domain -- this indicates a wiring bug, not a counterparty fault"
    )
    return REQUEST_SIGNATURE_BRAND_JSON_MALFORMED


async def _check_brand_authorization(resolution: AgentResolution, config: SigningConfig) -> None:
    """Tier 3: is this cryptographically-verified agent authorized to act for its brand?

    #1291 hksr. Runs AFTER ``_run_verifier`` succeeds (Tiers 1+2 already passed) — a
    valid signature proves WHO signed, never that the signer may act for the brand
    listed at its own brand.json. Only called for brand-json-WALKED counterparties
    (see :class:`_CounterpartyResolution`); B4's registry fallback has no real
    brand.json to check against and must never reach this function.

    ``brand_domain`` is derived from ``resolution.brand_json_url``'s own host — the
    natural, spec-consistent choice, since eTLD+1 binding checks whether the agent's
    host shares that SAME registrable domain.
    """
    resolver = _brand_authz_resolver_for(resolution.brand_json_url)
    try:
        result = await resolver.check(
            agent_url=resolution.agent_url,
            brand_domain=host_from(resolution.brand_json_url),
            agent_type=config.counterparty_agent_type,
            brand_id=None,
        )
    except Exception as exc:
        # BrandJsonAuthorizationResolver.check() normalizes fetch/parse failures into
        # a "brand_json_unavailable" result (handled below via result.fetch_error),
        # but transport-layer failures below that normalization boundary -- e.g. SSRF
        # host-validation rejecting a non-routable/unresolvable host -- escape as raw
        # exceptions instead. Treat any such failure the same as a fetch failure
        # (unreachable), not an uncaught 500.
        logger.warning("Tier 3 brand authorization check raised %s: %s", type(exc).__name__, exc)
        raise SignatureVerificationError(
            REQUEST_SIGNATURE_BRAND_JSON_UNREACHABLE,
            step=7,
            message=f"brand authorization check failed: {exc}",
        ) from exc
    if result.authorized:
        return
    code = _map_brand_authorization_reason(result)
    raise SignatureVerificationError(code, step=7, message=f"brand authorization refused: {result.reason}")


# ---------------------------------------------------------------------------
# Phase 3 — the checklist (one thread hop)
# ---------------------------------------------------------------------------


def _run_verifier(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    capability: VerifierCapability,
    operation: str,
    bucket: PostureBucket,
    resolution: AgentResolution | None,
    agent_url: str | None,
    config: SigningConfig,
    precheck_only: bool = False,
) -> VerifiedSigner:
    """Run the SDK checklist over one database session — or the pre-check over none.

    One hop, one session: the replay store's ``at_capacity`` / ``seen`` / ``remember``
    are synchronous and the verifier calls them inline, so a single checkout serves all
    three (A4's wiring contract, ``src/core/signing/replay_store.py``). The SDK's own
    call order is what keeps the two spec ordering invariants — capacity before crypto
    verify, replay claim after it — and this function preserves them by not reordering
    anything.

    All four key-origin fields are passed (R-M2): ``expected_key_origins``,
    ``agent_url``, ``signing_purpose`` and ``posture``. Omitting the first turns a
    mandatory check into a ``UserWarning``. ``expected_key_origins`` is ``resolution.
    key_origins or {}`` (#1291 hksr) rather than the map itself: passing ``None``
    (what a counterparty advertising no ``identity.key_origins`` map produces) tells
    the SDK the ADOPTER never threaded the map through and makes it silently SKIP the
    check with a warning — the shared-tenancy defense the check exists for then ships
    silently off. An empty dict is what makes the SDK run the check and correctly
    refuse with ``request_signature_key_origin_missing``.

    *agent_url* is the ORIGINAL value (independent of whether *resolution* resolved),
    threaded through to :func:`_jwks_resolver` so a failed walk can still be mapped to
    its discovery code even though *resolution* itself is ``None``.

    Revocation (step 9) is wired through ``revocation_checker`` and ONLY that hook.
    ``revocation_list`` is the staleness-only branch — ``verifier.py:283-292`` calls
    ``is_stale()`` and never ``is_revoked()`` — so passing both would be two sources
    for one decision; :func:`~src.core.signing.revocation.checker_for` owns both
    halves. Step 9 runs before crypto verify because the SDK calls it at
    ``verifier.py:294`` and crypto at :311; nothing here may reorder that, and
    ``tests/integration/test_request_signature_revocation.py``'s ordering canary is
    what keeps it true.
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
        return verify_request_signature(method=method, url=url, headers=headers, body=body, options=options)


@contextmanager
def _verifier_dependencies(
    resolution: AgentResolution | None,
    *,
    agent_url: str | None,
    config: SigningConfig,
    precheck_only: bool,
) -> Iterator[tuple[JwksResolver, ReplayStore | None, RevocationChecker | None]]:
    """The three dependencies the checklist needs, or the null trio for a pre-check.

    ONE :class:`VerifyOptions` construction is built on this, deliberately: two literals
    would let the pre-check path and the verified path drift apart, which is the class of
    duplication this seam exists to remove.

    The null trio is what makes the narrowed ``none`` bucket safe to pre-check. The
    SDK reaches ``jwks_resolver`` at ``verifier.py:256`` and every step-1 raise fires
    before it, so an empty resolver stops execution there — no signature base, no crypto,
    no outbound, and no database session at all. ``VerifyOptions`` declares
    ``replay_store`` and ``revocation_checker`` as optional (``verifier.py:139-140``), so
    the nulls are the SDK's own contract rather than an assumption about it.

    WRAPS the verify call rather than merely preceding it: the replay store's
    ``at_capacity`` / ``seen`` / ``remember`` are synchronous and the verifier calls them
    inline, so the session has to outlive the construction.
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


def _verify_url(scope: Mapping[str, Any], headers: Mapping[str, str]) -> str:
    """The URL the signature covers, as the CLIENT addressed it.

    Authority comes from the ``Host`` header as received and the scheme from the first
    hop of ``X-Forwarded-Proto`` — the client-facing scheme our own edge proxy
    terminated. Never from proxy ROUTING state (``Apx-Incoming-Host`` and friends),
    which security.mdx step 10 forbids deriving identity from: the signer signed the URL
    it dialed, and a rewritten one would fail ``@target-uri`` on every legitimate
    request behind TLS termination.
    """
    forwarded = headers.get("x-forwarded-proto", "")
    scheme = forwarded.split(",")[0].strip().lower() if forwarded else ""
    if scheme not in ("http", "https"):
        scheme = scope.get("scheme", "http")

    authority = headers.get("host")
    if not authority:
        server = scope.get("server") or ("", None)
        authority = f"{server[0]}:{server[1]}" if server[1] else str(server[0])

    query = scope.get("query_string", b"").decode("latin-1")
    return f"{scheme}://{authority}{_signed_path(scope)}" + (f"?{query}" if query else "")


def _signed_path(scope: Mapping[str, Any]) -> str:
    """The path AS SENT — percent-encoding intact, mount prefix intact.

    ``scope["path"]`` is percent-DECODED by every real server (uvicorn sets
    ``path = unquote(raw_path)``), so reading it turns ``%2F`` into a real separator and
    every encoded byte into its decoded form. The signature covers the bytes the client
    sent, so a decoded path yields a signature base the client never signed and rejects
    a legitimate request as ``request_signature_invalid`` — reachable in production on
    any ``/api/v1/...`` route with a path parameter, and pinned by conformance vectors
    ``positive/008``/``009``/``010``.

    **This is deliberately the OPPOSITE of** :func:`~src.core.http_utils.path_from_asgi_scope`,
    and a blanket "always use ``raw_path``" rule would be a defect. That helper reads the
    DECODED path and STRIPS ``root_path`` because it feeds a ROUTE TABLE, and it must
    agree with the Starlette router that actually dispatches — as must the four other
    decoded-path readers in the tree (``src/app.py``'s ``/a2a`` predicate and telemetry
    label, and ``rest_compat_middleware``'s two route lookups). This function feeds
    ``@target-uri``, whose only authority is the wire, so:

    * ``raw_path`` is BYTES and ALREADY carries ``root_path`` (uvicorn builds
      ``full_raw_path`` that way in both its h11 and httptools implementations). The
      client signed the URL it dialed, mount prefix included, so it is NOT stripped.
    * The decode is STRICT ASCII. A non-ASCII raw path is unrepresentable in a signed
      ``@target-uri`` — percent-encoding is mandatory on the wire — so it is a malformed
      request, not an encoding puzzle: ``latin-1`` would mojibake it into a base that
      could still VERIFY, and UTF-8 would mask it. Both are silent-acceptance bugs.
    * Anything from the first ``?`` is dropped: some ASGI servers and test clients put
      the query INSIDE ``raw_path``, and the query has exactly one source
      (``scope["query_string"]``, raw bytes decoded latin-1 — which is why
      ``positive/007``'s byte-preserved query already passes).
    * ``raw_path`` absent -> the decoded path, with the degradation known and accepted:
      on such a server the encoded bytes are gone before we are called, so 008/009/010
      are not gradeable there. Failing every signed request instead would be worse.
    """
    raw = scope.get("raw_path")
    if not isinstance(raw, bytes | bytearray):
        return str(scope.get("path", ""))
    try:
        decoded = bytes(raw).decode("ascii")
    except UnicodeDecodeError as exc:
        raise SignatureVerificationError(
            REQUEST_SIGNATURE_HEADER_MALFORMED,
            step=1,
            message="the request path carries raw non-ASCII bytes; a signed @target-uri must be percent-encoded",
        ) from exc
    return decoded.split("?", 1)[0]


def _reject_headers(message: str) -> NoReturn:
    """The one rejection the pre-parse gate raises — the SDK's own type and constant."""
    raise SignatureVerificationError(REQUEST_SIGNATURE_HEADER_MALFORMED, step=1, message=message)


def _duplicate_signature_input_label(value: str) -> str | None:
    """Why one ``Signature-Input`` value is ambiguous, or ``None``.

    Multiple DISTINCT labels are legal and must stay legal —
    ``positive/004-multiple-signature-labels`` ships ``sig1`` and ``sig2`` and is a
    vector we must ACCEPT. Only a REPEATED key is the ambiguity: RFC 8941 §3.2 permits
    "rejecting the input" or "retaining only the last value", and the second option
    would let a proxy smuggle a weaker covered-component set past a verifier that read
    the first.

    An entry this cannot parse yields ``None`` on purpose: an unparseable header is the
    SDK's to code (``negative/011``, ``negative/024``), and pre-empting it here would
    change a graded artifact that is already correct.
    """
    seen: set[str] = set()
    for entry in split_structured_field(value, ","):
        marker = entry.find("=(")
        if marker < 0:
            return None
        label = entry[:marker].strip()
        if label in seen:
            return f"the dictionary key {label!r} appears twice, which RFC 8941 §3.2 leaves ambiguous"
        seen.add(label)
    return None


def _multi_valued_content_type(value: str) -> str | None:
    """Why one ``Content-Type`` value is ambiguous, or ``None``.

    ``Content-Type`` is not a List-Structured-Field — it has one canonical value with
    optional parameters — so a second value (from a buggy client, or a proxy appending
    one) leaves the field's meaning relative to the signature base undefined.
    """
    if "," in value:
        return "the field is multi-valued, and it is not a List-Structured-Field"
    return None


def _duplicate_digest_algorithm(value: str) -> str | None:
    """Why one ``Content-Digest`` value is ambiguous, or ``None``.

    RFC 9530 §2 makes the field a Dictionary keyed by digest algorithm; two members of
    the SAME algorithm are a parser-differential, because signer and verifier may
    disagree about which value entered the base — true even if the two digests match.
    Distinct algorithms (``sha-256`` plus ``sha-512``) are legal.

    Splitting on ``,`` is sound for this field specifically: members are
    ``<alg>=:<base64>:`` and the base64 alphabet contains no comma, so no member can
    hide a separator.
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


#: ``header -> what makes ONE of its values malformed``. A table rather than four
#: near-identical blocks: each rule is the same shape (read a value, name a reason or
#: pass), and four copies is four chances for one of them to stop rejecting.
#:
#: ``host`` shares :func:`~src.core.signing_contract.canonical.malformed_authority_reason` with
#: the canonicalization seam so the authority rule has exactly ONE definition. Only the
#: CODE differs by caller — checklist step 1 here, ``request_target_uri_malformed``
#: there — and that difference is deliberate (``negative/026`` grades the former).
_MALFORMED_VALUE_RULES: tuple[tuple[str, Callable[[str], str | None]], ...] = (
    ("signature-input", _duplicate_signature_input_label),
    ("content-type", _multi_valued_content_type),
    ("content-digest", _duplicate_digest_algorithm),
    ("host", malformed_authority_reason),
)

#: Headers that MUST arrive on exactly one line when a signature covers the request.
#: Not a style rule: :func:`~src.core.http_utils.headers_from_asgi_scope` — like every
#: dict view of ASGI headers — LAST-WINS on a repeated tuple rather than joining it, so
#: a proxy-inserted second line rewrites a covered value with nothing anywhere to notice.
_SINGLE_LINE_SIGNED_HEADERS: tuple[str, ...] = tuple(name for name, _rule in _MALFORMED_VALUE_RULES)


def _strict_header_precheck(scope: Mapping[str, Any]) -> None:
    """Checklist step 1 over the RAW header list, before the SDK sees anything.

    Runs against ``scope["headers"]`` — the ``list[tuple[bytes, bytes]]`` — and NOT
    against the collapsed dict, because the collapse is the attack. The conformance
    vectors express "multi-valued" as one comma-joined value, so a gate written over
    the dict would pass all four of them while missing the threat their ``$comment``s
    actually describe: a SECOND header LINE, which last-wins before any check runs.
    ``tests/unit/test_signing_raw_wire_gaps.py`` grades both forms of each shape.

    Why OURS and not the SDK's: measured against ``adcp==6.6.0``, the SDK answers these
    four vectors with the wrong graded artifact — ``request_signature_components_incomplete``
    for ``negative/021`` (its RFC 8941 parser last-wins on the duplicate dictionary key)
    and ``request_signature_invalid`` for ``022``/``023``/``026`` (no single-value check on
    a covered non-list field, no RFC 9530 duplicate-algorithm check, no A-label
    enforcement anywhere on the authority path). Filed upstream as SDK divergence #6.

    Spec grounding, stated honestly rather than implied:

    * the non-ASCII authority and the malformed-authority family are well grounded —
      ``url-canonicalization.mdx`` steps 2-3, MUST-reject, shared with
      :mod:`src.core.signing_contract.canonical` so the rule has ONE definition (the CODE
      differs: a wire-header rejection is checklist step 1, a canonicalization
      rejection is ``request_target_uri_malformed``);
    * ``negative/023`` rests on RFC 9530 §2's Dictionary semantics;
    * ``negative/021`` (duplicate ``Signature-Input`` dictionary key) and ``negative/022``
      (multi-valued covered non-list field) are grounded ONLY in the vectors' own
      ``$comment`` plus checklist step 1's bare "Reject if malformed" — no prose
      enumerates them. Do not cite prose support that is not there.

    Deliberately NARROW. A gate that rejected traffic the spec requires us to ACCEPT
    would be worse than the bug it closes, so anything it cannot prove malformed is
    handed to the SDK unchanged — including a ``Signature-Input`` it cannot parse
    (``negative/011``, ``negative/024``), whose codes the SDK already gets right.
    """
    lines: dict[str, list[str]] = {}
    for raw_name, raw_value in scope.get("headers", []):
        lines.setdefault(raw_name.decode("latin-1").lower(), []).append(raw_value.decode("latin-1"))

    for name in _SINGLE_LINE_SIGNED_HEADERS:
        if len(lines.get(name, ())) > 1:
            _reject_headers(f"{name!r} arrived on {len(lines[name])} header lines; a covered field must have one")

    for name, reason_for in _MALFORMED_VALUE_RULES:
        for value in lines.get(name, ()):
            reason = reason_for(value)
            if reason is not None:
                _reject_headers(f"{name!r} is malformed: {reason} — received {value!r}")


async def _reject(
    exc: SignatureVerificationError,
    scope: dict[str, Any],
    receive: Receive,
    send: Send,
) -> None:
    """Send the spec's 401 directly.

    SENT, never raised: FastAPI's exception handlers are inner to this middleware, so a
    raise would reach ``ServerErrorMiddleware`` and become a 500 — turning a graded
    rejection into an outage-shaped response. The header comes from the SDK
    (``WWW-Authenticate: Signature error="<code>"``, realm intentionally omitted) so the
    byte-for-byte grading surface has one source.
    """
    logger.info("Rejecting request signature: code=%s step=%s (%s)", exc.code, exc.step, exc)
    await Response(status_code=401, headers=unauthorized_response_headers(exc))(scope, receive, send)

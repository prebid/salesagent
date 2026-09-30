"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

ONE DISPATCH FORM. The MCP and A2A dispatchers call ``env.deliver_mcp`` /
``env.deliver_a2a`` — the single pair ``BaseTestEnv`` owns
(``tests/harness/_base.py``), which ``call_mcp``/``call_a2a`` delegate to as
``deliver_*(**kwargs).payload``. There is no second channel: the deprecated
raw-kwargs dispatch path was deleted once its last 18 call sites migrated, and
per-env ``call_mcp``/``call_a2a`` re-implementations are banned by
``tests/unit/test_architecture_harness_single_dispatch.py``. A scenario builds
the request model; the transport serializes it.

THE WIRE RIDES THE RETURN VALUE. Success-path wire bytes arrive on
``DeliverResult.wire_response`` and are re-emitted on
``TransportResult.wire_response``; error-path wire bytes arrive on
``TransportResult.wire_error_envelope`` from ``client.py``'s per-transport
unwraps. No dispatcher reads another object's private wire stash — that
attribute (``_last_wire_response``) is deleted, which is what makes a second
writer, and a stale wire, structurally impossible.

On error, dispatchers capture the wire error envelope (the raw two-layer dict
the buyer would see) alongside the reconstructed exception.  New tests should
assert on ``result.wire_error_envelope`` via ``assert_envelope_shape()`` — see
``tests/CLAUDE.md`` § Error Verification Policy.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.A2A]
    result = dispatcher.dispatch(env, signed=False, **kwargs)

``signed`` is a DISPATCH concern and is consumed HERE, on every arm — declared
keyword-only on every ``dispatch()`` so it can never fall through into
``**kwargs`` and reach a request body (``extra="forbid"`` refuses a ``signed``
field, and the leak stays silent until some schema happens to catch it). What
each arm DOES with it differs by transport and is stated on the arm itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from tests.harness.transport import (
    DeliverResult,
    Transport,
    TransportResult,
)

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv
    from tests.helpers.signing import SignatureRealization

# This module imports NO envelope BUILDER, only envelope readers. It used to own
# one, for the IMPL transport, and that is the whole reason IMPL is gone: a
# dispatcher that computed an envelope from the same in-memory exception the
# assertion then read could not have reddened for a regression in the production
# boundary translator. ``_envelope_from_adcp_error`` stays in transport.py, where
# client.py reads a REAL wire with it — housing it here would also recreate the
# mutual lazy-import cycle untangled later (client.py used to lazily import it
# back from this module, while this module lazily imports dispatch functions FROM
# client.py — the two together being the "mutual" part).
#
# The MCP/A2A/REST error-path unwraps themselves are NOT re-implemented here:
# this module delegates to client.py's unwrap_mcp_error / unwrap_a2a_error /
# unwrap_rest_error, so there is one error unwrap per transport family for both
# dispatch paths (CLAUDE.md DRY invariant; remediation finding 1). The same rule
# now covers the REST *response* unwrap (``unwrap_rest_response``), which is why
# the local ``_non_json_error_result`` helper is gone: the one-shape-on-both-REST-
# legs guarantee it existed for IS that function's non-JSON branch, and that
# branch keeps the ``raw_response`` a bodyless signature challenge is read off.
#
# Two invariants this module used to hold in its own helpers travel WITH those
# unwraps and must keep holding at their new home:
#   - ``wire_error_envelope`` carries REAL wire bytes or None — NEVER an
#     envelope the harness rebuilt from the exception it just caught. A
#     scenario asserting on that field would otherwise grade the rebuild, which
#     passes whether or not production emitted anything at all. Every dispatcher
#     below has a real wire, so None is the honest answer when nothing crossed
#     it and there is no second field to fall back to.
#   - ``has_wire`` is declared PER CONSTRUCTION SITE (required and keyword-only
#     on TransportResult), True only downstream of an actual send/receive; a
#     catch-all branch that may fire before anything was sent declares False.
#     Re-attaching a refusal's raw HTTP response (``_carrying_refusal_response``
#     below) does NOT change that declaration: ``replace()`` preserves the
#     ``has_wire=False`` the shared unwrap declared, because a refusal caught as
#     an exception still cannot prove bytes moved before it was raised.


def _refusal_response(exc: Exception) -> Any | None:
    """The raw HTTP response a JSON-RPC leg was refused with, if it was.

    The ``/a2a`` and ``/mcp`` legs read an ENVELOPE, and an envelope is not the
    whole refusal: ``WWW-Authenticate: Signature error="<code>"`` is a HEADER, and
    the verifier's bodyless 401 has no envelope at all. Both harness carriers hold
    the response for that reason — ``WireRefusal`` (no envelope existed) and
    ``WireError`` (one did, on a response worth keeping). Every other exception
    yields ``None``, which ``assert_signature_challenge`` reports as "no raw HTTP
    response" rather than passing for want of evidence.

    Matched on the CARRIER TYPES, not on ``getattr(exc, "response", None)``: a
    duck-typed read would also pick up whatever an httpx or SDK exception happens to
    hang off that name, and a response this harness never saw put on the wire is not
    evidence of the refusal under test.
    """
    from tests.harness._base import WireError, WireRefusal

    return exc.response if isinstance(exc, WireRefusal | WireError) else None


def _carrying_refusal_response(result: TransportResult, exc: Exception) -> TransportResult:
    """*result*, with the refusal's raw HTTP response re-attached when there is one.

    The error unwraps live in ``client.py`` and are shared with
    ``AdCPTestClient`` — they know about ENVELOPES, which is the right thing for
    them to know. A signature refusal's challenge is not in the envelope, so its
    evidence is the response the carrier holds; it is re-attached here
    rather than inside the shared unwrap because it is a fact about the
    EXCEPTION, not about the transport family. One helper, every error arm: a leg
    that dropped the response would be graded "no wire to read" instead of as the
    refusal — or the acceptance — it actually is.
    """
    response = _refusal_response(exc)
    return result if response is None else replace(result, raw_response=response)


def _refuse_signed_generic_client(signed: SignatureRealization, transport: Transport) -> None:
    """Fail loudly when a realization reaches an E2E leg the generic client cannot sign.

    ``E2E_MCP`` and ``E2E_A2A`` deliver through ``AdCPTestClient``
    (``client.py``'s ``_deliver_e2e_mcp`` / ``_deliver_e2e_a2a``), which build
    and serialize their own request instead of going through
    ``BaseTestEnv.wire_request`` — the seam that owns serialize-once, the
    credential and the tenant hint, and therefore the only place a real RFC 9421
    signature is produced.

    So a realization cannot be honored on these two legs today, and refusing
    rather than ignoring ``signed`` is the whole discipline: an unsigned send
    here would let a scenario that ASKED for a signature pass having signed
    nothing — precisely the class of hole S1 exists to close (the A2A
    credential-location bypass survived because the property was asserted by
    code shape and never observed). The in-process ``mcp``/``a2a`` legs are
    unaffected — they sign, because ``_run_mcp_client``/``_run_a2a_handler``
    re-route through ``src.app.app`` once the env can sign.

    Refusal is on TRUTHINESS, not on ``signed is True``: ``signed`` carries
    failure realizations too (``"malformed"`` / ``"tampered"``,
    :data:`tests.helpers.signing.SIGNATURE_REALIZATIONS`), and a malformed
    signature that never left the process has exactly as little meaning as a
    valid one.
    """
    raise NotImplementedError(
        f"call_via({transport.value!r}, signed={signed!r}) cannot be realized: this leg delivers "
        "through AdCPTestClient (tests/harness/client.py), which builds its own request instead "
        "of going through BaseTestEnv.wire_request, so no RFC 9421 signature is produced. Use "
        "Transport.E2E_REST for a signed request against the live stack, or Transport.MCP / "
        "Transport.A2A in process. Refusing rather than running unsigned, which would make a "
        "signing scenario pass without a signature."
    )


def a2a_transport_result(call: Callable[[], DeliverResult | Any]) -> TransportResult:
    """One ``TransportResult`` out of any dispatch onto the ``/a2a`` wire.

    Stated once because A2A has MORE THAN ONE thing a buyer can send it — a
    skill invocation and a bare credential registration
    (``tasks/pushNotificationConfig/set``) — and both have to produce results a
    scenario can grade side by side. A second copy of this wrapping would be
    free to drop the refusal's raw response, which
    ``assert_signature_challenge`` reports as "no wire to read" rather than as
    the acceptance it actually is.

    *call* returns a :class:`~tests.harness.transport.DeliverResult` on a leg that
    observes a success-path wire (the skill dispatch, ``env.deliver_a2a``), and a
    bare payload on one that does not (the credential registration, which
    deliberately captures no wire — the operation dispatch's capture belongs to
    the operation dispatch). Either way the wire rides the RETURN VALUE and is
    never read back off another object's private attribute.

    The error path delegates to ``client.py``'s ``unwrap_a2a_error`` — the one
    A2A error unwrap, shared with ``AdCPTestClient``, which owns wire-envelope
    recovery and the derived status — then re-attaches the refusal response.
    """
    from tests.harness.client import unwrap_a2a_error

    try:
        delivered = call()
    except Exception as exc:
        # ONE A2A error unwrap for both dispatch paths (client.py). This used to
        # be a second copy of that body, which is how the derived status ended up
        # on this path and not on AdCPTestClient.call — the path the graded
        # storyboard scenarios actually take. It reads the REAL envelope off the
        # exception and must never hand back a synthesized stand-in under
        # ``wire_error_envelope`` — see the module note above. It declares
        # ``has_wire=False`` at its own construction site, and re-attaching the
        # refusal response leaves that declaration alone: a catch-all cannot tell
        # whether bytes moved before the raise.
        return _carrying_refusal_response(unwrap_a2a_error(exc, Transport.A2A), exc)
    if isinstance(delivered, DeliverResult):
        # Real A2A wire: the artifact DataPart dict, carried back on the SAME
        # return value as the payload. It used to be read off
        # env._last_wire_response — one object reaching into another's private
        # attribute, which is what allowed a second writer and a stale wire.
        return TransportResult(
            has_wire=True,  # the artifact DataPart came back from the handler
            payload=delivered.payload,
            envelope={"transport": "a2a"},
            wire_response=delivered.wire_response,
        )
    # A leg that returned a bare payload rather than a ``DeliverResult``: no
    # success-path wire was captured, so it declares has_wire=False rather than
    # claiming a wire whose body it never stashed.
    return TransportResult(payload=delivered, envelope={"transport": "a2a"}, has_wire=False)


class A2ADispatcher:
    """Dispatch via ``handler.on_message_send`` — exercises the full A2A pipeline.

    ``env.deliver_a2a`` drives ``AdCPRequestHandler.on_message_send`` end-to-end
    (message parsing → skill routing → ``serve`` → ``to_wire``
    → Task/Artifact framing). On a failed Task, the harness reconstructs the
    ``AdCPSalesAgentError`` from the artifact DataPart and stashes the real wire
    envelope on the exception via ``_wire_error_envelope`` — read off it by
    ``client.py``'s ``unwrap_a2a_error``, the one A2A error unwrap.

    ``signed`` is consumed here but not forwarded. Whether a signature is
    realizable is a fact about the ENV (does it have a counterparty key?), not
    about this class, and the answer changes the TRANSPORT: once the env can
    sign, ``_run_a2a_handler`` routes through a real ``POST /a2a`` on
    ``src.app.app``, because an ``on_message_send`` call has no wire for the ASGI
    verifier to see. The flag reaches that method on the env — see
    ``BaseTestEnv._signed_dispatch``, which ``call_via`` sets before dispatching.
    An env with no capability raises from ``env.signing``, naming
    ``enable_request_signing()``. Declaring it keyword-only here is what keeps it
    out of ``**kwargs``, and so out of the request body.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        # Real A2A wire on success: the artifact DataPart dict, carried back on
        # the SAME return value as the payload. It used to be read off
        # env._last_wire_response — one object reaching into another's private
        # attribute, which is what allowed a second writer and a stale wire.
        return a2a_transport_result(lambda: env.deliver_a2a(**kwargs))


class RestDispatcher:
    """Dispatch via FastAPI TestClient → route → invoke_tool() → _impl().

    The credential flows through kwargs to env._run_rest_request(), which pops it
    and sends it as the request headers; the production middleware reads it there.

    ``signed`` is forwarded EXPLICITLY to ``_run_rest_request`` — the in-process
    REST leg builds its own request, so it is the leg that has to be told.
    ``_run_rest_request`` and every override of it declare ``signed`` keyword-only
    for the same reason this method does: an override that let it fall through
    into ``**kwargs`` would put a ``signed`` FIELD in the request body, which
    ``extra="forbid"`` refuses as ``INVALID_REQUEST`` — the bug found on
    ``MediaBuyDualEnv._run_rest_request``.

    Unlike other dispatchers, REST includes HTTP metadata in the envelope
    (status_code, content_type) since tests may assert on these —
    ``unwrap_rest_response`` builds it.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        from tests.harness.client import unwrap_rest_error, unwrap_rest_response

        try:
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = env._run_rest_request(endpoint, signed=signed, **kwargs)
        except Exception as exc:
            # ONE REST DELIVER-exception unwrap for both dispatch paths — it
            # derives status=transport_fault and declares has_wire=False,
            # because an exception here means no HTTP body, hence no AdCP
            # envelope, ever existed. Re-attaching the refusal response does not
            # revise that declaration.
            return _carrying_refusal_response(unwrap_rest_error(exc, Transport.REST), exc)
        # unwrap_rest_response owns the status-code branching, the envelope tag,
        # the #1417 pristine-wire deepcopy rule, the per-site has_wire declaration
        # (True on every branch it returns: a response — 2xx or >=400 — means
        # bytes came back over HTTP), and the non-JSON-body case — the same
        # function RestE2EDispatcher and the generic client's _unwrap_rest
        # delegate to below, so the verifier's BODYLESS 401 produces ONE error
        # shape on both REST legs and keeps its response for
        # assert_signature_challenge to read.
        return unwrap_rest_response(env, response, Transport.REST, env.parse_rest_response)


class McpDispatcher:
    """Dispatch via Client(mcp) — full FastMCP pipeline.

    The credential flows through kwargs to env.deliver_mcp() → _run_mcp_client(),
    which pops it and presents it as the request headers the tool reads.

    ``signed`` is consumed but not forwarded, for the reason given on
    ``A2ADispatcher``: an env that can sign makes ``_run_mcp_client`` drive a
    real streamable-HTTP session against ``src.app.app`` instead of FastMCP's
    in-memory object streams, which carry no headers and never reach the ASGI
    verifier.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        try:
            delivered = env.deliver_mcp(**kwargs)
        except Exception as exc:
            from tests.harness.client import unwrap_mcp_error

            # ONE MCP error unwrap for both dispatch paths (client.py) — it owns
            # the raw-ToolError unwrap, the REAL-wire-only envelope rule (never
            # the synthesized fallback), the per-site has_wire declaration
            # (False — a catch-all cannot tell whether bytes moved), and the
            # derived status. See the A2A sibling above for why this is a
            # delegation and not a copy. The refusal's raw response is
            # re-attached on top: a bodyless 401 leaves that unwrap no envelope
            # to recover.
            return _carrying_refusal_response(unwrap_mcp_error(exc, Transport.MCP), exc)
        # Real MCP wire: the structured_content dict, carried back on the SAME
        # return value as the payload — see the A2A sibling above.
        return TransportResult(
            has_wire=True,  # structured_content came back from the MCP client
            payload=delivered.payload,
            envelope={"transport": "mcp"},
            wire_response=delivered.wire_response,
        )


def _effective_rest_method(method: str, *, has_params: bool) -> str:
    """The HTTP verb a REST dispatch actually uses.

    An env declaring ``REST_METHOD = "get"`` means "GET the parameterless
    happy-path route, POST the same route when request params are present" —
    both are real production routes (``CapabilitiesEnv``, salesagent-5yik), and
    the in-process ``_run_rest_request`` override already switches exactly this
    way. Derived once so the e2e leg cannot answer the question differently from
    the in-process one; the verb then drives everything downstream — whether a
    body is sent at all, and which ``@target-uri`` a signature covers.
    """
    if method != "get":
        return method
    return "post" if has_params else "get"


def _deliver_e2e_rest_signed(
    env: BaseTestEnv,
    *,
    base_url: str,
    endpoint: str,
    method: str,
    body: dict[str, Any],
    credential: Any,
    signed: SignatureRealization,
) -> Any:
    """E2E_REST delivery through ``env.wire_request`` — the signABLE path.

    Not a second copy of ``client._deliver_e2e_rest``: that function serializes
    its own request (httpx's ``json=``), which is the one thing a signature
    cannot survive — httpx re-serializes with its own separators, so the bytes
    signed are not the bytes sent and the verifier answers
    ``request_signature_digest_mismatch``. Once the env CAN sign, every request
    it makes — signed or not — goes through ``wire_request``, which owns the
    three rules a signed request obeys (serialize once and send exactly those
    bytes; carry the same credential and tenant hint whether or not you sign; one
    identity, on ``Authorization``). The fork is on ``can_sign``, not on
    ``signed``, so a signed and an unsigned dispatch differ by exactly the
    signature (owner decision D1's corollary); an env that cannot sign keeps the
    single upstream delivery implementation untouched.

    ONE thing is this leg's OWN and is stated here rather than inside that seam,
    because only this leg has it: the ORIGIN is the live stack's, port included —
    nginx forwards ``Host`` verbatim and ``_verify_url`` rebuilds the authority
    from it, so the in-process default (``http://testserver``) would cover a
    different ``@target-uri`` than the verifier reconstructs.

    The tenant hint used to be this leg's too, for a reason that has since become
    every leg's: the header named a module-level ``sig_tenant`` that does not
    exist in the live database, and a header asserting a tenant other than the
    one being addressed is a lie inside the signed byte range — one ladder
    reordering away from collapsing the posture bucket to ``none``, an unverified
    pass-through wearing a 200. ``wire_request`` now sets it for all four legs,
    and nothing else is carried across from *credential*: there are no testing
    headers left to forward (the ``x-dry-run`` channel went with the testing
    hooks), so this leg passes no ``extra`` at all.

    *credential* is read for ONE bit and no more. ``wire_request`` owns the
    credential it sends — the signing capability's bearer, on ``Authorization``,
    per owner decision D3/D4 — so what a caller passes here cannot add a second
    identity to the request; it can only say whether an ACCEPTABLE one is
    presented. That bit is what selects the branch being graded: security.mdx
    :1269 makes an unsigned request carrying a valid bearer a spec-correct 200,
    so only a call presenting no bearer (``credential={}``, or one built with
    ``token=None``) reaches the verifier's refusal at all.

    *base_url* is passed in rather than read off ``env.e2e_config`` here: the
    caller has already established it is not ``None`` (and returns a
    ``TransportResult`` naming ``e2e_config=`` when it is), so re-deriving it
    would either duplicate that check or type as optional.
    """
    import httpx

    from tests.harness.client import _presented
    from tests.helpers.signing import wire_origin

    raw, headers = env.wire_request(
        path=endpoint,
        # A parameterless GET has no body to sign, and none to send.
        body=None if method == "get" else body,
        signed=signed,
        origin=wire_origin(base_url),
        # ``_presented`` is the one place an omitted credential falls back to
        # env.credential(), shared with every other e2e delivery; the bearer is
        # then the acceptable-credential bit, not the header set to send.
        credentialed=bool(_presented(env, credential).get("Authorization")),
        method=method.upper(),
    )
    with httpx.Client(base_url=base_url, timeout=30) as client:
        # Signed or not, the bytes signed are the bytes sent — `content=`, never
        # `json=`, which would re-serialize them.
        return client.request(method.upper(), endpoint, content=raw, headers=headers)


class RestE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack.

    Exercises the full stack: nginx -> the live server's header read ->
    resolver -> get_principal_from_token() DB lookup -> route
    handler -> _impl().

    WRAP (``env.build_rest_body`` / ``env.REST_ENDPOINT`` / ``env.REST_METHOD``)
    stays the per-env contract every dispatch path already uses — migrating
    that to the generic ``tests.harness.client._wrap_rest`` would require
    rewriting each env's bespoke request-shaping (e.g. ``MediaBuyDualEnv``'s
    create/update routing), an explicit non-goal of the transport-generic
    client design (see ``tests/harness/client.py``).

    DELIVER (the actual httpx call) is NOT hand-rolled here — it delegates to
    ``tests.harness.client._deliver_e2e_rest``, the single delivery
    implementation also used by ``AdCPTestClient.call(..., Transport.E2E_REST)``
    (the wire-grading work; design doc §5). THE ONE EXCEPTION is an env that CAN
    SIGN: a signature requires the ``wire_request`` seam (serialize once, send
    exactly those bytes), which that delivery function does not go through, so
    the signable path is ``_deliver_e2e_rest_signed`` above. This is the only leg
    that leaves the process, and the owner's D1 ruling — "the http path is the
    only really truthful one" — makes it the one that matters most.

    UNWRAP (the status-code/envelope handling) delegates to
    ``tests.harness.client.unwrap_rest_response`` —
    the one REST unwrap shared with the in-process ``RestDispatcher`` and the
    generic client's ``_unwrap_rest``. It derives the envelope tag from
    ``Transport.E2E_REST.value`` (``"e2e_rest"``) and keeps the graceful
    non-JSON-body fallback (#1420) that e2e_rest — the only e2e transport
    running today — has always had as its regression baseline, and which the
    verifier's BODYLESS 401 now lands on too: the response survives on the
    result, so ``assert_signature_challenge`` can read the challenge off it.

    Ported from feature/media-buy-refactoring (PR #1360 lineage).
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        from tests.harness.address_table import ToolAddress
        from tests.harness.client import _deliver_e2e_rest, unwrap_rest_response
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        if signed and not env.can_sign:
            env.signing  # raises, naming enable_request_signing()  # noqa: B018
        if not env.e2e_config:
            return TransportResult(
                error=RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)"), has_wire=False
            )  # no e2e_config: refused before any httpx call

        # NO_IDENTITY_OVERRIDE default (not ``{}``): an omitted credential must fall
        # back to env.credential() inside the delivery function, the same default every
        # other transport's omitted-credential dispatch gets — a bare ``{}`` default
        # here would send every omitted-credential call unauthenticated instead. An
        # EXPLICIT ``credential={}`` still means "present nothing", which is the only
        # way this leg reaches the verifier's refusal branch at all: security.mdx
        # :1269 makes an unsigned request carrying a valid bearer a correct 200.
        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        body = env.build_rest_body(**kwargs)
        endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
        method = _effective_rest_method(getattr(env, "REST_METHOD", "post"), has_params=bool(kwargs))

        if env.can_sign:
            response = _deliver_e2e_rest_signed(
                env,
                base_url=env.e2e_config.base_url,
                endpoint=endpoint,
                method=method,
                body=body,
                credential=credential,
                signed=signed,
            )
        else:
            address = ToolAddress(Transport.E2E_REST, name=endpoint, method=method)
            response = _deliver_e2e_rest(env, address, {"url": endpoint, "body": body}, credential)
        return unwrap_rest_response(env, response, Transport.E2E_REST, env.parse_rest_response)


class McpE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack's MCP endpoint.

    Delegates to ``AdCPTestClient`` (``tests/harness/client.py``,
    the wire-grading work) instead of duplicating the
    ADDRESS/WRAP/DELIVER/UNWRAP logic here a second time — ``client.call()``
    already builds the real ``fastmcp.Client`` against
    ``env.e2e_config.base_url`` and unwraps the response identically to the
    in-process ``McpDispatcher`` above (design doc §5).

    Unlike the other dispatchers on this legacy ``env.call_via(transport,
    **kwargs)`` path, per-env subclasses hardcode their MCP tool name as a
    string literal inside their dispatch override (e.g. ``ProductEnv`` calls
    ``self._run_mcp_client("get_products", ...)``) — there is no attribute to
    introspect it from generically, and unlike ``RestE2EDispatcher`` (which
    reads ``env.REST_ENDPOINT``/``env.REST_METHOD``) no env exposes an MCP
    equivalent. This dispatcher was a ``NotImplementedError`` placeholder with
    zero callers (no env ever dispatched ``Transport.E2E_MCP`` through
    ``call_via``), so this is not a breaking-change surface: callers must pass
    ``tool_name=`` explicitly in kwargs, the same tool identity
    ``AdCPTestClient.call()``'s first positional argument already requires.

    ``signed`` is consumed and REFUSED — see ``_refuse_signed_generic_client``:
    this leg's delivery does not pass through ``wire_request``, so honoring a
    realization here could only mean silently sending an unsigned request.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        from tests.harness.client import _dispatch_core, flatten_payload
        from tests.harness.transport import NO_IDENTITY_OVERRIDE, MissingToolNameError, Transport

        if signed:
            _refuse_signed_generic_client(signed, Transport.E2E_MCP)

        tool_name = kwargs.pop("tool_name", None)
        if tool_name is None:
            raise MissingToolNameError(
                "McpE2EDispatcher.dispatch requires tool_name= in kwargs (e.g. "
                'env.call_via(Transport.E2E_MCP, tool_name="get_products", req=...)) — '
                "there is no per-env attribute to derive it from generically. "
                "Prefer AdCPTestClient(env).call(tool_name, payload, Transport.E2E_MCP) directly."
            )

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        req = kwargs.pop("req", None)
        payload = flatten_payload(req, **kwargs)

        return _dispatch_core(env, Transport.E2E_MCP, tool_name, payload, credential)


class A2AE2EDispatcher:
    """Dispatch via a real JSON-RPC ``message/send`` HTTP request to the live A2A endpoint.

    Unlike ``RestE2EDispatcher`` (which reuses each env's hand-written
    ``REST_ENDPOINT``/``build_rest_body``/``parse_rest_response`` overrides),
    this delegates entirely to ``AdCPTestClient``/``_deliver_e2e_a2a``
    (``tests/harness/client.py``, the wire-grading work) — the address,
    JSON-RPC envelope construction, and Task-state handling all live there,
    derived from the live ``create_agent_card()`` registration
    (``tests/harness/address_table.py``), not re-implemented per-env.

    Tool-name threading: unlike ``AdCPTestClient.call(tool, payload,
    transport)`` (which takes the tool name explicitly), the legacy
    ``env.call_via(transport, **kwargs)`` entry point this dispatcher is
    reached through carries no tool-name parameter — every OTHER dispatcher
    sidesteps this because the env subclass's own ``deliver_a2a``/``deliver_mcp``
    override already has the tool name hard-coded in its body (e.g.
    ``self._run_a2a_handler("get_products", ...)``). Since this
    dispatcher must call the generic client instead of an env override, the
    caller supplies the tool name explicitly via a ``tool_name=`` kwarg (or
    an ``env.A2A_SKILL`` class attribute, for envs that want to declare it
    once) — same open question the wire-grading work's ``McpE2EDispatcher`` faces for
    ``Transport.E2E_MCP``, resolved independently here since neither
    dispatcher's fix depends on the other's.

    ``signed`` is consumed and REFUSED, for the reason given on
    ``McpE2EDispatcher``. The in-process ``Transport.A2A`` leg signs; this one
    cannot, and running it unsigned would grade a signing scenario green having
    signed nothing.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        from tests.harness.client import _dispatch_core, flatten_payload
        from tests.harness.transport import NO_IDENTITY_OVERRIDE, MissingToolNameError, Transport

        if signed:
            _refuse_signed_generic_client(signed, Transport.E2E_A2A)

        credential = kwargs.pop("credential", NO_IDENTITY_OVERRIDE)
        tool_name = kwargs.pop("tool_name", None) or getattr(env, "A2A_SKILL", None)
        if not tool_name:
            raise MissingToolNameError(
                "A2AE2EDispatcher.dispatch() needs a tool/skill name to resolve an address via "
                "AdCPTestClient — pass tool_name=... to env.call_via(Transport.E2E_A2A, ...) (or "
                "declare env.A2A_SKILL), or call AdCPTestClient(env).call(tool, payload, "
                "Transport.E2E_A2A) directly instead — the primary path this design promotes "
                "(see tests/harness/client.py — rewriting per-env shaping is a non-goal)."
            )

        req = kwargs.pop("req", None)
        payload = flatten_payload(req, **kwargs)

        return _dispatch_core(env, Transport.E2E_A2A, tool_name, payload, credential)


DISPATCHERS: dict[
    Transport,
    A2ADispatcher | RestDispatcher | McpDispatcher | RestE2EDispatcher | McpE2EDispatcher | A2AE2EDispatcher,
] = {
    Transport.A2A: A2ADispatcher(),
    Transport.REST: RestDispatcher(),
    Transport.MCP: McpDispatcher(),
    Transport.E2E_REST: RestE2EDispatcher(),
    Transport.E2E_MCP: McpE2EDispatcher(),
    Transport.E2E_A2A: A2AE2EDispatcher(),
}

"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

On error, dispatchers capture the wire error envelope (the raw two-layer dict
the buyer would see) alongside the reconstructed exception.  New tests should
assert on ``result.wire_error_envelope`` via ``assert_envelope_shape()`` — see
``tests/CLAUDE.md`` § Error Verification Policy.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.A2A]
    result = dispatcher.dispatch(env, **kwargs)
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tests.harness.transport import Transport, TransportResult

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv
    from tests.helpers.signing import SignatureRealization


def _envelope_from_adcp_error(exc: Exception) -> dict[str, Any] | None:
    """Build a SYNTHESIZED envelope from an AdCPError instance.

    Used by ImplDispatcher to populate the separate
    ``synthesized_error_envelope`` field — IMPL has no wire by definition
    and ``wire_error_envelope`` is reserved for real wire bytes captured
    by REST/MCP/A2A. Production code uses the same
    ``build_two_layer_error_envelope`` helper at the boundary, so the
    synthesized envelope matches what production would emit for the same
    exception. It does NOT verify that a regression in
    ``build_two_layer_error_envelope`` actually reaches the wire.

    A2A and REST tests asserting on ``result.wire_error_envelope`` see
    REAL wire bytes:
        - A2A: the artifact DataPart, attached to the reconstructed
          ``AdCPError`` as ``_wire_error_envelope`` by
          ``tests.harness._base._envelope_to_adcp_error``.
        - REST: the HTTP response body, captured directly by RestDispatcher.
        - MCP: the JSON string in ``ToolError``, parsed by McpDispatcher.
    """
    from src.core.exceptions import AdCPError, build_two_layer_error_envelope

    if isinstance(exc, AdCPError):
        return build_two_layer_error_envelope(exc)
    return None


def _wire_envelope_from_exception(exc: Exception) -> dict[str, Any] | None:
    """Prefer the REAL wire envelope stashed by the harness; fall back to synthesized.

    When the A2A pipeline reconstructs an AdCPError from a failed Task's
    artifact DataPart, ``tests.harness._base._envelope_to_adcp_error``
    attaches the captured envelope to the exception as
    ``_wire_error_envelope``. This helper returns that real wire envelope
    if present; otherwise falls back to ``_envelope_from_adcp_error``
    (synthesized — same helper production calls).
    """
    real_wire = getattr(exc, "_wire_error_envelope", None)
    if isinstance(real_wire, dict):
        return real_wire
    return _envelope_from_adcp_error(exc)


def _refusal_response(exc: Exception) -> Any | None:
    """The raw HTTP response a JSON-RPC leg was refused with, if it was.

    The ``/a2a`` and ``/mcp`` legs read an ENVELOPE, so a refusal that never
    produced one (the verifier's bodyless 401) can only be graded from the
    response itself. ``_base.WireRefusal`` carries it; every other exception
    yields ``None``, which ``assert_signature_challenge`` reports as "no raw HTTP
    response" rather than passing for want of evidence.
    """
    from tests.harness._base import WireRefusal

    return exc.response if isinstance(exc, WireRefusal) else None


def _envelope_from_mcp_error(exc: Exception) -> dict[str, Any] | None:
    """Extract the wire envelope from an MCP ToolError's JSON string."""
    from fastmcp.exceptions import ToolError

    if not isinstance(exc, ToolError):
        return None
    try:
        envelope = json.loads(str(exc))
        if isinstance(envelope, dict) and "errors" in envelope:
            return envelope
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _non_json_error_result(response: Any, envelope: dict[str, Any]) -> TransportResult:
    """One result shape for a 4xx/5xx whose body is not JSON, on EVERY REST leg.

    A BODYLESS rejection is not a parse accident: the ASGI signature verifier
    answers 401 with the challenge in ``WWW-Authenticate`` and NO body at all
    (``_reject``, ``request_verifier_middleware``). Both REST legs must survive
    that and keep the response, or ``assert_signature_challenge`` has nothing to
    read.

    They must also produce the SAME ``error``. The in-process leg previously
    returned the raw ``JSONDecodeError`` while the e2e leg built a typed
    ``AdCPError`` — so one transport-blind scenario saw two different
    ``ctx["error"]`` types depending on which leg ran it. The unsigned refusal
    IS a bodyless 401, so that divergence lands exactly on the scenarios S1.3
    adds; a cross-transport claim that resolves differently per transport is the
    defect this epic exists to remove, not a detail. One helper, both callers,
    so they cannot drift again.

    No ``wire_error_envelope``: there is no structured body to expose. The
    status and the raw text ride on ``details`` so a Then step can tell a server
    crash apart from a real rejection (#1420).
    """
    from src.core.exceptions import AdCPError

    body_text = response.text or "(empty body)"
    error = AdCPError(
        f"HTTP {response.status_code}: {body_text}",
        details={"status_code": response.status_code, "raw_body": body_text},
    )
    error.status_code = response.status_code
    return TransportResult(payload=None, envelope=envelope, error=error, raw_response=response)


def _refuse_signed_impl(signed: SignatureRealization) -> None:
    """Fail loudly when ANY signature realization reaches ``impl``, which has no wire.

    ANY, not only ``signed=True``: since ``signed`` widened to carry failure
    realizations (``"malformed"`` / ``"tampered"``,
    :data:`tests.helpers.signing.SIGNATURE_REALIZATIONS`) the caller above refuses on
    TRUTHINESS, which is what keeps that widening from re-opening this hole — a
    malformed signature has exactly as little meaning on a direct function call as a
    valid one. Graded, at last, by
    ``tests/integration/test_harness_signed_dispatch.py``
    ``test_impl_refuses_every_signature_realization``: this function had two
    references and no test until salesagent-nx8jp.9.

    All four WIRE legs — ``rest``, ``a2a``, ``mcp`` and ``e2e_rest`` — realize a
    real signature over the real HTTP path (``salesagent-n78j0.1.1``). ``IMPL``
    is not an oversight and is not "next": it is a direct in-process function
    call with nothing between the caller and ``_impl``, so there is nothing to
    sign and no verifier to grade it. It stays a refusal permanently.

    Refusing rather than ignoring ``signed`` is the whole discipline: an
    unsigned send here would let a signing scenario PASS on a transport that
    never signed anything, which is precisely the class of hole S1 exists to
    close (the A2A credential-location bypass survived because the property was
    asserted by code shape and never observed).
    """
    raise NotImplementedError(
        f"call_via(signed={signed!r}) has no meaning on transport 'impl': a direct _impl call "
        "puts nothing on a wire, so there is nothing for RequestSignatureMiddleware to "
        "verify — a malformed or tampered signature has exactly as little meaning there as a "
        "valid one. Dispatch the scenario over rest, a2a, mcp or e2e_rest. Refusing rather "
        "than running unsigned, which would make a signing scenario pass without a signature."
    )


class ImplDispatcher:
    """Dispatch via direct ``_impl()`` call.

    IMPL is the in-process direct call — there is no wire by definition.
    ``wire_error_envelope`` is left ``None`` on this transport; the envelope
    that production WOULD emit at the boundary is exposed on the separate
    ``synthesized_error_envelope`` field so tests cannot accidentally lean
    on IMPL to catch real-wire regressions (a regression in the production
    boundary translator would not change what this dispatcher computes,
    because both call ``build_two_layer_error_envelope`` on the same
    in-memory exception). Use A2A, REST, or MCP for wire-shape coverage.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        if signed:
            _refuse_signed_impl(signed)
        try:
            payload = env.call_impl(**kwargs)
        except Exception as exc:
            return TransportResult(
                error=exc,
                synthesized_error_envelope=_envelope_from_adcp_error(exc),
            )
        return TransportResult(payload=payload, envelope={"transport": "impl"})


def a2a_transport_result(
    call: Callable[[], Any],
    *,
    wire_response_of: Callable[[], dict[str, Any] | None] | None = None,
) -> TransportResult:
    """One ``TransportResult`` out of any dispatch onto the ``/a2a`` wire.

    Stated once because A2A has MORE THAN ONE thing a buyer can send it — a
    skill invocation and a bare credential registration
    (``tasks/pushNotificationConfig/set``) — and both have to produce results a
    scenario can grade side by side. A second copy of this wrapping would be
    free to drop the refusal's raw response, which
    ``assert_signature_challenge`` reports as "no wire to read" rather than as
    the acceptance it actually is.

    *wire_response_of* is read only on success, and only by callers that stash a
    success-path wire capture; a caller that does not stash passes nothing rather
    than handing over some other dispatch's capture.
    """
    try:
        payload = call()
    except Exception as exc:
        return TransportResult(
            error=exc,
            wire_error_envelope=_wire_envelope_from_exception(exc),
            raw_response=_refusal_response(exc),
        )
    return TransportResult(
        payload=payload,
        envelope={"transport": "a2a"},
        wire_response=wire_response_of() if wire_response_of is not None else None,
    )


class A2ADispatcher:
    """Dispatch via ``handler.on_message_send`` — exercises the full A2A pipeline.

    ``env.call_a2a`` drives ``AdCPRequestHandler.on_message_send`` end-to-end
    (message parsing → skill routing → handler dispatch → ``_serialize_for_a2a``
    → Task/Artifact framing). On a failed Task, the harness reconstructs the
    ``AdCPError`` from the artifact DataPart and stashes the real wire
    envelope on the exception via ``_wire_error_envelope`` — captured here
    by ``_wire_envelope_from_exception``.

    ``signed`` is not consumed here. Whether a signature is realizable is a fact
    about the ENV (does it have a counterparty key?), not about this class, and
    the answer changes the TRANSPORT: once the env can sign, ``_run_a2a_handler``
    routes through a real ``POST /a2a`` on ``src.app.app``, because an
    ``on_message_send`` call has no wire for the ASGI verifier to see. The flag
    reaches that method on the env — see ``BaseTestEnv._signed_dispatch``. An env
    with no capability raises from ``env.signing``, naming
    ``enable_request_signing()``.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        # Real A2A wire on success: the artifact DataPart dict stashed by
        # _run_a2a_handler (declared on BaseTestEnv, reset per call_via — read
        # directly so a missed capture surfaces as None against a known
        # attribute, not getattr).
        return a2a_transport_result(
            lambda: env.call_a2a(**kwargs),
            wire_response_of=lambda: env._last_wire_response,
        )


class RestDispatcher:
    """Dispatch via FastAPI TestClient → route → _raw() → _impl().

    Identity flows through kwargs to env._run_rest_request(), which pops it
    and configures the FastAPI auth dep override per-request.

    Unlike other dispatchers, REST includes HTTP metadata in the envelope
    (status_code, content_type) since tests may assert on these.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        try:
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = env._run_rest_request(endpoint, signed=signed, **kwargs)

            envelope = {
                "transport": "rest",
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
            }

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:  # noqa: BLE001 - surfaced as the result's error
                    # Letting the decode error escape to the outer handler
                    # discarded the response — and with it the only evidence of
                    # WHICH refusal happened, leaving assert_signature_challenge
                    # nothing to read. Shared with the e2e leg so both produce the
                    # same error shape (salesagent-n78j0.1.2).
                    return _non_json_error_result(response, envelope)
                error = env.parse_rest_error(response.status_code, body)
                return TransportResult(
                    error=error,
                    envelope=envelope,
                    raw_response=response,
                    wire_error_envelope=body,
                )

            body = response.json()
            # Parse a COPY: env parsers strip envelope keys in place (e.g.
            # _parse_update_rest_response pops "status", #1417), which
            # would silently delete fields from the stashed wire capture — the
            # dispatcher owns the pristine-wire guarantee (#1417).
            payload = env.parse_rest_response(copy.deepcopy(body))
            # Real REST wire: the HTTP JSON body dict.
            return TransportResult(payload=payload, envelope=envelope, raw_response=response, wire_response=body)
        except Exception as exc:
            return TransportResult(error=exc)


class McpDispatcher:
    """Dispatch via Client(mcp) — full FastMCP pipeline.

    Identity flows through kwargs to env.call_mcp() → _run_mcp_client(),
    which pops it and dispatches via FastMCP in-memory transport.

    ``signed`` is not consumed here, for the reason given on ``A2ADispatcher``:
    an env that can sign makes ``_run_mcp_client`` drive a real streamable-HTTP
    session against ``src.app.app`` instead of FastMCP's in-memory object
    streams, which carry no headers and never reach the ASGI verifier.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_mcp(**kwargs)
        except Exception as exc:
            # REAL wire only: the raw MCP ToolError JSON when present, else
            # the envelope the harness reconstruction stashed on the AdCPError
            # as ``_wire_error_envelope`` (same stash A2A uses). NEVER the
            # synthesized fallback — a dead MCP wire path must yield None here
            # (failing assert_envelope_shape), not an envelope regenerated
            # from the lossy reconstructed exception.
            wire = _envelope_from_mcp_error(exc) or getattr(exc, "_wire_error_envelope", None)
            # When a wire envelope came from the raw ToolError JSON, exc is an
            # AdCPToolError carrying that envelope (an env that dispatched through
            # the production with_error_logging boundary). Unwrap it so
            # result.error is the typed AdCPError — error-code assertions resolve
            # to the real wire code, not "AdCPToolError". Typed errors (raw JSON
            # absent, the path taken by every _run_mcp_client-based env, which
            # unwraps internally) pass through unchanged, so this is a no-op for
            # them.
            error = exc
            if _envelope_from_mcp_error(exc) is not None:
                from tests.harness._base import _unwrap_mcp_tool_error

                error = _unwrap_mcp_tool_error(exc)
            return TransportResult(
                error=error,
                wire_error_envelope=wire,
                # What production WOULD emit for the same exception — see the
                # ImplDispatcher caveat; never a substitute for the wire field.
                synthesized_error_envelope=_envelope_from_adcp_error(exc),
                raw_response=_refusal_response(exc),
            )
        # Real MCP wire: the structured_content dict stashed by _run_mcp_client
        # (declared on BaseTestEnv, reset per call_via — read directly).
        return TransportResult(
            payload=payload,
            envelope={"transport": "mcp"},
            wire_response=env._last_wire_response,
        )


class RestE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack.

    Uses httpx to send POST requests to the live server, exercising the full
    stack: nginx -> UnifiedAuthMiddleware -> resolve_identity() ->
    get_principal_from_token() DB lookup -> route handler -> _impl().

    Reuses the env's REST contract (build_rest_body / REST_ENDPOINT /
    parse_rest_response / parse_rest_error); the only e2e-specific dependency is
    ``env.e2e_config`` (base_url of the live stack), set by the bdd conftest for
    E2E scenarios. Ported from feature/media-buy-refactoring (PR #1360 lineage).

    THE SIGNED PATH (salesagent-n78j0.1.1). This is the only leg that leaves the
    process, and the owner's D1 ruling — "the http path is the only really
    truthful one" — makes it the one that matters most. Once the env can sign,
    both the signed and the unsigned dispatch go through ``env.wire_request``,
    the same seam the three in-process legs use, which owns all three rules a
    signed request obeys (serialize once and send exactly those bytes; carry the
    same credential and tenant hint whether or not you sign; one identity, on
    ``Authorization``, and the env's own tenant hint). ONE thing is this leg's OWN
    and is stated at the call site rather than inside that seam, because only this
    leg has it: the ORIGIN is the live stack's, port included — nginx forwards
    ``Host`` verbatim and ``_verify_url`` rebuilds the authority from it.

    The tenant hint used to be this leg's too, for a reason that has since become
    every leg's: ``request_headers`` injected the module-level ``sig_tenant``, which
    does not exist in the live database, and a header asserting a tenant other than
    the one being addressed is a lie inside the signed byte range — one ladder
    reordering away from collapsing the posture bucket to ``none``, an unverified
    pass-through wearing a 200. ``wire_request`` now sets it for all four legs.
    """

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        import httpx

        if signed and not env.can_sign:
            env.signing  # raises, naming enable_request_signing()  # noqa: B018
        if not env.e2e_config:
            return TransportResult(error=RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)"))

        identity = kwargs.pop("identity", None)
        base_url = env.e2e_config.base_url

        # identity=None means "send without auth headers" (no-auth test) — let the
        # server's auth middleware return 401/structured error. When identity exists
        # but auth_token is None (principal_id=None boundary tests), omit the header
        # so the server rejects gracefully instead of httpx raising on a None header.
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if identity is not None:
            if identity.auth_token is not None:
                headers["x-adcp-auth"] = identity.auth_token
            tenant = getattr(identity, "tenant", None)
            if tenant is not None:
                subdomain = tenant.get("subdomain") if isinstance(tenant, dict) else getattr(tenant, "subdomain", None)
                if subdomain is not None:
                    headers["x-adcp-tenant"] = subdomain
            tc = getattr(identity, "testing_context", None)
            if tc is not None and getattr(tc, "dry_run", False):
                headers["x-dry-run"] = "true"

        body = env.build_rest_body(**kwargs)
        endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]

        method = getattr(env, "REST_METHOD", "post")
        bodyless_get = method == "get" and not kwargs

        raw: bytes | None = None
        if env.can_sign:
            from tests.helpers.signing import wire_origin

            extra = {}
            if "x-dry-run" in headers:
                extra["x-dry-run"] = headers["x-dry-run"]
            raw, headers = env.wire_request(
                path=endpoint,
                body=None if bodyless_get else body,
                signed=signed,
                extra=extra,
                origin=wire_origin(base_url),
                credentialed=identity is not None,
                method="GET" if bodyless_get else "POST",
            )

        with httpx.Client(base_url=base_url, timeout=30) as client:
            # GET-with-no-body only when no params were supplied at all — mirrors
            # the in-process _run_rest_request dispatch (e.g. CapabilitiesEnv:
            # parameterless GET /api/v1/capabilities happy path vs POST
            # /api/v1/capabilities when protocols/context/adcp_version are set,
            # salesagent-5yik).
            if raw is not None:
                # Signed or not, the bytes signed are the bytes sent — httpx's
                # `json=` re-serializes with its own separators, which is refused
                # as request_signature_digest_mismatch.
                response = client.request(
                    "GET" if bodyless_get else "POST",
                    endpoint,
                    content=raw,
                    headers=headers,
                )
            elif bodyless_get:
                response = client.get(endpoint, headers=headers)
            elif method == "get":
                response = client.post(endpoint, json=body, headers=headers)
            else:
                response = getattr(client, method)(endpoint, json=body, headers=headers)

        envelope = {
            "transport": "e2e_rest",
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }

        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                # Non-JSON error (e.g. 500 with empty body, or the verifier's
                # bodyless 401) — wrapped as AdCPError so Then steps detect the
                # error type and xfail spec-production gaps. Shared with the
                # in-process leg: one shape, both transports.
                return _non_json_error_result(response, envelope)
            # Structured JSON error: mirror the in-process RestDispatcher and expose
            # the raw two-layer body as wire_error_envelope so error Then-steps assert
            # on the buyer-visible envelope (e.g. uc004 _assert_wire_rejection, or
            # assert_envelope_shape) instead of a lossy reconstructed exception. (#1420)
            error = env.parse_rest_error(response.status_code, body)
            return TransportResult(
                payload=None,
                envelope=envelope,
                error=error,
                wire_error_envelope=body,
                raw_response=response,
            )

        try:
            wire_response = response.json()
            # Parse a COPY — same pristine-wire guarantee as the in-process
            # RestDispatcher (parsers strip envelope keys in place, #1417).
            payload = env.parse_rest_response(copy.deepcopy(wire_response))
        except Exception as exc:
            return TransportResult(payload=None, envelope=envelope, error=exc, raw_response=response)

        # Real HTTP wire body — the e2e analogue of the in-process RestDispatcher's
        # wire_response (parallel to wire_error_envelope on the error path), so
        # success-path wire-shape steps grade the live server too instead of
        # re-deriving from the typed payload (#rlgl.3).
        return TransportResult(
            payload=payload,
            envelope=envelope,
            error=None,
            wire_response=wire_response,
            raw_response=response,
        )


class McpE2EDispatcher:
    """Placeholder for real MCP E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_MCP dispatcher is not yet implemented. Use Transport.MCP for in-process MCP dispatch."
        )


class A2AE2EDispatcher:
    """Placeholder for real A2A E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, *, signed: SignatureRealization = False, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_A2A dispatcher is not yet implemented. Use Transport.A2A for in-process A2A dispatch."
        )


DISPATCHERS: dict[
    Transport,
    ImplDispatcher
    | A2ADispatcher
    | RestDispatcher
    | McpDispatcher
    | RestE2EDispatcher
    | McpE2EDispatcher
    | A2AE2EDispatcher,
] = {
    Transport.IMPL: ImplDispatcher(),
    Transport.A2A: A2ADispatcher(),
    Transport.REST: RestDispatcher(),
    Transport.MCP: McpDispatcher(),
    Transport.E2E_REST: RestE2EDispatcher(),
    Transport.E2E_MCP: McpE2EDispatcher(),
    Transport.E2E_A2A: A2AE2EDispatcher(),
}

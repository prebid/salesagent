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

from typing import TYPE_CHECKING, Any

from tests.harness.transport import (
    Transport,
    TransportResult,
    _envelope_from_adcp_error,
)

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv

# _envelope_from_adcp_error lives in transport.py, not here — both this module
# and client.py need it, and housing it in either would recreate the mutual
# lazy-import cycle untangled later (client.py used to lazily
# import it back from this module, while this module lazily imports dispatch
# functions FROM client.py — the two together being the "mutual" part).
#
# The MCP/A2A/REST error-path unwraps themselves are NOT re-implemented here:
# this module delegates to client.py's unwrap_mcp_error / unwrap_a2a_error /
# unwrap_rest_error, so there is one error unwrap per transport family for both
# dispatch paths (CLAUDE.md DRY invariant; remediation finding 1).
#
# Two invariants this module used to hold in its own helpers travel WITH those
# unwraps and must keep holding at their new home:
#   - ``wire_error_envelope`` carries REAL wire bytes or None — NEVER an
#     envelope the harness rebuilt from the exception it just caught. A
#     scenario asserting on that field would otherwise grade the rebuild, which
#     passes whether or not production emitted anything at all. A transport
#     that genuinely has no wire says so through ``has_wire=False`` and offers
#     ``_synthesized_error_envelope`` under its own name, as ImplDispatcher
#     does below.
#   - ``has_wire`` is declared PER CONSTRUCTION SITE (required and keyword-only
#     on TransportResult), True only downstream of an actual send/receive; a
#     catch-all arm that may fire before anything was sent declares False.


class ImplDispatcher:
    """Dispatch via direct ``_impl()`` call.

    IMPL is the in-process direct call — there is no wire by definition.
    ``wire_error_envelope`` is left ``None`` on this transport; the envelope
    that production WOULD emit at the boundary is exposed on the separate
    private ``_synthesized_error_envelope`` field so tests cannot accidentally lean
    on IMPL to catch real-wire regressions (a regression in the production
    boundary translator would not change what this dispatcher computes,
    because both call ``build_two_layer_error_envelope`` on the same
    in-memory exception). Use A2A, REST, or MCP for wire-shape coverage.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_impl(**kwargs)
        except Exception as exc:
            return TransportResult(
                has_wire=False,  # in-process call, no wire exists
                error=exc,
                _synthesized_error_envelope=_envelope_from_adcp_error(exc),
            )
        return TransportResult(
            payload=payload, envelope={"transport": "impl"}, has_wire=False
        )  # in-process call, no wire exists


class A2ADispatcher:
    """Dispatch via ``handler.on_message_send`` — exercises the full A2A pipeline.

    ``env.call_a2a`` for envs that drive ``AdCPRequestHandler.on_message_send``
    end-to-end (message parsing → skill routing → handler dispatch →
    ``_serialize_for_a2a`` → Task/Artifact framing) stashes the real
    Task/Artifact DataPart. On a failed Task, the harness reconstructs the
    ``AdCPError`` from the artifact DataPart and stashes the real wire
    envelope on the exception via ``_wire_error_envelope`` — read off it by
    ``client.py``'s ``unwrap_a2a_error``, the one A2A error unwrap.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            delivered = env.deliver_a2a(**kwargs)
        except Exception as exc:
            from tests.harness.client import unwrap_a2a_error

            # ONE A2A error unwrap for both dispatch paths (client.py). This
            # used to be a second copy of that body, which is how the derived
            # status ended up on this path and not on AdCPTestClient.call — the
            # path the graded storyboard scenarios actually take. It reads the
            # REAL envelope off the exception and must never hand back a
            # synthesized stand-in under ``wire_error_envelope`` — see the
            # module note above.
            return unwrap_a2a_error(exc, Transport.A2A)
        # Real A2A wire: the artifact DataPart dict, carried back on the SAME
        # return value as the payload. It used to be read off env._last_wire_response
        # — one object reaching into another's private attribute, which is what
        # allowed a second writer and a stale wire.
        return TransportResult(
            has_wire=True,  # the artifact DataPart came back from the handler
            payload=delivered.payload,
            envelope={"transport": "a2a"},
            wire_response=delivered.wire_response,
        )


class RestDispatcher:
    """Dispatch via FastAPI TestClient → route → _raw() → _impl().

    Identity flows through kwargs to env._run_rest_request(), which pops it
    and configures the FastAPI auth dep override per-request.

    Unlike other dispatchers, REST includes HTTP metadata in the envelope
    (status_code, content_type) since tests may assert on these.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        from tests.harness.client import unwrap_rest_error, unwrap_rest_response

        try:
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = env._run_rest_request(endpoint, **kwargs)
        except Exception as exc:
            # ONE REST DELIVER-exception unwrap for both dispatch paths — it
            # derives status=transport_fault and declares has_wire=False,
            # because an exception here means no HTTP body, hence no AdCP
            # envelope, ever existed.
            return unwrap_rest_error(exc, Transport.REST)
        # unwrap_rest_response owns the status-code
        # branching, envelope tag, the #1417 pristine-wire deepcopy rule, and the
        # per-site has_wire declaration (True on every branch it returns: a
        # response — 2xx or >=400 — means bytes came back over HTTP) —
        # the same function RestE2EDispatcher and the generic client's
        # _unwrap_rest delegate to below.
        return unwrap_rest_response(env, response, Transport.REST, env.parse_rest_response)


class McpDispatcher:
    """Dispatch via Client(mcp) — full FastMCP pipeline.

    Identity flows through kwargs to env.call_mcp() → _run_mcp_client(),
    which pops it and dispatches via FastMCP in-memory transport.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            delivered = env.deliver_mcp(**kwargs)
        except Exception as exc:
            from tests.harness.client import unwrap_mcp_error

            # ONE MCP error unwrap for both dispatch paths (client.py) — it owns
            # the raw-ToolError unwrap, the REAL-wire-only envelope rule (never
            # the synthesized fallback), the per-site has_wire declaration, and
            # the derived status. See the A2A sibling above for why this is a
            # delegation and not a copy.
            return unwrap_mcp_error(exc, Transport.MCP)
        # Real MCP wire: the structured_content dict, carried back on the SAME
        # return value as the payload — see the A2A sibling above.
        return TransportResult(
            has_wire=True,  # structured_content came back from the MCP client
            payload=delivered.payload,
            envelope={"transport": "mcp"},
            wire_response=delivered.wire_response,
        )


class RestE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack.

    Exercises the full stack: nginx -> UnifiedAuthMiddleware ->
    resolve_identity() -> get_principal_from_token() DB lookup -> route
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
    (the wire-grading work; design doc §5).

    UNWRAP (the status-code/envelope handling) delegates to
    ``tests.harness.client.unwrap_rest_response`` —
    the one REST unwrap shared with the in-process ``RestDispatcher`` and the
    generic client's ``_unwrap_rest``. It derives the envelope tag from
    ``Transport.E2E_REST.value`` (``"e2e_rest"``) and keeps the graceful
    non-JSON-body fallback (#1420) that e2e_rest — the only e2e transport
    running today — has always had as its regression baseline.

    Ported from feature/media-buy-refactoring (PR #1360 lineage).
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        from tests.harness.address_table import ToolAddress
        from tests.harness.client import _deliver_e2e_rest, unwrap_rest_response
        from tests.harness.transport import NO_IDENTITY_OVERRIDE

        if not env.e2e_config:
            return TransportResult(
                error=RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)"), has_wire=False
            )  # no e2e_config: refused before any httpx call

        # NO_IDENTITY_OVERRIDE default (not None): omitted identity must fall
        # back to env.identity_for(transport) inside _deliver_e2e_rest, the
        # same resolution every other transport's omitted-identity dispatch
        # gets — a bare ``None`` default here would force every omitted-
        # identity call unauthenticated instead.
        identity = kwargs.pop("identity", NO_IDENTITY_OVERRIDE)
        body = env.build_rest_body(**kwargs)
        endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
        method = getattr(env, "REST_METHOD", "post")
        address = ToolAddress(Transport.E2E_REST, name=endpoint, method=method)

        response = _deliver_e2e_rest(env, address, {"url": endpoint, "body": body}, identity)
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
    string literal inside ``call_mcp()`` (e.g. ``ProductEnv.call_mcp`` calls
    ``self._run_mcp_client("get_products", ...)``) — there is no attribute to
    introspect it from generically, and unlike ``RestE2EDispatcher`` (which
    reads ``env.REST_ENDPOINT``/``env.REST_METHOD``) no env exposes an MCP
    equivalent. This dispatcher was a ``NotImplementedError`` placeholder with
    zero callers (no env ever dispatched ``Transport.E2E_MCP`` through
    ``call_via``), so this is not a breaking-change surface: callers must pass
    ``tool_name=`` explicitly in kwargs, the same tool identity
    ``AdCPTestClient.call()``'s first positional argument already requires.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        from tests.harness.client import _dispatch_core, flatten_payload
        from tests.harness.transport import NO_IDENTITY_OVERRIDE, MissingToolNameError, Transport

        tool_name = kwargs.pop("tool_name", None)
        if tool_name is None:
            raise MissingToolNameError(
                "McpE2EDispatcher.dispatch requires tool_name= in kwargs (e.g. "
                'env.call_via(Transport.E2E_MCP, tool_name="get_products", req=...)) — '
                "there is no per-env attribute to derive it from generically. "
                "Prefer AdCPTestClient(env).call(tool_name, payload, Transport.E2E_MCP) directly."
            )

        identity = kwargs.pop("identity", NO_IDENTITY_OVERRIDE)
        req = kwargs.pop("req", None)
        payload = flatten_payload(req, **kwargs)

        return _dispatch_core(env, Transport.E2E_MCP, tool_name, payload, identity)


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
    sidesteps this because the env subclass's own ``call_a2a``/``call_mcp``/
    ``call_rest`` override already has the tool name hard-coded in its body
    (e.g. ``self._run_a2a_handler("get_products", ...)``). Since this
    dispatcher must call the generic client instead of an env override, the
    caller supplies the tool name explicitly via a ``tool_name=`` kwarg (or
    an ``env.A2A_SKILL`` class attribute, for envs that want to declare it
    once) — same open question the wire-grading work's ``McpE2EDispatcher`` faces for
    ``Transport.E2E_MCP``, resolved independently here since neither
    dispatcher's fix depends on the other's.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        from tests.harness.client import _dispatch_core, flatten_payload
        from tests.harness.transport import NO_IDENTITY_OVERRIDE, MissingToolNameError, Transport

        identity = kwargs.pop("identity", NO_IDENTITY_OVERRIDE)
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

        return _dispatch_core(env, Transport.E2E_A2A, tool_name, payload, identity)


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

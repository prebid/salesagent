"""One ADDRESS/WRAP/DELIVER/UNWRAP core for generic and environment callers.

A generic AdCPTestClient preserves the explicitly requested tool and parses its
pinned SDK response model (or None when no single model exists). An environment
owns request-content addressing and its local response model. The shared core
applies those contracts before wrapping and after untyped delivery on every leg.
Legacy REST dispatchers retain their environment endpoint/body builders and use
the shared response reader. They are not migrated into this core.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from tests.harness.a2a_wire import (
    _response_reader,
    parse_response_result,
    unwrap_delivery_error,
)
from tests.harness.address_table import ADDRESS_TABLE, ToolAddress
from tests.harness.spec_models import spec_response_model
from tests.harness.transport import (
    NO_IDENTITY_OVERRIDE,
    DeliverResult,
    Transport,
    TransportResult,
    _envelope_from_mcp_error,
    _wire_envelope_from_exception,
    derive_error_status,
)

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv

# NoAddressForTransport re-exported here for callers that only import
# tests.harness.client (both this module and
# address_table.py as "new files, the transport-generic client builds it" — callers should not
# need to know the map lives in a separate module).
from tests.harness.address_table import NoAddressForTransport  # noqa: F401  (re-export)


def _with_credential(payload: dict[str, Any], credential: Any) -> dict[str, Any]:
    """Copy *payload* and, unless *credential* is the no-override sentinel, add it.

    Shared by the in-process DELIVER functions below — the credential-forwarding
    rule is identical regardless of transport (MCP/A2A/REST-family), only the
    env primitive that pops ``credential`` back out differs.
    """
    kwargs = dict(payload)
    if credential is not NO_IDENTITY_OVERRIDE:
        kwargs["credential"] = credential
    return kwargs


def _presented(env: BaseTestEnv, credential: Any) -> dict[str, str]:
    """The headers an E2E leg sends: *credential*, or the env's own when none was passed."""
    return env.credential() if credential is NO_IDENTITY_OVERRIDE else dict(credential)


def flatten_payload(req: Any, **kwargs: Any) -> dict[str, Any]:
    """Flatten a request model + explicit sibling kwargs into one wire payload.

    The one kwargs-merge policy for the legacy ``env.call_via(transport,
    req=..., **kwargs)`` calling convention (used by the E2E dispatchers in
    ``tests/harness/dispatchers.py``): explicit kwargs always win over the
    model dump, identical regardless of transport. ``req=None`` (or a *req*
    with no ``model_dump``) returns *kwargs* unchanged. Callers that already
    have a flat payload dict (no ``req`` object) should not call this at all.
    """
    if req is not None and hasattr(req, "model_dump"):
        return {**req.model_dump(mode="json", exclude_none=True), **kwargs}
    return dict(kwargs)


# -- WRAP: payload (flat AdCP request dict) -> transport envelope -----------
#
# One function per transport FAMILY — MCP/A2A/REST all
# accept the same flat dict shape on the wire (FastMCP call_tool arguments,
# A2A skill parameters, REST JSON body), so WRAP for the in-process transport
# and its E2E sibling is the literal same function object.


def _wrap_mcp(address: ToolAddress, payload: dict[str, Any]) -> dict[str, Any]:
    """MCP WRAP: no transformation — payload IS the FastMCP call_tool arguments dict."""
    return dict(payload)


def _wrap_a2a(address: ToolAddress, payload: dict[str, Any]) -> dict[str, Any]:
    """A2A WRAP: no transformation — payload becomes the skill ``parameters`` dict.

    Limitation, A2A push-notification injection: production's
    ``on_message_send`` (``src/a2a_server/adcp_a2a_server.py``)
    injects ``push_notification_config`` from the A2A protocol-layer
    ``SendMessageConfiguration``, not from the skill ``parameters`` dict — a
    caller putting ``push_notification_config`` in *payload* here reaches the
    skill as an ordinary parameter, NOT through that protocol-layer injection
    path. Reproducing the injection itself needs ``_run_a2a_handler`` (or its
    caller, ``on_message_send``) to accept a push-notification config
    argument, which it does not today — flagged as a follow-up, not silently
    faked here.
    """
    return dict(payload)


def _wrap_rest(address: ToolAddress, payload: dict[str, Any]) -> dict[str, Any]:
    """REST WRAP: peel ``{name}`` path params out of *payload* into the URL.

    Generalizes what ``MediaBuyDualEnv._run_update_rest_request`` hand-codes
    for exactly one route (``media_buy_id``) into one rule that covers every
    path-parameterized route. The remaining payload keys
    become the JSON body — sent as-is; production's per-route Pydantic
    ``Body`` class (not this WRAP function) is what validates/rejects fields
    that drift from the AdCP request schema (see "REST body != raw
    request model 1:1"), surfacing as a real 422, not a client-side KeyError.

    Returns ``{"url": concrete_path, "body": remaining_payload}`` — ``method``
    lives on *address* already and DELIVER reads it directly.
    """
    body = dict(payload)
    url = address.path_template or ""
    for param in address.path_params:
        if param in body:
            url = url.replace(f"{{{param}}}", str(body.pop(param)))
    return {"url": url, "body": body}


WRAP: dict[Transport, Callable[[ToolAddress, dict[str, Any]], Any]] = {
    Transport.MCP: _wrap_mcp,
    Transport.E2E_MCP: _wrap_mcp,
    Transport.A2A: _wrap_a2a,
    Transport.E2E_A2A: _wrap_a2a,
    Transport.REST: _wrap_rest,
    Transport.E2E_REST: _wrap_rest,
}


# -- DELIVER: wrapped request -> raw transport response (or raise) ----------
#
# In-process DELIVER reuses the env primitives named in the transport-family
# table verbatim (``_run_mcp_client``, ``_run_a2a_handler``,
# ``get_rest_client``) — these already own factory-commit / middleware
# plumbing; DELIVER only adds the tool-name-generic call shape.


def _deliver_mcp(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any) -> DeliverResult:
    kwargs = _with_credential(wrapped, credential)
    # response_cls=dict: _run_mcp_client ends with `response_cls(**structured_content)`;
    # `dict(**d)` is `d`, so this yields the raw structured_content dict instead of a
    # per-tool Pydantic model the client has no way to know generically.
    return env._run_mcp_client(address.name, dict, **kwargs)


def _deliver_a2a(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any) -> TransportResult:
    kwargs = _with_credential(wrapped, credential)
    return env._run_a2a_handler(address.name, **kwargs)


def _deliver_rest(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any) -> Any:
    headers = env._pop_credential(_with_credential({}, credential))
    env._commit_factory_data()
    client = env.get_rest_client()
    method = address.method or "post"
    # The credential rides the request, the same headers ``_run_rest_request`` sends: the
    # boundary resolves the caller from them, and a request without them is a request
    # from nobody, answered AUTH_MISSING on a protected tool whatever principal the env names.
    return getattr(client, method)(wrapped["url"], json=wrapped["body"], headers=headers)


def _deliver_e2e_rest(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any) -> Any:
    """E2E_REST DELIVER: real HTTP through nginx to the live Docker stack.

    The single implementation of e2e_rest delivery (the wire-grading work)
    — ``RestE2EDispatcher`` (``tests/harness/dispatchers.py``) delegates
    here instead of hand-rolling its own header-building/httpx-client
    construction, matching the DELIVER-function split: WRAP
    (``_wrap_rest`` above) and UNWRAP already serve both ``Transport.REST``
    and ``Transport.E2E_REST`` unchanged; only DELIVER differed, and now it
    is one function reused by both the generic client and the dispatcher.

    *wrapped* is ``{"url": ..., "body": ...}`` — the shape ``_wrap_rest``
    produces. Returns the raw ``httpx.Response``; UNWRAP (status-code /
    envelope handling) is the caller's responsibility — ``_unwrap_rest``
    below for the generic ``AdCPTestClient.call()`` path,
    ``RestE2EDispatcher``'s own status-code handling for the dispatcher
    path (see that class's docstring for why the two UNWRAP paths are not
    unified: the e2e_rest envelope/non-JSON-error shape is the standing
    regression baseline and must not shift silently).
    """
    import httpx

    if not env.e2e_config:
        raise RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)")

    headers = {"Content-Type": "application/json", **_presented(env, credential)}
    method = address.method or "post"

    with httpx.Client(base_url=env.e2e_config.base_url, timeout=30) as client:
        return getattr(client, method)(wrapped["url"], json=wrapped["body"], headers=headers)


def _deliver_e2e_mcp(
    env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any
) -> dict[str, Any]:
    """E2E MCP DELIVER: real HTTP via ``fastmcp.Client`` against the live Docker
    stack — the transport ``runStoryboard`` (the real AdCP conformance runner)
    actually speaks (``request_signing.transport = 'mcp'``, agent URLs ending
    ``/mcp``), see the task (the wire-grading work).

    Same call shape as ``_run_mcp_client`` (``tests/harness/_base.py``) —
    ``call_tool`` -> ``structured_content`` -> returned on the ``DeliverResult``
    -> read the ``ToolError``'s envelope with the SAME ``_mcp_wire_envelope``
    helper ``_run_mcp_client`` uses and re-raise those bytes verbatim on a
    ``WireError`` — only the transport under the FastMCP
    ``Client`` changes: a real ``StreamableHttpTransport`` against
    ``env.e2e_config.base_url`` instead of the in-memory ``mcp`` app object.
    The credential flows as real HTTP headers instead of the ``get_http_headers``
    patch ``_run_mcp_client`` installs for in-process dispatch — the live server
    reads them off the wire itself, so nothing needs mocking here.
    """
    import asyncio

    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from tests.harness._base import WireError, _mcp_wire_envelope

    if not env.e2e_config:
        raise RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)")

    # Mirrors _run_mcp_client's unconditional commit — the live
    # server hits its own Postgres via env.e2e_config.postgres_url, so
    # uncommitted factory rows in this test session would be invisible to it.
    env._commit_factory_data()

    headers = _presented(env, credential)
    url = f"{env.e2e_config.base_url}/mcp/"

    async def _call() -> DeliverResult:
        mcp_transport = StreamableHttpTransport(url=url, headers=headers)
        async with Client(transport=mcp_transport) as mcp_client:
            result = await mcp_client.call_tool(address.name, wrapped)
            return DeliverResult(payload=result.structured_content, wire_response=result.structured_content)

    try:
        return asyncio.run(_call())
    except Exception as exc:
        # Byte-for-byte the in-process rule (``_run_mcp_client``): the JSON payload
        # inside the ``ToolError`` IS the envelope the buyer received, so it is
        # re-raised VERBATIM on a ``WireError``. No code -> class reconstruction —
        # that map is deleted from the harness — and a ``ToolError`` carrying no
        # envelope propagates untouched rather than being re-typed into one.
        envelope = _mcp_wire_envelope(exc)
        if envelope is not None:
            raise WireError(envelope) from exc
        raise


# -- E2E_A2A DELIVER: real JSON-RPC message/send over HTTP ------------------
#
# the wire-grading work. Same message shape ``_run_a2a_handler`` builds
# in-process — only how it reaches the server
# differs: a real ``POST /a2a`` JSON-RPC 2.0 request instead of a direct
# ``AdCPRequestHandler().on_message_send()`` call. The route is mounted at
# ``rpc_url="/a2a"`` by ``create_jsonrpc_routes`` (``src/app.py``), and the
# live server resolves the credential off the request headers the same way
# it does for REST. Push-notification injection
# (``on_message_send``, ``adcp_a2a_server.py``) is out of scope —
# see ``_wrap_a2a``'s docstring; this DELIVER function sends whatever
# ``_wrap_a2a`` produced unchanged, same limitation.


def _deliver_e2e_a2a(
    env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], credential: Any
) -> TransportResult:
    """Send the live A2A frame through the env's HTTP route and shared reader."""
    if not env.e2e_config:
        raise RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)")
    return env._run_a2a_over_http(address.name, **_with_credential(wrapped, credential))


DELIVER: dict[Transport, Callable[[BaseTestEnv, ToolAddress, Any, Any], Any]] = {
    Transport.MCP: _deliver_mcp,
    Transport.A2A: _deliver_a2a,
    Transport.REST: _deliver_rest,
    Transport.E2E_REST: _deliver_e2e_rest,
    Transport.E2E_MCP: _deliver_e2e_mcp,
    Transport.E2E_A2A: _deliver_e2e_a2a,
}


# -- UNWRAP: raw transport response -> normalized TransportResult -----------
#
# Success-path UNWRAP for MCP/A2A assumes DELIVER already raised on error
# (mirrors McpDispatcher/A2ADispatcher in tests/harness/dispatchers.py — the
# exception path is handled once, in AdCPTestClient.call, using the SAME
# module-level envelope-extraction helpers those dispatchers use, imported
# below rather than re-implemented). REST's UNWRAP inspects the response
# status itself (TestClient/httpx do not raise on 4xx/5xx), matching
# RestDispatcher.


def _unwrap_a2a(
    env: BaseTestEnv,
    delivered: TransportResult,
    transport: Transport,
    response_reader: Callable[[dict[str, Any]], Any],
) -> TransportResult:
    """Type one captured A2A result without changing its native evidence."""
    return parse_response_result(delivered, response_reader)


def _unwrap_tool_success(
    env: BaseTestEnv,
    delivered: DeliverResult,
    transport: Transport,
    response_reader: Callable[[dict[str, Any]], Any],
) -> TransportResult:
    """Parse the MCP structured body with the caller's response reader."""
    return parse_response_result(
        TransportResult(has_wire=True, envelope={"transport": transport.value}, wire_response=delivered.wire_response),
        response_reader,
    )


def _rest_transport_fault(envelope: dict[str, Any], raw_response: Any) -> TransportResult:
    """A >=400 REST response from which NO AdCP envelope could be recovered.

    One helper for the two ways that happens — a body that is not JSON at all,
    and a JSON body carrying no error code ``parse_rest_error_envelope`` can
    locate. Both mean the same thing on the wire: bytes came back, but the
    server produced no AdCP rejection, so there is nothing for
    ``wire_error_envelope`` to hold and ``derive_error_status`` classifies the
    result a transport fault rather than an ``adcp_error``.

    The error object carries INTERNAL_ERROR and nothing else: the HTTP status and
    body are on ``raw_response``, which the result already holds, and
    ``internal_detail`` takes only a caught exception (``AdCPSalesAgentError``'s
    class note), of which there is none here. It deliberately does NOT guess an
    AdCP class from the status: that map — 400 -> validation, 404 -> not found,
    and five more — is deleted from the harness, because a status is not a code
    and a guess is not evidence of what the buyer received (see
    ``BaseTestEnv.parse_rest_error_envelope``).
    """
    from src.core.exceptions import AdCPInternalError

    return TransportResult(
        # The HTTP response was received; its body just carries no AdCP
        # envelope. Bytes crossed the wire, so has_wire is True.
        has_wire=True,
        envelope={**envelope, "status": derive_error_status(None)},
        error=AdCPInternalError(),
        raw_response=raw_response,
    )


def unwrap_rest_response(
    env: BaseTestEnv,
    raw_response: Any,
    transport: Transport,
    parse_response: Callable[[dict[str, Any]], Any],
) -> TransportResult:
    """The one REST UNWRAP — ``RestDispatcher``, ``RestE2EDispatcher``
    (``tests/harness/dispatchers.py``) and the generic client's
    ``_unwrap_rest`` (below) all delegate here instead of each re-parsing the
    raw HTTP response (— three REST unwraps collapsed
    into one).

    *transport* supplies the envelope ``"transport"`` tag via
    ``transport.value`` — derived from the ``Transport`` enum, never a
    hardcoded string literal, so ``Transport.REST`` and ``Transport.E2E_REST``
    are tagged ``"rest"``/``"e2e_rest"`` respectively as read directly off the
    transport that produced the result, not duplicated per call site.

    *parse_response* controls how ``payload`` is derived from a **deep copy**
    of the parsed wire body — the #1417 pristine-wire rule: env parsers like
    ``_parse_update_rest_response`` mutate their input in place (e.g. popping
    ``"status"``), so handing them the SAME dict backing ``wire_response``
    would silently corrupt the stashed wire capture. Dispatchers pass
    ``env.parse_rest_response`` to get a typed Pydantic model; the
    transport-generic ``AdCPTestClient`` core (``_unwrap_rest`` below) passes
    the dispatch owner's selected model, as MCP/A2A UNWRAP do. Either way,
    ``payload`` and ``wire_response`` are built from separate dict objects —
    they never alias.
    """
    envelope: dict[str, Any] = {
        "transport": transport.value,
        "status_code": raw_response.status_code,
        "content_type": raw_response.headers.get("content-type", ""),
    }
    if raw_response.status_code >= 400:
        from tests.harness._base import WireError

        try:
            body = raw_response.json()
        except Exception:
            # Non-JSON error body (e.g. a bare 500 with an empty body) — no
            # structured envelope to expose, so error Then-steps see a typed
            # failure instead of a JSONDecodeError, matching the live-server
            # e2e_rest baseline (#1420).
            return _rest_transport_fault(envelope, raw_response)
        # THE one REST error-body reader, shared with BaseTestEnv.call_rest:
        # normalizes the REAL HTTP body into the two-layer envelope shape and
        # returns None when the body names no code. It reshapes; it never
        # rebuilds an exception class from a status.
        wire_error_envelope = env.parse_rest_error_envelope(raw_response.status_code, body)
        if wire_error_envelope is None:
            # A >=400 body that names no AdCP code is not a rejection the buyer
            # can act on — same verdict call_rest reaches, reported here as a
            # result rather than a raise.
            return _rest_transport_fault(envelope, raw_response)
        # REST's authentic evidence is its real HTTP body: a parseable AdCP
        # envelope is a structured rejection, anything else is a fault (C4).
        # ``WireError`` carries those bytes verbatim on ``.envelope`` and knows
        # nothing about our exception hierarchy — the assertion target stays the
        # envelope, not a harness-side re-typing of it.
        return TransportResult(
            # Structured >= 400 body — the real HTTP response was received.
            has_wire=True,
            error=WireError(wire_error_envelope),
            envelope={**envelope, "status": derive_error_status(wire_error_envelope)},
            raw_response=raw_response,
            wire_error_envelope=wire_error_envelope,
        )

    try:
        wire_response = raw_response.json()
        # #1417 pristine-wire deepcopy rule — parse_response may mutate its
        # input in place; hand it a COPY so wire_response keeps the untouched
        # wire body.
        payload = parse_response(copy.deepcopy(wire_response))
    except Exception as exc:
        # A success-status response whose body doesn't parse as JSON, or
        # whose parse_response rejects it — surface as an error result with
        # the envelope/raw_response still attached (mirrors the former
        # RestE2EDispatcher behavior) rather than propagating a raw
        # JSONDecodeError/ValidationError past the dispatch boundary.
        # Parse failure on an ALREADY-RECEIVED 2xx response: the wire happened,
        # only the harness-side parse of it did not.
        return TransportResult(has_wire=True, envelope=envelope, error=exc, raw_response=raw_response)
    # 2xx success — the real HTTP JSON body.
    return TransportResult(
        has_wire=True, payload=payload, envelope=envelope, raw_response=raw_response, wire_response=wire_response
    )


def _unwrap_rest(
    env: BaseTestEnv, raw: Any, transport: Transport, response_reader: Callable[[dict[str, Any]], Any]
) -> TransportResult:
    # The reader stays deepcopy-isolated from wire_response by unwrap_rest_response.
    return unwrap_rest_response(env, raw, transport, response_reader)


#: Facts the RESOLVER produces, which therefore have no spelling in a request payload.
#: Each maps to what a caller controls instead. A wire carries a credential; ``serve``
#: turns it into a ``ResolvedIdentity`` and reads the tenant and account off that, so
#: none of these is a field any DTO declares.
_RESOLVER_OWNED_PAYLOAD_KEYS = {
    "identity": "pass credential={...} to present headers, or credential={} to send none",
    "principal": "the resolver derives it from the credential; seed the row with PrincipalFactory",
    "tenant": "the resolver derives it from the credential or the hostname",
}


def _refuse_resolver_owned_payload_keys(payload: dict[str, Any]) -> None:
    """Refuse a payload key that names something the resolver owns.

    These reached production as UNDECLARED REQUEST FIELDS, where the accepted-shape
    strip refused them correctly -- but as ``INVALID_REQUEST`` with, for ``identity=``,
    ``pointer: /identity``. That reads as a spec violation by the seller when it is a
    harness misuse, and three tests in
    ``tests/integration/test_creative_formats_discovery.py`` were written against that
    reading. Refusing here makes the mistake impossible to express instead of
    diagnosable after the fact, which is the same answer ``AdapterCreateResult``'s
    ``extra="forbid"`` and ``PrincipalFactory.make_identity``'s unknown-keyword refusal
    already give for their own arguments.
    """
    for key, instead in _RESOLVER_OWNED_PAYLOAD_KEYS.items():
        if key in payload:
            raise TypeError(
                f"{key}= is not a request field: the resolver produces it inside serve(), so it has "
                f"no wire representation and no DTO declares it. Instead, {instead}."
            )


class DispatchOwner(Protocol):
    """The caller owns request addressing and response meaning on every transport."""

    @property
    def dispatch_env(self) -> BaseTestEnv: ...

    def address_tool(self, tool: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]: ...

    def response_parser(self, tool: str) -> Any | None: ...


def _dispatch_core(
    owner: DispatchOwner,
    transport: Transport,
    tool_name: str,
    payload: dict[str, Any],
    credential: Any = NO_IDENTITY_OVERRIDE,
) -> TransportResult:
    """Address one request, deliver it, and parse its successful body once.

    Environment calls own their local request routing and response model. A generic
    client owns the explicitly requested tool's pinned model, including None for
    tools without a single response model. Delivery primitives remain untyped.
    """
    from tests.harness.transport import MissingToolNameError

    _refuse_resolver_owned_payload_keys(payload)
    tool_name, payload = owner.address_tool(tool_name, dict(payload))
    if not tool_name:
        raise MissingToolNameError("Dispatch needs a tool name; declare A2A_SKILL/MCP_TOOL or pass tool_name=")
    payload = dict(payload)
    req = payload.pop("req", None)
    payload = flatten_payload(req, **payload)
    address = ADDRESS_TABLE.resolve(tool_name, transport)
    wrapped = WRAP[transport](address, payload)
    env = owner.dispatch_env
    try:
        raw = DELIVER[transport](env, address, wrapped, credential)
    except NotImplementedError:
        raise
    except Exception as exc:
        return UNWRAP_ERROR[transport](exc, transport)
    # Selection is lazy: a refusal never asks for a success parser, and a parser
    # error stays attached to the already captured response.
    return UNWRAP_SUCCESS[transport](
        env, raw, transport, lambda body: _response_reader(owner.response_parser(tool_name))(body)
    )


class AdCPTestClient:
    """One client, all transports, in-process and e2e.

    Constructed per-env — it needs the env's credential + factory-
    bound session + e2e_config, exactly what ``BaseTestEnv`` already carries.
    The address map it consults (``tests.harness.address_table.ADDRESS_TABLE``)
    IS a process-wide, lazily-built singleton (cheap: no I/O, just reads three
    live registration objects) — but auth/session/e2e state stays per-scenario
    on ``env``, so a fresh ``AdCPTestClient(env)`` per scenario is correct and
    matches every existing env's per-scenario construction.
    """

    def __init__(self, env: BaseTestEnv) -> None:
        self._env = env

    @property
    def dispatch_env(self) -> BaseTestEnv:
        return self._env

    def address_tool(self, tool: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return tool, payload

    def response_parser(self, tool: str) -> Any | None:
        return spec_response_model(tool)

    def call(
        self,
        tool: str,
        payload: dict[str, Any],
        transport: Transport,
        *,
        credential: Any = NO_IDENTITY_OVERRIDE,
    ) -> TransportResult:
        """Dispatch *tool* through *transport* — see ``_dispatch_core`` above
        for the full ADDRESS/WRAP/DELIVER/UNWRAP contract and the
        ``TransportResult.payload`` typed-payload caveat."""
        return _dispatch_core(self, transport, tool, payload, credential)


def unwrap_mcp_error(exc: Exception, transport: Transport = Transport.MCP) -> TransportResult:
    """THE MCP error-path unwrap — one definition, both dispatch paths.

    ``AdCPTestClient.call`` (via ``UNWRAP_ERROR`` below) and
    ``tests/harness/dispatchers.py``'s ``McpDispatcher`` both delegate here.
    They used to hold byte-equivalent copies of this body, which is exactly how
    the derived status came to exist on one path and not the other: C4 wired
    ``derive_error_status`` into the dispatcher copy, while the client copy —
    the one every ``dispatch_via_client`` storyboard scenario actually takes —
    kept returning ``envelope={}``, so ``then_response_not_500_or_non_adcp_shape``
    read ``status=None`` and passed trivially on mcp and a2a. One definition
    removes the class of defect, not just this instance (CLAUDE.md DRY
    invariant).

    *transport* supplies the envelope ``"transport"`` tag via
    ``transport.value``, the same rule ``_unwrap_tool_success`` and
    ``unwrap_rest_response`` follow, so an E2E dispatch is never mislabeled
    in-process.
    """
    from tests.harness._base import WireError

    # _run_mcp_client already raises WireError carrying the ToolError's envelope
    # verbatim, which _wire_envelope_from_exception reads back off ``.envelope``;
    # the raw-ToolError branch covers the rare case where a raw ToolError reached
    # here untouched (an env that dispatched through the production
    # RegistryTool.run boundary). It is re-raised as the same WireError rather
    # than reconstructed into a production error class: the code -> class map is
    # deleted, and result.error then resolves to the real wire code instead of
    # "AdCPToolError" without any harness-side re-typing.
    raw_tool_error_envelope = _envelope_from_mcp_error(exc)
    wire = raw_tool_error_envelope or _wire_envelope_from_exception(exc)
    error = WireError(raw_tool_error_envelope) if raw_tool_error_envelope is not None else exc
    return TransportResult(
        # This is the catch-all branch of an MCP dispatch: it wraps env.call_mcp
        # whole, so it can fire before any bytes moved and cannot tell which.
        # It declares False and still hands back the REAL envelope it recovered
        # from the ToolError above — see TransportResult.has_wire's SCOPE note.
        has_wire=False,
        error=error,
        # Derived per-transport status: MCP's authentic evidence is
        # whether a structured AdCP envelope was recoverable from the ToolError,
        # rather than a fault that produced no envelope at all.
        envelope={"transport": transport.value, "status": derive_error_status(wire)},
        wire_error_envelope=wire,
        # NO synthesized envelope. MCP HAS a wire, so a rebuilt copy here is
        # either redundant or — when the capture above came back None — a mask
        # that would let error_envelope() hand a downstream test a value
        # regenerated from the very exception it caught. Only ImplDispatcher,
        # which has no wire by definition, may populate that field; pinned by
        # tests/unit/test_harness_mcp_never_synthesizes.py.
    )


def unwrap_rest_error(exc: Exception, transport: Transport = Transport.REST) -> TransportResult:
    """THE REST DELIVER-exception unwrap — one definition, both dispatch paths.

    Genuine exceptions only (e.g. ``get_rest_client`` failing before an
    HTTP call is even made) — ordinary 4xx/5xx responses do not raise and are
    handled by ``unwrap_rest_response`` instead, which derives the status from
    the real HTTP body. An exception here means no HTTP response body existed at
    all, so the derived status is a fault by construction: ``derive_error_status``
    is called with ``None`` explicitly rather than the value being left absent,
    because an ABSENT status is what let the storyboard Then pass on nothing.
    """
    return TransportResult(
        # An exception here means no HTTP response body existed at all, so no
        # bytes ever crossed the wire.
        has_wire=False,
        error=exc,
        envelope={"transport": transport.value, "status": derive_error_status(None)},
    )


UNWRAP_SUCCESS: dict[
    Transport, Callable[[BaseTestEnv, Any, Transport, Callable[[dict[str, Any]], Any]], TransportResult]
] = {
    Transport.A2A: _unwrap_a2a,
    Transport.E2E_A2A: _unwrap_a2a,
    Transport.MCP: _unwrap_tool_success,
    Transport.E2E_MCP: _unwrap_tool_success,
    Transport.REST: _unwrap_rest,
    Transport.E2E_REST: _unwrap_rest,
}

UNWRAP_ERROR: dict[Transport, Callable[[Exception, Transport], TransportResult]] = {
    Transport.MCP: unwrap_mcp_error,
    Transport.E2E_MCP: unwrap_mcp_error,
    Transport.A2A: unwrap_delivery_error,
    Transport.E2E_A2A: unwrap_delivery_error,
    Transport.REST: unwrap_rest_error,
    Transport.E2E_REST: unwrap_rest_error,
}

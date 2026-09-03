"""Transport-generic AdCP test client — one ``call()``, all transports.

One ``call()`` reaches every transport, so a scenario is written once and
graded on all of them; the per-transport shaping lives behind this seam rather
than in each env. ``AdCPTestClient.call(tool, payload,
transport)`` replaces the per-tool ``call_a2a``/``call_mcp``/
``build_rest_body``/``parse_rest_response`` quartet that today is
hand-written on every one of the 33 ``tests/harness/*.py`` env classes
(``MediaBuyDualEnv`` is the worst offender).

ADDRESS (tool -> address) is fully derived — see ``tests/harness/
address_table.py``. WRAP (payload -> transport envelope) and UNWRAP
(transport envelope -> normalized ``TransportResult``) are the only
per-transport code, and are transport-**family** functions: the same
function object serves both an in-process transport and its E2E sibling
for the reason given below, because the wire format is identical either way — only
DELIVER (how bytes reach the server) differs.

DELIVER reuses the SAME env primitives ``_run_mcp_client`` /
``_run_a2a_handler`` / ``_prepare_rest_request`` that the per-env dispatch
methods already call — this is deliberate: those
methods own the real auth-chain / factory-commit / FastMCP-middleware
plumbing, and duplicating that here would violate this project's DRY
invariant for no benefit. ``client.py`` only adds the tool-name-generic
glue around them; passing ``response_cls=dict`` gets a plain dict back
from ``_run_mcp_client``/``_run_a2a_handler`` — UNWRAP (not DELIVER) then
parses that dict into ``tool_name``'s pinned SDK response model via
``spec_response_model``, so ``call()`` still does not
need a ``response_cls`` parameter — see the "typed payload" docstring note
on ``TransportResult.payload`` below for the no-pinned-model case.

All three E2E transports are now implemented — ``_deliver_e2e_rest``,
``_deliver_e2e_mcp`` and ``_deliver_e2e_a2a`` below, each real HTTP through
nginx to the live Docker stack. ``RestE2EDispatcher`` and
``A2AE2EDispatcher`` (``tests/harness/dispatchers.py``) delegate to the
matching DELIVER function instead of duplicating it, so there is one
implementation per transport, not two. Auth-header construction is shared
across all three via ``e2e_identity_headers`` below (this project's DRY
invariant, CLAUDE.md) — WRAP/UNWRAP were already written per transport
*family*, so each of these follow-ups only needed to add a
DELIVER function; ADDRESS and WRAP needed no changes.

Usage::

    from tests.harness.client import AdCPTestClient
    from tests.harness.transport import Transport

    client = AdCPTestClient(env)
    result = client.call("get_products", {"brief": "video ads"}, Transport.MCP)
    assert result.is_success
    result.assert_wire_error(...)  # on the error path — unchanged from env.call_via
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
    strip_a2a_protocol_fields,
)

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv

# NoAddressForTransport re-exported here for callers that only import
# tests.harness.client (both this module and
# address_table.py as "new files, the transport-generic client builds it" — callers should not
# need to know the map lives in a separate module).
from tests.harness.address_table import NoAddressForTransport  # noqa: F401  (re-export)


def _with_identity(payload: dict[str, Any], identity: Any) -> dict[str, Any]:
    """Copy *payload* and, unless *identity* is the no-override sentinel, add it.

    Shared by all three WRAP-family functions below — the identity-forwarding
    rule is identical regardless of transport (MCP/A2A/REST-family), only the
    DELIVER function that consumes the resulting kwargs differs.
    """
    kwargs = dict(payload)
    if identity is not NO_IDENTITY_OVERRIDE:
        kwargs["identity"] = identity
    return kwargs


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
    ``_handle_explicit_skill`` (``src/a2a_server/adcp_a2a_server.py:1491``)
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
# ``_prepare_rest_request``) — these already own auth-chain / factory-commit
# / middleware plumbing; DELIVER only adds the tool-name-generic call shape.


def _deliver_mcp(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> DeliverResult:
    kwargs = _with_identity(wrapped, identity)
    # response_cls=dict: _run_mcp_client ends with `response_cls(**structured_content)`;
    # `dict(**d)` is `d`, so this yields the raw structured_content dict instead of a
    # per-tool Pydantic model the client has no way to know generically.
    return env._run_mcp_client(address.name, dict, **kwargs)


def _deliver_a2a(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> DeliverResult:
    kwargs = _with_identity(wrapped, identity)
    return env._run_a2a_handler(address.name, dict, **kwargs)


# HTTP verbs with no request body — a JSON body kwarg is either rejected by
# the client (starlette TestClient.get/httpx.Client.get do not accept `json=`
# at all) or simply wrong to send. address_table.py's REST_TOOL_ALIASES made
# get_adcp_capabilities (GET /api/v1/capabilities) genuinely REST-resolvable
# , which surfaced this: every verb used to get `json=`
# unconditionally, so a GET dispatch raised TypeError before any HTTP call.
_BODILESS_REST_VERBS = frozenset({"get", "delete"})


def _rest_request_kwargs(method: str, body: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Build the kwargs for ``getattr(client, method)(url, **kwargs)``.

    Omits ``json=`` for bodiless verbs (get/delete) — see
    ``_BODILESS_REST_VERBS``. Shared by in-process and e2e REST DELIVER so the
    rule is defined once, not per call site.
    """
    kwargs: dict[str, Any] = dict(extra)
    if method not in _BODILESS_REST_VERBS:
        kwargs["json"] = body
    return kwargs


def _deliver_rest(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> Any:
    kwargs = _with_identity({}, identity)
    client, _resolved_identity = env._prepare_rest_request(kwargs)
    method = address.method or "post"
    return getattr(client, method)(wrapped["url"], **_rest_request_kwargs(method, wrapped["body"]))


def e2e_identity_headers(identity: Any) -> dict[str, str]:
    """Auth/tenant/dry-run HTTP headers for e2e dispatch, derived from a
    resolved identity.

    Shared by e2e REST, e2e MCP, and e2e A2A DELIVER (below) — production's
    identity resolution (``resolve_identity()``,
    ``src/core/resolved_identity.py``) reads the same
    ``x-adcp-auth``/``x-adcp-tenant``/``x-dry-run`` headers regardless of
    transport protocol (MCP's ``mcp_auth_middleware`` and A2A's
    ``UnifiedAuthMiddleware`` resolve through the identical
    ``resolve_identity_from_context`` -> header extraction chain REST does),
    so this is one function, not an independently reinvented convention per
    transport (this project's DRY invariant, CLAUDE.md).

    ``identity=None`` means "dispatch without auth headers" (explicit
    unauthenticated) — the live server's own auth middleware then returns the
    real 401/``AUTH_REQUIRED`` rejection. When identity carries no
    ``auth_token`` (e.g. ``principal_id=None`` boundary tests), the header is
    simply omitted rather than sent empty.
    """
    headers: dict[str, str] = {}
    if identity is None:
        return headers
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
    return headers


def _deliver_e2e_rest(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> Any:
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

    resolved_identity = env.identity_for(Transport.E2E_REST) if identity is NO_IDENTITY_OVERRIDE else identity
    headers = {"Content-Type": "application/json", **e2e_identity_headers(resolved_identity)}
    from tests.harness.dispatchers import apply_testing_hook_headers

    apply_testing_hook_headers(
        headers,
        resolved_identity,
        fallback_mock_time=getattr(env, "mock_time", None),
    )
    method = address.method or "post"

    with httpx.Client(base_url=env.e2e_config.base_url, timeout=30) as client:
        return getattr(client, method)(wrapped["url"], **_rest_request_kwargs(method, wrapped["body"], headers=headers))


def _deliver_e2e_mcp(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> dict[str, Any]:
    """E2E MCP DELIVER: real HTTP via ``fastmcp.Client`` against the live Docker
    stack — the transport ``runStoryboard`` (the real AdCP conformance runner)
    actually speaks (``request_signing.transport = 'mcp'``, agent URLs ending
    ``/mcp``), see the task (the wire-grading work).

    Same call shape as ``_run_mcp_client`` (``tests/harness/_base.py:754``) —
    ``call_tool`` -> ``structured_content`` -> returned on the ``DeliverResult``
    -> unwrap ``ToolError`` via the SAME
    ``_unwrap_mcp_tool_error`` helper ``_run_mcp_client`` and
    ``McpDispatcher.dispatch`` use — only the transport under the FastMCP
    ``Client`` changes: a real ``StreamableHttpTransport`` against
    ``env.e2e_config.base_url`` instead of the in-memory ``mcp`` app object.
    Auth flows as real HTTP headers (``e2e_identity_headers``) instead of the
    ``get_http_headers``/``resolve_identity_from_context`` patches
    ``_run_mcp_client`` installs for in-process dispatch — there is a real
    nginx -> ``UnifiedAuthMiddleware`` -> ``resolve_identity()`` chain running
    on the live server, so nothing needs mocking here.
    """
    import asyncio

    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from tests.harness._base import _unwrap_mcp_tool_error

    if not env.e2e_config:
        raise RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)")

    # Mirrors _run_mcp_client's unconditional commit (_base.py:788) — the live
    # server hits its own Postgres via env.e2e_config.postgres_url, so
    # uncommitted factory rows in this test session would be invisible to it.
    env._commit_factory_data()

    resolved_identity = env.identity_for(Transport.E2E_MCP) if identity is NO_IDENTITY_OVERRIDE else identity
    headers = e2e_identity_headers(resolved_identity)
    url = f"{env.e2e_config.base_url}/mcp/"

    async def _call() -> DeliverResult:
        mcp_transport = StreamableHttpTransport(url=url, headers=headers)
        async with Client(transport=mcp_transport) as mcp_client:
            result = await mcp_client.call_tool(address.name, wrapped)
            return DeliverResult(payload=result.structured_content, wire_response=result.structured_content)

    try:
        return asyncio.run(_call())
    except Exception as exc:
        raise _unwrap_mcp_tool_error(exc) from exc


# -- E2E_A2A DELIVER: real JSON-RPC message/send over HTTP ------------------
#
# the wire-grading work. Same message shape ``_run_a2a_handler`` builds
# in-process — only how it reaches the server
# differs: a real ``POST /a2a`` JSON-RPC 2.0 request instead of a direct
# ``AdCPRequestHandler().on_message_send()`` call. The route is mounted at
# ``rpc_url="/a2a"`` by ``create_jsonrpc_routes`` (``src/app.py``), and
# resolves identity through the SAME ``UnifiedAuthMiddleware`` REST uses
# (``src/core/auth_middleware.py`` — x-adcp-auth / x-adcp-tenant / x-dry-run
# headers), via ``AdCPCallContextBuilder`` (``src/a2a_server/
# context_builder.py``). Push-notification injection
# (``_handle_explicit_skill``, ``adcp_a2a_server.py:1491``) is out of scope —
# see ``_wrap_a2a``'s docstring; this DELIVER function sends whatever
# ``_wrap_a2a`` produced unchanged, same limitation.


def _build_a2a_jsonrpc_body(skill_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """JSON-RPC 2.0 envelope for a ``message/send`` (``SendMessage``) call.

    Builds the SAME protobuf ``Message`` in-process dispatch uses
    (``create_a2a_message_with_skill``, ``tests/utils/a2a_helpers.py:67`` —
    the exact helper ``_run_a2a_handler`` calls, ``tests/harness/_base.py:692``)
    and serializes it through the real proto JSON mapping
    (``google.protobuf.json_format``) so the wire body is byte-for-byte what
    a real A2A client would send — not a hand-rolled approximation. Method
    name ``"SendMessage"`` and the ``params.message`` shape are dictated by
    ``a2a.server.routes.jsonrpc_dispatcher.JsonRpcDispatcher.METHOD_TO_MODEL``
    and ``SendMessageRequest``'s proto fields, confirmed by direct
    inspection, not assumed.
    """
    import uuid

    from a2a.types.a2a_pb2 import SendMessageRequest
    from google.protobuf import json_format

    from tests.utils.a2a_helpers import create_a2a_message_with_skill

    message = create_a2a_message_with_skill(skill_name=skill_name, parameters=parameters)
    params = json_format.MessageToDict(SendMessageRequest(message=message))
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage", "params": params}


def _artifact_data_from_json(artifact: dict[str, Any]) -> dict[str, Any]:
    """First ``data`` Part's payload from a JSON-decoded A2A artifact dict.

    Mirrors ``extract_data_from_artifact`` (``tests/utils/a2a_helpers.py:39``)
    for the case where the artifact already went through
    ``response.json()`` (real HTTP) instead of being read off a live
    protobuf ``Artifact`` object (in-process) — same field, same shape,
    only the decoding step differs.
    """
    for part in artifact.get("parts", []):
        if "data" in part:
            data = part["data"]
            return data if isinstance(data, dict) else {}
    return {}


def _deliver_e2e_a2a(env: BaseTestEnv, address: ToolAddress, wrapped: dict[str, Any], identity: Any) -> dict[str, Any]:
    """Real HTTP delivery: POST a JSON-RPC ``message/send`` request to the live
    A2A endpoint, then walk the same Task-state branches ``_run_a2a_handler``
    walks in-process (``tests/harness/_base.py:705-752``) — FAILED raises the
    reconstructed ``AdCPError`` (real wire envelope stashed via
    ``_envelope_to_adcp_error``, same helper the in-process path uses),
    SUBMITTED synthesizes the manual-approval wire, otherwise the first
    artifact's ``data`` Part is the success payload.

    Sends the ``A2A-Version`` header the real JSON-RPC route requires
    (``a2a.server.routes.jsonrpc_dispatcher``'s ``@validate_version(PROTOCOL_VERSION_1_0)``
    decorator on ``on_message_send`` / ``on_message_send_stream``) — omitting it
    makes the SDK's own ``validate_version`` default to the legacy '0.3' and
    reject the request with a ``VersionNotSupportedError`` before it ever
    reaches ``AdCPRequestHandler``. The in-process ``_run_a2a_handler`` path
    (``tests/harness/_base.py``) never needed this: it calls
    ``AdCPRequestHandler().on_message_send()`` directly, bypassing the
    route-level decorator entirely — a divergence invisible until this
    function got its first live caller.
    """
    import httpx
    from a2a.utils import constants as a2a_constants

    from tests.harness._base import _envelope_to_adcp_error

    if not env.e2e_config:
        raise RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)")

    resolved_identity = env.identity_for(Transport.E2E_A2A) if identity is NO_IDENTITY_OVERRIDE else identity

    headers = {
        "Content-Type": "application/json",
        a2a_constants.VERSION_HEADER: a2a_constants.PROTOCOL_VERSION_CURRENT,
        **e2e_identity_headers(resolved_identity),
    }
    rpc_body = _build_a2a_jsonrpc_body(address.name, wrapped)

    with httpx.Client(base_url=env.e2e_config.base_url, timeout=30) as http_client:
        response = http_client.post("/a2a", json=rpc_body, headers=headers)

    # PARSE BEFORE raise_for_status, matching the REST
    # sibling's >=400 handling. raise_for_status() first threw away the response
    # BODY on any 4xx/5xx — and that body is where the AdCP two-layer error
    # envelope lives, so every error-path Then that asserts on
    # wire_error_envelope saw None and could only fall back to the lossy
    # reconstructed exception. A transport-level failure with no JSON body still
    # raises, but only after the body has had its chance to speak.
    try:
        body = response.json()
    except ValueError:
        response.raise_for_status()
        raise

    if "error" in body:
        rpc_error = body["error"]
        error_data = rpc_error.get("data")
        fallback_message = rpc_error.get("message") or "A2A JSON-RPC request failed"
        reconstructed = _envelope_to_adcp_error(error_data, fallback_message=fallback_message)
        if reconstructed is not None:
            raise reconstructed
        raise RuntimeError(f"A2A JSON-RPC error {rpc_error.get('code')}: {fallback_message}")

    result = body.get("result") or {}
    task = result.get("task")
    if task is None:
        raise TypeError(f"Expected a Task in the A2A JSON-RPC result, got: {result!r}")

    state = task.get("status", {}).get("state")
    if state == "TASK_STATE_FAILED":
        artifacts = task.get("artifacts") or []
        if artifacts:
            envelope = _artifact_data_from_json(artifacts[0])
            reconstructed = _envelope_to_adcp_error(envelope, fallback_message="A2A skill failed")
            if reconstructed is not None:
                raise reconstructed
        raise RuntimeError(f"A2A task failed: {task.get('status')}")

    if state == "TASK_STATE_SUBMITTED":
        submitted_wire = {"status": "submitted", "task_id": task.get("id")}
        return DeliverResult(payload=submitted_wire, wire_response=dict(submitted_wire))

    artifacts = task.get("artifacts") or []
    if not artifacts:
        raise ValueError(f"Task has no artifacts. Status: {task.get('status')}")
    artifact_data = _artifact_data_from_json(artifacts[0])
    # Real A2A wire, unstripped — captured BEFORE stripping (mirrors
    # _run_a2a_handler's own capture order).
    wire_response = dict(artifact_data)
    return DeliverResult(payload=strip_a2a_protocol_fields(artifact_data), wire_response=wire_response)


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


def _parse_pinned_response(tool_name: str, raw: dict[str, Any]) -> Any | None:
    """Parse *raw* wire JSON back into ``tool_name``'s pinned SDK response model.

    Resolves the one pinned response class for tools that have one, via
    ``spec_response_model(tool_name)``. This is harness-side only: the sweep
    changes no production code, so nothing here mirrors a production seam. ``None`` is the explicit named case for
    tools that don't — either genuinely no pinned schema, or (e.g.
    ``create_media_buy``) a ``Union`` of outcome variants with no single class
    to parse into (see ``spec_response_model``'s docstring) — callers keep
    ``wire_response`` for those; there is nothing to hand-maintain per tool
    here, so a spec bump that adds/renames a response model widens this
    automatically.
    """
    model = spec_response_model(tool_name)
    if model is None:
        return None
    return model(**raw)


def _unwrap_tool_success(
    env: BaseTestEnv, delivered: DeliverResult, transport: Transport, tool_name: str
) -> TransportResult:
    """Success-path unwrap for tool-style transports, returning PINNED types.

    One function only: the MCP and A2A versions were byte-identical.

    Not the sole unwrap. The in-process dispatchers re-parse with the env's own
    ``response_parser`` on purpose, so they return the env-LOCAL response type
    that ~34 call sites outside tests/harness depend on — see
    ``_base.py:656-660``. Do not collapse them into this.

    The wire comes off the DELIVERED VALUE, not ``env._last_wire_response``:
    one delivery, one channel. ``tag`` is ``transport.value``, never a literal,
    so an E2E dispatch is never mislabeled in-process.
    """
    return TransportResult(
        # Downstream of DELIVER: the MCP structured_content / A2A artifact
        # DataPart already came back, which is the same declaration the
        # in-process McpDispatcher/A2ADispatcher success sites make.
        has_wire=True,
        payload=_parse_pinned_response(tool_name, delivered.payload),
        envelope={"transport": transport.value},
        wire_response=delivered.wire_response,
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
    ``_parse_pinned_response`` bound to the dispatched tool, same
    ``spec_response_model(tool)`` parse-back MCP/A2A UNWRAP use. Either way,
    ``payload`` and ``wire_response`` are built from separate dict objects —
    they never alias.
    """
    envelope: dict[str, Any] = {
        "transport": transport.value,
        "status_code": raw_response.status_code,
        "content_type": raw_response.headers.get("content-type", ""),
    }
    if raw_response.status_code >= 400:
        try:
            body = raw_response.json()
        except Exception:
            # Non-JSON error body (e.g. a bare 500 with an empty body) — no
            # structured envelope to expose; wrap as AdCPError so error
            # Then-steps see a typed failure instead of a JSONDecodeError,
            # matching the live-server e2e_rest baseline (#1420).
            from src.core.exceptions import AdCPError

            body_text = raw_response.text or "(empty body)"
            non_json_error = AdCPError(
                f"HTTP {raw_response.status_code}: {body_text}",
                details={"status_code": raw_response.status_code, "raw_body": body_text},
            )
            non_json_error.status_code = raw_response.status_code
            # No JSON body means no AdCP envelope was produced at all — the
            # request died as a transport fault.
            return TransportResult(
                # The HTTP response was received (status >= 400); its body just
                # isn't JSON. Bytes crossed the wire, so has_wire is True even
                # though no AdCP envelope could be recovered from them.
                has_wire=True,
                envelope={**envelope, "status": derive_error_status(None)},
                error=non_json_error,
                raw_response=raw_response,
            )
        parsed_error = env.parse_rest_error(raw_response.status_code, body)
        # REST's authentic evidence is its real HTTP body: a parseable AdCP
        # envelope is a structured rejection, anything else is a fault (C4).
        return TransportResult(
            # Structured >= 400 body — the real HTTP response was received.
            has_wire=True,
            error=parsed_error,
            envelope={**envelope, "status": derive_error_status(body)},
            raw_response=raw_response,
            wire_error_envelope=body,
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


def _unwrap_rest(env: BaseTestEnv, raw: Any, transport: Transport, tool_name: str) -> TransportResult:
    # spec_response_model(tool_name) parse-back, same as MCP/A2A UNWRAP —
    # still deepcopy-isolated from wire_response by unwrap_rest_response above.
    return unwrap_rest_response(env, raw, transport, lambda body: _parse_pinned_response(tool_name, body))


def _dispatch_core(
    env: BaseTestEnv,
    transport: Transport,
    tool_name: str,
    payload: dict[str, Any],
    identity: Any = NO_IDENTITY_OVERRIDE,
) -> TransportResult:
    """Address -> wrap -> deliver -> unwrap -> ``TransportResult``.

    The one dispatch core — ``AdCPTestClient.call``
    below and every E2E dispatcher (``tests/harness/dispatchers.py``:
    ``McpE2EDispatcher``, ``A2AE2EDispatcher``) delegate here instead of each
    re-implementing ADDRESS/WRAP/DELIVER/UNWRAP or hand-rolling their own
    identity/exception handling.

    *payload* is always the flat AdCP request payload as a dict (the same
    shape ``req.model_dump(mode="json", exclude_none=True)`` already produces
    across every env's ``build_rest_body``/``_flatten_request`` — see
    ``flatten_payload`` above for callers that still carry a ``req`` object).

    Raises :class:`~tests.harness.address_table.NoAddressForTransport` when
    *tool_name* has no registered address on *transport* — expected for tools
    that are not exposed on every transport (e.g. A2A-only skills).

    Note on ``TransportResult.payload``: UNWRAP resolves ``tool_name``'s
    pinned SDK response model via ``spec_response_model`` (mirroring the
    production request seam, ``src/core/version_compat.py``) and parses the
    wire dict back into it — ``result.payload.<field>`` attribute access,
    never ``result.payload["<field>"]`` subscripting.
    Tools with no single pinned response class (no schema, or a ``Union`` of
    outcome variants — see ``spec_response_model``'s docstring) get the
    explicit named case instead: ``payload`` is ``None`` and
    ``wire_response`` still carries the raw dict, so every existing
    Then-step helper that only checks
    ``is_success``/``is_error``/``wire_response``/``wire_error_envelope``
    (i.e. ``assert_wire_error``) is unaffected — but ``is_success`` (which
    requires ``payload is not None``) is FALSE for those tools' successful
    dispatches; a caller that needs the flat wire dict for one of them reads
    ``result.wire_response`` directly instead of relying on ``is_success``.
    """
    address = ADDRESS_TABLE.resolve(tool_name, transport)
    wrapped = WRAP[transport](address, payload)
    try:
        raw = DELIVER[transport](env, address, wrapped, identity)
    except NotImplementedError:
        # Missing delivery support — an E2E delivery gap (§7), an env that
        # doesn't implement REST (get_rest_client), or a MissingToolNameError
        # raised by a dispatcher before DELIVER even runs — must surface as a
        # hard failure, not get silently downgraded into a TransportResult
        # error a test could mistake for a real AdCP rejection.
        raise
    except Exception as exc:
        # *transport* is forwarded so the error envelope carries the same
        # transport.value tag and derived status the dispatcher path produces —
        # one error-unwrap implementation per transport family, not two.
        return UNWRAP_ERROR[transport](exc, transport)
    return UNWRAP_SUCCESS[transport](env, raw, transport, tool_name)


class AdCPTestClient:
    """One client, all transports, in-process and e2e.

    Constructed per-env — it needs the env's identity resolution + factory-
    bound session + e2e_config, exactly what ``BaseTestEnv`` already carries.
    The address map it consults (``tests.harness.address_table.ADDRESS_TABLE``)
    IS a process-wide, lazily-built singleton (cheap: no I/O, just reads three
    live registration objects) — but auth/session/e2e state stays per-scenario
    on ``env``, so a fresh ``AdCPTestClient(env)`` per scenario is correct and
    matches every existing env's per-scenario construction.
    """

    def __init__(self, env: BaseTestEnv) -> None:
        self._env = env

    def call(
        self,
        tool: str,
        payload: dict[str, Any],
        transport: Transport,
        *,
        identity: Any = NO_IDENTITY_OVERRIDE,
    ) -> TransportResult:
        """Dispatch *tool* through *transport* — see ``_dispatch_core`` above
        for the full ADDRESS/WRAP/DELIVER/UNWRAP contract and the
        ``TransportResult.payload`` typed-payload caveat."""
        return _dispatch_core(self._env, transport, tool, payload, identity)


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
    from tests.harness._base import _unwrap_mcp_tool_error

    # _run_mcp_client already unwraps ToolError -> AdCPError internally
    # (stashing _wire_error_envelope when reconstruction succeeds); the
    # raw-ToolError branch covers the rare case where that internal unwrap left
    # a raw ToolError untouched (an env that dispatched through the production
    # with_error_logging boundary). Unwrapping it means result.error is the
    # typed AdCPError, so error-code assertions resolve to the real wire code
    # rather than "AdCPToolError".
    raw_tool_error_envelope = _envelope_from_mcp_error(exc)
    wire = raw_tool_error_envelope or _wire_envelope_from_exception(exc)
    error = _unwrap_mcp_tool_error(exc) if raw_tool_error_envelope is not None else exc
    return TransportResult(
        # This is the catch-all arm of an MCP dispatch: it wraps env.call_mcp
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


def unwrap_a2a_error(exc: Exception, transport: Transport = Transport.A2A) -> TransportResult:
    """THE A2A error-path unwrap — one definition, both dispatch paths.

    ``_run_a2a_handler`` already reconstructs ``AdCPError`` with
    ``_wire_error_envelope`` stashed (via ``_envelope_to_adcp_error``) before
    raising, so the ``_wire_error_envelope`` getattr below covers it.
    See :func:`unwrap_mcp_error` for why this is one function rather than a copy
    per dispatch path.

    REAL STASH ONLY — never ``_envelope_from_adcp_error``. That rule has ONE
    owner, ``transport._wire_envelope_from_exception``, called below rather than
    re-implemented here: an inlined second copy is how the fallback survived a
    merge once already (it was restored in ``transport.py`` while this call site
    kept its own read). Handing back an envelope the harness rebuilt from the
    exception it just caught, under the field named for what actually crossed
    the wire, is the laundered-copy channel this branch closed — a
    scenario asserting on ``wire_error_envelope`` would then grade the rebuild,
    and pass whether or not production emitted anything at all. ``None`` is the
    honest answer when nothing was captured; pinned by
    ``tests/unit/test_harness_mcp_never_synthesizes.py``.
    """
    wire = _wire_envelope_from_exception(exc)
    return TransportResult(
        # Catch-all arm wrapping the whole A2A delivery — it may fire before
        # anything was sent, so it declares False while still exposing the real
        # envelope stashed on the reconstructed AdCPError. This is the exact
        # case TransportResult.has_wire's SCOPE note names.
        has_wire=False,
        error=exc,
        # Derived per-transport status: the A2A evidence is whether
        # a failed Task carried an AdCP envelope in its artifact DataPart.
        envelope={"transport": transport.value, "status": derive_error_status(wire)},
        wire_error_envelope=wire,
    )


def unwrap_rest_error(exc: Exception, transport: Transport = Transport.REST) -> TransportResult:
    """THE REST DELIVER-exception unwrap — one definition, both dispatch paths.

    Genuine exceptions only (e.g. ``_prepare_rest_request`` failing before an
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


UNWRAP_SUCCESS: dict[Transport, Callable[[BaseTestEnv, Any, Transport, str], TransportResult]] = {
    Transport.MCP: _unwrap_tool_success,
    Transport.E2E_MCP: _unwrap_tool_success,
    Transport.A2A: _unwrap_tool_success,
    Transport.E2E_A2A: _unwrap_tool_success,
    Transport.REST: _unwrap_rest,
    Transport.E2E_REST: _unwrap_rest,
}

UNWRAP_ERROR: dict[Transport, Callable[[Exception, Transport], TransportResult]] = {
    Transport.MCP: unwrap_mcp_error,
    Transport.E2E_MCP: unwrap_mcp_error,
    Transport.A2A: unwrap_a2a_error,
    Transport.E2E_A2A: unwrap_a2a_error,
    Transport.REST: unwrap_rest_error,
    Transport.E2E_REST: unwrap_rest_error,
}

"""Central FastAPI application.

Mounts all sub-applications (MCP, A2A, Admin) into a single process.
Replaces the previous multi-process architecture where MCP, A2A, and Admin
ran as separate processes behind nginx.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types import AgentCard as A2AAgentCard
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.responses import Response
from starlette.routing import Route

from src.a2a_server.adcp_a2a_server import (
    AdCPRequestHandler,
    create_agent_card,
    restore_a2a_integer_types,
)
from src.a2a_server.context_builder import AdCPCallContextBuilder
from src.admin.app import create_app
from src.core.agent_identity import agent_identity_for_tenant_id
from src.core.auth_middleware import AuthChallengeResponder
from src.core.config import load_settings
from src.core.domain_config import get_a2a_server_url, get_sales_agent_domain
from src.core.domain_routing import route_landing_page
from src.core.errors.issues import issues_from_validation_error
from src.core.exceptions import AdCPInvalidRequestError, AdCPSalesAgentError
from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive
from src.core.http_utils import path_from_asgi_scope
from src.core.lifecycle import run_all_shutdown_callbacks
from src.core.main import mcp
from src.core.resolved_identity import TransportProtocol
from src.core.signing.capture import SignedExchangeCapture
from src.core.tools._boundary import failure_response
from src.core.tools._wire import to_wire
from src.landing import generate_tenant_landing_page
from src.landing.landing_page import generate_fallback_landing_page
from src.routes.api_v1 import router as api_v1_router
from src.routes.health import debug_router as health_debug_router
from src.routes.health import router as health_router
from src.routes.well_known import router as well_known_router

logger = logging.getLogger(__name__)

# The composition root: the environment is read here, once, and every component this
# module composes is selected from the result. The embedded Flask admin app receives the
# same object rather than reading the environment again.
settings = load_settings()


def _install_admin_mounts() -> None:
    """Ensure Flask admin mounts are the final routes in the FastAPI app.

    The root fallback mount must stay last so dynamically-added FastAPI test
    routes (and any later app routes) are matched before Flask catches all
    remaining paths.
    """

    from a2wsgi import WSGIMiddleware
    from starlette.routing import Mount

    filtered_routes = []
    for route in app.router.routes:
        # Remove any prior compatibility mounts so we can re-add them at the end.
        if isinstance(route, Mount) and isinstance(route.app, WSGIMiddleware) and route.path in {"/admin", ""}:
            continue
        filtered_routes.append(route)

    app.router.routes = filtered_routes
    # WSGIMiddleware is an ASGI-compatible adapter that Starlette accepts at runtime,
    # but mypy sees a protocol mismatch with Starlette.mount's ASGIApp expectation.
    # ``unused-ignore`` keeps both environments happy: CI's mypy (full project venv
    # with starlette stubs) flags the arg-type error so we suppress it; pre-commit's
    # isolated mypy hook env lacks starlette and reports the type:ignore as unused,
    # which the ``unused-ignore`` category suppresses too.
    app.mount("/admin", admin_wsgi)  # type: ignore[arg-type, unused-ignore]
    app.mount("/", admin_wsgi)  # type: ignore[arg-type, unused-ignore]


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """FastAPI application lifespan — startup and shutdown hooks."""
    _install_admin_mounts()
    logger.info("FastAPI application starting up")
    yield
    logger.info("FastAPI application shutting down")
    # Service-agnostic shutdown: a service that needs teardown self-registers an
    # async close callback via ``src.core.lifecycle.register_shutdown`` at first
    # construction. This lifespan only drains the registry — it never references a
    # concrete service, which is what lets producers come and go without touching
    # it. The webhook service was the last producer and no longer has anything to
    # close: each delivery now builds and discards a transport pinned to its own
    # destination, so no pooled session survives a request (the leak this drained
    # was item #3 of the production OOM-cycle investigation, GH #1264).
    # Per-callback errors are logged and swallowed inside
    # ``run_all_shutdown_callbacks`` so they cannot mask the yielded exit.
    await run_all_shutdown_callbacks()


# Build the MCP sub-application.
# path="/" because we mount it at /mcp — routes inside are relative.
#
# json_response=True makes every response a BUFFERED application/json body instead of an
# SSE stream, and that is what lets a refused credential be answered the same way it is on
# A2A: read the AdCP code off the outgoing body, lift the status to 401. Under SSE it could
# not be, because streamable-HTTP sends http.response.start -- 200, text/event-stream --
# before the tool is dispatched, so the status was already on the wire before the error
# existed. That asymmetry was a property of the RESPONSE MODE, not of MCP.
#
# Costs nothing this seller uses: nothing in src/ streams MCP output (no report_progress,
# no partial results), the tools are request/response, and MCP's streamable-HTTP transport
# specifies JSON responses as a first-class alternative. The SSE handling that does exist
# here is in the creative-agent CLIENT, consuming another agent's stream, and is untouched.
mcp_app = mcp.http_app(path="/", json_response=True, stateless_http=True)

# Create the root FastAPI app with combined lifespans so that both
# the MCP schedulers (delivery webhooks, media-buy status) and any
# future app-level startup/shutdown hooks fire correctly.
app = FastAPI(
    title="AdCP Sales Agent",
    description="Unified REST API for the AdCP Sales Agent. Also serves MCP at /mcp and A2A at /a2a.",
    version="1.0.0",
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)

# Mount MCP at /mcp behind NOTHING.
#
# There is deliberately no auth gate in front of this. A middleware here cannot know which
# tool is being called: the name is inside the JSON-RPC body, and a middleware that parsed
# it would be re-implementing a fragment of the transport's own parsing and reading
# ToolSpec.auth a second time -- exactly the drift building-tools.md says the single
# registry exists to prevent ("auth is a property of the tool, not of a transport ... what
# makes 'MCP soft-returns where A2A hard-refuses' unrepresentable").
#
# So auth is decided where the tool IS known -- inside ``serve``, which reads the registry
# row's declaration for the named tool, the same way it does for A2A and REST. The RENDERING
# is app-wide: AuthChallengeResponder is registered as middleware over the whole app (see the
# middleware stack below), so MCP, A2A and REST are all answered by the same code and no
# transport can grow its own 401.
app.mount("/mcp", mcp_app)


# ---------------------------------------------------------------------------
# AdCP exception handlers — translate typed exceptions to HTTP responses.
# ---------------------------------------------------------------------------


def _envelope_response(request: Request, exc: Exception) -> JSONResponse:
    """Answer an exception raised OUTSIDE ``serve`` with the failure response, recorded unscoped.

    Only faults that never reached the boundary arrive here -- an exception in a route that is
    not a tool route -- so no caller was resolved and the record carries no tenant. The body
    and the status are the same the boundary answers with; REST adds only the HTTP status.

    No WWW-Authenticate here. A 401 MUST name a scheme the caller can authenticate with
    (RFC 7235; graded by the storyboard's security_baseline), and AuthChallengeResponder
    attaches it app-wide by reading the code off this very body.
    """
    response = failure_response(TransportProtocol.REST, request.url.path, exc)
    return JSONResponse(status_code=response.http_status, content=to_wire(response))


@app.exception_handler(AdCPSalesAgentError)
async def adcp_error_handler(request: Request, exc: AdCPSalesAgentError) -> JSONResponse:
    """A typed error raised outside ``serve`` is answered with its failure response."""
    return _envelope_response(request, exc)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """A raw ``ValueError`` is typed by ``adcp_error_for`` like on every other transport.

    The mapping is type-keyed, and the order inside ``adcp_error_for`` is what makes a pydantic
    ``ValidationError`` (a ``ValueError`` SUBCLASS, so it arrives at this handler) come out as
    INVALID_REQUEST carrying ``field`` and ``issues`` rather than as a bare VALIDATION_ERROR.

    Does NOT catch FastAPI's ``RequestValidationError`` (separate class, not a ValueError
    subclass) -- that has its own handler below.
    """
    return _envelope_response(request, exc)


def _jsonpath_lite(loc: list[str]) -> str:
    """Join a pydantic ``loc`` into the JSONPath-lite form ``field`` is specified in.

    core/error.json: "Field path ... in JSONPath-lite format (e.g., 'packages[0].targeting')".
    Numeric segments are array indices and are bracketed onto the preceding segment; every
    other segment is dot-joined.
    """
    out: list[str] = []
    for seg in loc:
        if seg.isdigit() and out:
            out[-1] = f"{out[-1]}[{seg}]"
        else:
            out.append(seg)
    return ".".join(out)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Translate FastAPI request-body schema failures into the AdCP envelope.

    A payload that is malformed or violates a schema constraint (a missing,
    mistyped, out-of-enum, or out-of-range field — exactly what FastAPI's
    ``RequestValidationError`` represents) maps to the standard ``INVALID_REQUEST``
    code per the AdCP error-code vocabulary ("Request is malformed, missing
    required fields, or violates schema constraints").

    Without this handler FastAPI emits its default raw ``422 {"detail": [...]}``,
    which is NOT the two-layer envelope buyers parse — so the REST boundary
    silently diverged from MCP/A2A (which wrap schema rejections in the AdCP
    envelope). Surfacing the first failure's pointer as ``field`` keeps the
    response actionable; the full list is preserved under ``details``.
    """
    # FastAPI prefixes every loc with the request LOCATION ("body"/"query"/"path"). That is
    # a framework detail, not part of the buyer's document, so it is stripped once here --
    # for the issues[] pointers as well as for ``field``, which used to strip it alone and
    # left issues reading "/body/idempotency_key" where the pin wants "/idempotency_key".
    errors = [
        {**e, "loc": tuple(e.get("loc", ())[1:])}
        if e.get("loc") and str(e["loc"][0]) in ("body", "query", "path")
        else e
        for e in exc.errors()
    ]
    first = errors[0] if errors else {}
    # Drop ONLY the leading "body"/"query"/"path" location segment (the FastAPI
    # location prefix); join the rest into the JSONPath-lite ``field`` the envelope
    # already uses (e.g. attribution_window.post_click.interval). Stripping at any
    # position would erase a body field literally named "query"/"body"/"path".
    raw_loc = [str(p) for p in first.get("loc", ())]
    loc = raw_loc[1:] if raw_loc and raw_loc[0] in ("body", "query", "path") else raw_loc
    # JSONPath-lite, so an ARRAY INDEX is bracketed: core/error.json's own example is
    # 'packages[0].targeting'. Dot-joining every segment produced 'packages.0.package_id'
    # here while mcp and a2a produced 'packages[0].package_id' for the identical rejection
    # -- one buyer-facing pointer per transport, from a formatting detail.
    field = _jsonpath_lite(loc) or None
    # A rejection raised by request-schema validation is, by construction, a
    # SCHEMA-constraint violation: FastAPI only ever raises it for what the
    # pinned JSON Schema declares (3.1/core/duration.json gives interval
    # {"minimum": 1} and unit an enum, so interval=0 and unit="weeks" are
    # schema violations, not business-rule ones). INVALID_REQUEST is the code
    # for that per 3.1/enums/error-code.json, and it is what MCP and A2A
    # already emit for the same payload -- this path previously special-cased
    # the attribution_window family to VALIDATION_ERROR, which made one request
    # answer with two different codes depending on the transport it arrived on.
    # Cross-field rules JSON Schema cannot express (a "campaign"-unit Duration
    # must have interval == 1) are NOT reached here: they are enforced after
    # the model validates, and correctly raise AdCPValidationError there.
    adcp_exc = AdCPInvalidRequestError(
        field=field,
        issues=issues_from_validation_error(errors),
    )
    return _envelope_response(request, adcp_exc)


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    """A raw ``PermissionError`` is PERMISSION_DENIED, the 403 every transport emits for it."""
    return _envelope_response(request, exc)


@app.exception_handler(Exception)
async def untyped_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """An untyped exception is INTERNAL_ERROR in the same body, never Starlette's bare 500.

    Registration ORDER is irrelevant: Starlette keys handlers by exception CLASS and resolves
    by walking ``type(exc).__mro__`` for the nearest registered ancestor, so the typed handlers
    above keep theirs wherever this one sits, and this catches only what has no more specific
    handler. Starlette re-raises after a handler registered for ``Exception`` answers, so the
    traceback also reaches the server log.
    """
    return _envelope_response(request, exc)


# ---------------------------------------------------------------------------
# A2A Integration — add routes directly to the FastAPI app (not as sub-app)
# so middleware and scope["state"] propagate correctly within the same ASGI app.
# ---------------------------------------------------------------------------


def _restore_a2a_wire_integers(
    endpoint: Callable[[Request], Awaitable[Response]],
) -> Callable[[Request], Awaitable[Response]]:
    """Wrap an a2a-sdk JSON-RPC endpoint to fix up integer fields on the response.

    The a2a-sdk builds the response body via
    ``google.protobuf.json_format.MessageToDict`` on the ``Task``, which
    recursively converts every ``Part.data`` (a ``google.protobuf.Value``) to
    a dict. ``Value`` has no integer variant -- every number comes back as a
    JSON float (86400 -> 86400.0), regardless of what type was originally
    placed there. This is the one point where we see the
    real outgoing JSON body for the ``/a2a`` route and can restore known
    integer-typed AdCP fields before it reaches the client -- see
    ``restore_a2a_integer_types`` for the shared coercion logic and the
    field list's spec citations.

    Being that one point, it is also where a REFUSED CREDENTIAL becomes a 401. A2A frames
    every failure as a JSON-RPC error inside an HTTP 200, which is right for an application
    answer and wrong for this one: a caller with no identity cannot read an AdCP envelope to
    learn how to authenticate, and the storyboard's security_baseline grades the HTTP
    handshake. The body is fully buffered here -- it is already being parsed and re-emitted
    -- so the status is simply set on the response being built, with no side channel and no
    ordering hazard. AUTH_INVALID reaches this point too, because A2A validates the token
    before answering; MCP cannot say the same (see the pre-dispatch gate).
    """

    async def _wrapped(request: Request) -> Response:
        response = await endpoint(request)
        if isinstance(response, JSONResponse) and response.body:
            # Integer restoration ONLY. This used to also lift an auth refusal to 401 and
            # attach the challenge -- a second copy of AuthChallengeResponder, living in a
            # function whose job is protobuf number coercion. The responder is app-wide
            # middleware now and answers this route like every other.
            fixed = restore_a2a_integer_types(json.loads(bytes(response.body)))
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return JSONResponse(fixed, status_code=response.status_code, headers=headers)
        return response

    # Marker for test_guards_a2a_integer_restoration.py -- lets the structural
    # guard confirm every /a2a route endpoint is wrapped without depending on
    # closure internals or function identity. setattr (not dot-assignment)
    # since _wrapped has no statically-declared attribute for this.
    setattr(_wrapped, "__a2a_integer_restoration_wrapped__", True)  # noqa: B010
    return _wrapped


# Create the A2A application and add routes
_agent_card = create_agent_card()
_request_handler = AdCPRequestHandler()

# Build A2A routes using a2a-sdk 1.0 route factories
# NO v0.3 COMPATIBILITY. `enable_v0_3_compat` is deliberately not passed (it defaults
# off), so `/a2a` speaks the native 1.0 surface only: `SendMessage` with an
# `A2A-Version: 1.0` header, not the 0.3 `message/send` family.
#
# The shim was not merely redundant, it was destructive. The SDK dispatches to it by
# METHOD NAME before any version check, and its catch-all
# (a2a/compat/v0_3/jsonrpc_adapter.py) rebuilt every raised exception as
# `CoreInternalError(message=str(e))` -- keeping the message string and discarding both
# the error TYPE and its `data`. So the two-layer AdCP envelope this server attaches to
# an auth refusal never reached the wire, `AuthChallengeResponder` found no code to read,
# and an unauthenticated call got 200 instead of 401. On the native path the same refusal
# arrives intact: JSON-RPC -32600, `data.adcp_error.code == AUTH_MISSING`, lifted to 401
# with `WWW-Authenticate`.
# The SDK's default context builder places ``dict(request.headers)`` on the call context's
# ``state["headers"]``, which is all the handler reads.
_a2a_rpc_routes_raw = create_jsonrpc_routes(
    request_handler=_request_handler,
    rpc_url="/a2a",
    # The SDK's default builder, plus the captured HTTP message an RFC 9421 signature covers
    # — see src/a2a_server/context_builder.py. It carries a value; it decides nothing.
    context_builder=AdCPCallContextBuilder(),
)
# Rebuild each route with an integer-restoring wrapper around its endpoint --
# mutating route.endpoint in place would not change dispatch, since Starlette
# builds the actual ASGI app from the endpoint at Route construction time.
_a2a_rpc_routes = [
    Route(path=route.path, endpoint=_restore_a2a_wire_integers(route.endpoint), methods=list(route.methods or []))
    for route in _a2a_rpc_routes_raw
]
_a2a_card_routes = create_agent_card_routes(
    agent_card=_agent_card,
    card_url="/.well-known/agent-card.json",
)

# Add routes directly to the FastAPI app
for route in _a2a_rpc_routes + _a2a_card_routes:
    app.routes.append(route)
logger.info("A2A routes added: /a2a, /.well-known/agent-card.json")


@app.api_route("/a2a/", methods=["GET", "POST", "OPTIONS"])
async def a2a_trailing_slash_redirect() -> RedirectResponse:
    """Preserve historical /a2a/ compatibility.

    The admin root fallback mount would otherwise catch `/a2a/` and hand it to
    Flask, which returns 404. Redirecting here keeps A2A owned by FastAPI.
    """

    return RedirectResponse(url="/a2a", status_code=307)


# ---------------------------------------------------------------------------
# Dynamic agent card endpoints — override SDK defaults to support
# tenant-specific URLs based on request headers.
# ---------------------------------------------------------------------------


_VALID_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*(\:\d{1,5})?$"
)


def _is_valid_hostname(value: str) -> bool:
    """Validate that a string is a safe hostname (with optional port). Rejects path traversal and injection chars."""
    return bool(value) and len(value) <= 253 and _VALID_HOSTNAME_RE.match(value) is not None


def _card_with_url(server_url: str):
    """A copy of the static agent card advertising *server_url* as its interface."""
    dynamic_card = A2AAgentCard()
    dynamic_card.CopyFrom(_agent_card)
    if dynamic_card.supported_interfaces:
        dynamic_card.supported_interfaces[0].url = server_url
    return dynamic_card


def _canonical_a2a_url(headers) -> str | None:
    """The tenant's canonical A2A endpoint URL for this Host, or None.

    Resolves the Host to a tenant and reads that tenant's STORED host, so the
    card advertises the same string brand.json's A2A ``agents[].url`` carries —
    the byte-equal match at security.mdx step 5 compares the URL a counterparty
    invoked against what we published, and two derivations means two chances to
    disagree on a scheme, a port or a trailing slash.

    Returns None when the Host routes to no tenant, which is the only case where
    the caller still has to derive something from headers.
    """
    routing = route_landing_page(dict(headers))
    if not routing.tenant:
        return None
    identity = agent_identity_for_tenant_id(routing.tenant["tenant_id"])
    return identity.endpoints["a2a"] if identity else None


def _create_dynamic_agent_card(request: Request):
    """Create agent card with the tenant's canonical A2A URL.

    When the Host routes to a tenant, the URL comes from that tenant's stored
    host (:func:`canonical_agent_url`) — NOT from ``Apx-Incoming-Host`` /
    ``Host`` / ``X-Forwarded-Proto``, which is the reverse-proxy routing state
    security.mdx step 10 forbids deriving identity from. The header ladder below
    survives only as the no-tenant fallback, where there is nothing stored to
    read.
    """

    def get_protocol(hostname: str) -> str:
        # Prefer the scheme the edge proxy terminated and forwarded
        # (X-Forwarded-Proto, set by our nginx) — the authoritative signal for the
        # client-facing scheme. Fall back to a hostname heuristic only when the
        # header is absent (e.g. direct, non-proxied access). This matches how the
        # admin app already trusts X-Forwarded-Proto, and fixes the agent card
        # advertising https for an http-only reverse proxy.
        forwarded_proto = _get_header_case_insensitive(request.headers, "X-Forwarded-Proto")
        if forwarded_proto:
            # May be a comma-separated proxy chain; the first hop is client-facing.
            proto = forwarded_proto.split(",")[0].strip().lower()
            if proto in ("http", "https"):
                return proto
        return "http" if hostname.startswith("localhost") or hostname.startswith("127.0.0.1") else "https"

    server_url = _canonical_a2a_url(request.headers)
    if server_url is not None:
        return _card_with_url(server_url)

    apx_incoming_host = _get_header_case_insensitive(request.headers, "Apx-Incoming-Host")
    if apx_incoming_host and not _is_valid_hostname(apx_incoming_host):
        logger.warning(f"Invalid Apx-Incoming-Host header value, ignoring: {apx_incoming_host!r}")
        apx_incoming_host = None
    if apx_incoming_host:
        protocol = get_protocol(apx_incoming_host)
        server_url = f"{protocol}://{apx_incoming_host}/a2a"
    else:
        host = _get_header_case_insensitive(request.headers, "Host") or ""
        if host and not _is_valid_hostname(host):
            logger.warning(f"Invalid Host header value, ignoring: {host!r}")
            host = ""
        sales_domain = get_sales_agent_domain()
        if host and host != sales_domain:
            protocol = get_protocol(host)
            server_url = f"{protocol}://{host}/a2a"
        else:
            server_url = get_a2a_server_url() or "http://localhost:8080/a2a"

    return _card_with_url(server_url)


# Override the SDK's static agent card endpoints with dynamic ones.
# We replace routes by matching path — SDK routes were added above.

_AGENT_CARD_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"}


def _replace_routes():
    """Replace SDK agent card routes with dynamic versions that read request headers."""

    async def dynamic_agent_card(request: Request):
        # to_thread: the card now reads the tenant's stored host from the
        # database, and this endpoint is unauthenticated.
        card = await asyncio.to_thread(_create_dynamic_agent_card, request)
        return JSONResponse(agent_card_to_dict(card))

    replaced_paths: set[str] = set()
    new_routes = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in _AGENT_CARD_PATHS:
            new_routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))
            replaced_paths.add(path)
        else:
            new_routes.append(route)

    # The SDK's route factory mounts exactly ONE path (a2a-sdk's
    # AGENT_CARD_WELL_KNOWN_PATH), so a pass that only REPLACES leaves every other
    # declared path unrouted -- /.well-known/agent.json (the path AdCP's own guide
    # names, and the one the tenant landing page publishes a link to) and
    # /agent.json both 404'd. Create what there was nothing to replace, reusing the
    # SAME handler and methods: one closure serves every path, so their bodies are
    # byte-identical by construction rather than by convention. Sorted for a
    # deterministic route table. Appending at import time is safe because
    # _install_admin_mounts() re-appends the Flask "" catch-all during lifespan
    # startup, after this runs.
    for path in sorted(_AGENT_CARD_PATHS - replaced_paths):
        new_routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))
        replaced_paths.add(path)

    app.router.routes = new_routes

    missing = _AGENT_CARD_PATHS - replaced_paths
    if missing:
        logger.warning(f"_replace_routes: expected SDK routes not found for paths: {sorted(missing)}")


_replace_routes()

# ---------------------------------------------------------------------------
# A2A messageId compatibility middleware (body rewriting, unrelated to auth)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def a2a_messageid_compatibility_middleware(request: Request, call_next):
    """Handle both numeric and string messageId for backward compatibility."""
    # ``path_from_asgi_scope``, not ``request.url.path``: this predicate selects a ROUTE, so
    # it has to read the path the router matches on — ``root_path`` stripped. Mounted under
    # a prefix, ``request.url.path`` is ``/adcp/a2a`` and this never fires again.
    if path_from_asgi_scope(request.scope) == "/a2a" and request.method == "POST":
        body = await request.body()
        try:
            data = json.loads(body)

            if isinstance(data, dict) and "params" in data:
                params = data.get("params", {})
                if "message" in params and isinstance(params["message"], dict):
                    message = params["message"]
                    if "messageId" in message and isinstance(message["messageId"], (int, float)):
                        logger.warning(
                            f"Converting numeric messageId {message['messageId']} to string for compatibility"
                        )
                        message["messageId"] = str(message["messageId"])
                        body = json.dumps(data).encode()

            if "id" in data and isinstance(data["id"], (int, float)):
                logger.warning(f"Converting numeric JSON-RPC id {data['id']} to string for compatibility")
                data["id"] = str(data["id"])
                body = json.dumps(data).encode()

        except (json.JSONDecodeError, KeyError):
            pass

        # Reconstruct request with potentially modified body
        from starlette.requests import Request as StarletteRequest

        async def _receive():
            return {"type": "http.request", "body": body}

        request = StarletteRequest(request.scope, receive=_receive)

    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Health and debug routes
# ---------------------------------------------------------------------------

app.include_router(api_v1_router)


def _openapi_with_rest_components() -> dict[str, Any]:
    """The generated OpenAPI document, with every REST request body's nested models resolvable.

    The REST routes advertise their DTOs through ``openapi_extra`` with ``$ref``s aimed at
    ``#/components/schemas/<Model>``; FastAPI only populates ``components/schemas`` for models
    it validates itself, and it no longer validates those bodies (``serve`` does). So the
    models the refs name are merged in here, once, from the same DTOs -- one declaration,
    published in one place, resolvable from the document root.
    """
    from fastapi.openapi.utils import get_openapi

    from src.routes.api_v1 import REST_COMPONENT_SCHEMAS

    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {}).setdefault("schemas", {}).update(REST_COMPONENT_SCHEMAS)
    return schema


app.include_router(health_router)
# The debug and reset routes EXIST only where the deployment allows them. Selected here,
# at composition, rather than answering 404 per request from inside the route.
if settings.debug_routes_enabled:
    app.include_router(health_debug_router)

# Trust root (#1291 A3): /.well-known/{brand,adagents,jwks}.json and the signed
# revocation list. Registered at import time so it is matched before
# _install_admin_mounts() re-appends the Flask "" catch-all at lifespan startup.
app.include_router(well_known_router)

# ---------------------------------------------------------------------------
# Middleware stack (via add_middleware — outermost = last registered):
#   1. AuthChallengeResponder (outermost — renders EVERY transport's 401)
#   2. CORSMiddleware (adds CORS headers to all responses)
#   3. SignedExchangeCapture (innermost — records the message, decides nothing)
#
# No auth middleware. Each transport hands the request headers to the boundary, and the
# resolver behind it is the one reader of a credential — the bearer AND the RFC 9421
# signature. The capture below is not an exception to that: it reads no credential and
# refuses nothing. It exists because a signature covers things the boundary cannot rebuild
# (the exact bytes, and ``@target-uri`` from ``raw_path``), so they have to be recorded where
# they still exist. INNERMOST, so that nothing between it and the app can rewrite the body it
# recorded a digest-able copy of.
# ---------------------------------------------------------------------------

app.add_middleware(SignedExchangeCapture)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.runtime.allowed_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Outermost, so it sees the FINAL response of every transport -- the MCP mount, the A2A
# routes and the REST routers alike. This is the only place a 401 challenge is written.
app.add_middleware(AuthChallengeResponder)

# ---------------------------------------------------------------------------
# Admin UI — mount Flask admin via WSGIMiddleware
# ---------------------------------------------------------------------------

flask_admin_app = create_app(settings=settings)
admin_wsgi = WSGIMiddleware(flask_admin_app)


# ---------------------------------------------------------------------------
# Landing page routes
# ---------------------------------------------------------------------------


async def _handle_landing_page(request: Request):
    """Common landing page logic for root and /landing routes."""
    result = await asyncio.to_thread(route_landing_page, dict(request.headers))
    logger.info(
        f"[LANDING] Routing decision: type={result.type}, host={result.effective_host}, "
        f"tenant={'yes' if result.tenant else 'no'}"
    )

    if result.type == "admin":
        return RedirectResponse(url="/admin/login", status_code=302)

    if result.type in ("custom_domain", "subdomain") and result.tenant:
        try:
            html_content = await asyncio.to_thread(generate_tenant_landing_page, result.tenant, result.effective_host)
            return HTMLResponse(content=html_content)
        except Exception as e:
            logger.error(f"Error generating landing page: {e}", exc_info=True)
            return HTMLResponse(
                content=generate_fallback_landing_page(
                    f"Error generating landing page for {result.tenant.get('name', 'tenant')}"
                )
            )

    # Custom domain not configured for any tenant
    if result.type == "custom_domain":
        return HTMLResponse(content=generate_fallback_landing_page(f"Domain {result.effective_host} is not configured"))

    return HTMLResponse(content=generate_fallback_landing_page("No tenant found"))


# NOTE: These landing routes must be added BEFORE the /admin mount catch-all
# so FastAPI matches them first. We insert at position 0 (before mounts).

app.router.routes.insert(0, Route("/", _handle_landing_page, methods=["GET"]))
app.router.routes.insert(1, Route("/landing", _handle_landing_page, methods=["GET"]))

logger.info("FastAPI app created: MCP at /mcp, A2A at /a2a, Admin at /admin")


# Assembled LAST, after every route this module registers. ``get_openapi`` reads ``app.routes``
# at the moment it is called, so assigning this ahead of a router drops that router's paths from
# the document -- measured at 15 published against 23 eligible when it sat between two routers.
# The A2A, landing and agent-card routes are plain Starlette ``Route``s, which FastAPI never
# documents, so 23 ``APIRoute`` paths is the whole document and their absence is by design.
app.openapi_schema = _openapi_with_rest_components()

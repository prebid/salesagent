"""Central FastAPI application.

Mounts all sub-applications (MCP, A2A, Admin) into a single process.
Replaces the previous multi-process architecture where MCP, A2A, and Admin
ran as separate processes behind nginx.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_jsonrpc_routes
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
    render_agent_card,
    restore_a2a_integer_types,
)
from src.admin.app import create_app
from src.core.auth_middleware import AuthChallengeResponder
from src.core.config import load_settings
from src.core.domain_routing import route_landing_page
from src.core.errors.details import ConfigurationDetails
from src.core.errors.issues import issues_from_validation_error
from src.core.exceptions import AdCPConfigurationError, AdCPInvalidRequestError, AdCPSalesAgentError
from src.core.lifecycle import run_all_shutdown_callbacks
from src.core.main import mcp
from src.core.resolved_identity import TransportProtocol, public_identity_for
from src.core.tools._boundary import failure_response
from src.core.tools._wire import to_wire
from src.landing import generate_tenant_landing_page
from src.landing.landing_page import generate_fallback_landing_page
from src.routes.api_v1 import router as api_v1_router
from src.routes.health import debug_router as health_debug_router
from src.routes.health import router as health_router
from src.services.seller_capabilities import describe_seller

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


# Create the A2A application and add routes. There is deliberately no module-level
# card: a card describes a TENANT, so it is built per request from that tenant's
# seller description. The static one existed only for _card_with_url to copy.
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
)
# Rebuild each route with an integer-restoring wrapper around its endpoint --
# mutating route.endpoint in place would not change dispatch, since Starlette
# builds the actual ASGI app from the endpoint at Route construction time.
_a2a_rpc_routes = [
    Route(path=route.path, endpoint=_restore_a2a_wire_integers(route.endpoint), methods=list(route.methods or []))
    for route in _a2a_rpc_routes_raw
]
# The card's routes are NOT taken from the SDK factory. It mounts one static path from
# one static card, which states twice what is declared elsewhere once -- which paths serve
# the card (_AGENT_CARD_PATHS, below) and what the card says (the seller description). The
# card routes are derived from that declaration instead, so there is nothing to reconcile.
for route in _a2a_rpc_routes:
    app.routes.append(route)
logger.info("A2A routes added: /a2a")


@app.api_route("/a2a/", methods=["GET", "POST", "OPTIONS"])
async def a2a_trailing_slash_redirect():
    """Preserve historical /a2a/ compatibility.

    The admin root fallback mount would otherwise catch `/a2a/` and hand it to
    Flask, which returns 404. Redirecting here keeps A2A owned by FastAPI.
    """

    return RedirectResponse(url="/a2a", status_code=307)


# ---------------------------------------------------------------------------
# Dynamic agent card endpoints — override SDK defaults to support
# tenant-specific URLs based on request headers.
# ---------------------------------------------------------------------------


def _create_dynamic_agent_card(request: Request) -> A2AAgentCard:
    """This tenant's agent card.

    Two calls and a render, and deliberately nothing else. Which tenant comes from
    the resolver's own seam, the same answer every tool request gets; what the card
    SAYS comes from the one service ``get_adcp_capabilities`` renders from, so the
    two cannot describe the same seller differently. Neither the URL nor any field
    is derived here.

    A request naming no tenant this deployment serves is refused by the resolver, and a
    tenant that declares no host of its own is refused here: a card whose ``url`` cannot
    be stated is a card that cannot be published. Both are CONFIGURATION_ERROR, and both
    are seller-side.

    What used to happen instead was a ladder over request headers, which published
    whatever host the caller asked for:
    ``Host: evil.example.com`` came back as
    ``supportedInterfaces[0].url == "https://evil.example.com/a2a"``, behind nothing but a
    syntax check. Every value in that ladder was attacker-supplied on a direct connection,
    and it answered a question ``canonical_agent_url`` already answers from stored state.
    """
    identity = public_identity_for(request.headers)
    seller = describe_seller(identity)
    if seller.agent_url is None:
        tenant = identity.tenant
        raise AdCPConfigurationError(details=ConfigurationDetails(tenant_id=tenant.tenant_id if tenant else None))
    return render_agent_card(seller)


# The paths the agent card is served on. This set is the declaration; every card route
# derives from it, so there is nothing for a route table to disagree with. /.well-known/
# agent.json is the path AdCP's own guide names and the one the tenant landing page links
# to; /agent.json is the legacy spelling. Sorted so the route table is deterministic.
_AGENT_CARD_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"}


def _install_agent_card_routes():
    """Serve the card on every declared path, from the one handler.

    One closure serves all of them, so their bodies are byte-identical by construction
    rather than by convention. Appending at import time is safe because
    _install_admin_mounts() re-appends the Flask "" catch-all during lifespan startup,
    after this runs.
    """

    async def dynamic_agent_card(request: Request):
        # to_thread: resolving the tenant and describing the seller both hit the
        # database, and this endpoint is unauthenticated.
        try:
            card = await asyncio.to_thread(_create_dynamic_agent_card, request)
        except AdCPSalesAgentError as exc:
            # The card is served OUTSIDE `serve`, so this route builds the failure the way
            # every transport does for a fault in its own container handling -- one builder,
            # so the envelope a buyer gets here is the envelope it gets anywhere.
            failure = failure_response(TransportProtocol.A2A, "agent_card", exc)
            return JSONResponse(status_code=failure.http_status, content=to_wire(failure))
        return JSONResponse(agent_card_to_dict(card))

    for path in sorted(_AGENT_CARD_PATHS):
        app.routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))


_install_agent_card_routes()

# ---------------------------------------------------------------------------
# A2A messageId compatibility middleware (body rewriting, unrelated to auth)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def a2a_messageid_compatibility_middleware(request: Request, call_next):
    """Handle both numeric and string messageId for backward compatibility."""
    if request.url.path == "/a2a" and request.method == "POST":
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

# ---------------------------------------------------------------------------
# Middleware stack (via add_middleware — outermost = last registered):
#   1. AuthChallengeResponder (outermost — renders EVERY transport's 401)
#   2. CORSMiddleware (adds CORS headers to all responses)
#
# No auth middleware. Each transport hands the request headers to the boundary, and the
# resolver behind it is the one reader of a credential.
# ---------------------------------------------------------------------------

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

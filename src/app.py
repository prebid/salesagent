"""Central FastAPI application.

Mounts all sub-applications (MCP, A2A, Admin) into a single process.
Replaces the previous multi-process architecture where MCP, A2A, and Admin
ran as separate processes behind nginx.
"""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.types import AgentCard as A2AAgentCard
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp.exceptions import ToolError
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.routing import Route

from src.a2a_server.adcp_a2a_server import (
    AdCPRequestHandler,
    create_agent_card,
)
from src.a2a_server.context_builder import AdCPCallContextBuilder
from src.admin.app import create_app
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.core.domain_config import get_a2a_server_url, get_sales_agent_domain
from src.core.domain_routing import route_landing_page
from src.core.exceptions import (
    INVALID_REQUEST_SUGGESTION,
    VALIDATION_ERROR_SUGGESTION,
    AdCPError,
    AdCPInvalidRequestError,
    AdCPValidationError,
    build_two_layer_error_envelope,
    build_validation_error_details,
    normalize_to_adcp_error,
)
from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive
from src.core.lifecycle import run_all_shutdown_callbacks
from src.core.main import mcp
from src.core.resolved_identity import resolve_identity
from src.core.tool_error_logging import handle_tool_error, record_boundary_error
from src.landing import generate_tenant_landing_page
from src.landing.landing_page import generate_fallback_landing_page
from src.routes.api_v1 import router as api_v1_router
from src.routes.health import debug_router as health_debug_router
from src.routes.health import router as health_router
from src.routes.rest_compat_middleware import RestCompatMiddleware

logger = logging.getLogger(__name__)


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
    # Service-agnostic shutdown: every service that needs teardown
    # self-registers an async close callback via
    # ``src.core.lifecycle.register_shutdown`` at first construction. This
    # lifespan only drains the registry — it never references a concrete
    # service. Releases long-lived HTTP sessions / connection pools (e.g.
    # the webhook service's ``requests.Session``) before process exit; that
    # is leak triage item #3 from the production OOM-cycle investigation
    # (GH #1264). Per-callback errors are logged and swallowed inside
    # ``run_all_shutdown_callbacks`` so they cannot mask the yielded exit.
    await run_all_shutdown_callbacks()


# Build the MCP sub-application.
# path="/" because we mount it at /mcp — routes inside are relative.
mcp_app = mcp.http_app(path="/")

# Create the root FastAPI app with combined lifespans so that both
# the MCP schedulers (delivery webhooks, media-buy status) and any
# future app-level startup/shutdown hooks fire correctly.
app = FastAPI(
    title="AdCP Sales Agent",
    description="Unified REST API for the AdCP Sales Agent. Also serves MCP at /mcp and A2A at /a2a.",
    version="1.0.0",
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)

# Mount MCP at /mcp
app.mount("/mcp", mcp_app)


# ---------------------------------------------------------------------------
# AdCP exception handlers — translate typed exceptions to HTTP responses.
# ---------------------------------------------------------------------------


def _envelope_response(request: Request, exc: AdCPError) -> JSONResponse:
    """Build a JSONResponse carrying the two-layer envelope for ``exc``.

    Single source of truth for the REST envelope-response shape — used by
    every exception handler so HTTP status, body envelope, wire codes,
    and observability (logger + activity feed + audit log) are constructed
    identically regardless of which exception type fired the handler.

    Symmetric with the MCP and A2A boundaries: all three transports delegate
    to ``record_boundary_error`` so log severity, activity-feed publishing,
    and audit logging stay in lockstep. Identity is not resolved on
    ``request.state`` at the exception-handler boundary, so we resolve it
    best-effort here (auth token + tenant headers) to populate the
    tenant-scoped sinks (activity feed, audit log) for REST errors the same
    way MCP and A2A do. Identity resolution never raises into the error path —
    a lookup miss degrades to anonymous and ``record_boundary_error`` falls
    back to the WARNING log line carrying the error code, message, and path.
    """
    tenant_id, principal_id = _best_effort_rest_identity(request)
    record_boundary_error("rest", request.url.path, exc, tenant_id=tenant_id, principal_id=principal_id)
    return JSONResponse(
        status_code=exc.status_code,
        content=build_two_layer_error_envelope(exc),
    )


def _best_effort_rest_identity(request: Request) -> tuple[str | None, str | None]:
    """Resolve ``(tenant_id, principal_id)`` for boundary observability only.

    Used solely to scope the activity-feed and audit-log sinks in
    ``record_boundary_error`` — never to make an authorization decision.
    ``require_valid_token=False`` so an invalid/expired token (which may be
    the very error being handled) still yields a tenant from the host headers
    instead of raising. Any failure degrades to ``(None, None)``; observability
    must not shadow the buyer's original error.
    """
    try:
        identity = resolve_identity(dict(request.headers), protocol="rest", require_valid_token=False)
        return identity.tenant_id, identity.principal_id
    except Exception:
        logger.debug("REST boundary: best-effort identity resolution failed", exc_info=True)
        return None, None


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    """Convert AdCP exceptions to the spec-compliant two-layer envelope.

    Body shape::

        {
            "adcp_error": {"code": "...", "message": "...", ...},
            "errors": [{"code": "...", "message": "...", ...}],
            "context": {...},     # echoed when present
        }

    HTTP status comes from ``exc.status_code``; the matching MCP/A2A
    transport markers (``isError: true`` / ``failed``) are set by their
    own boundary translators. Wire codes are translated through
    ``ERROR_CODE_MAPPING`` inside the envelope builder. Logging happens
    in ``_envelope_response`` so all three handlers leave a uniform
    breadcrumb.
    """
    return _envelope_response(request, exc)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Cross-transport symmetry: REST wraps raw ``ValueError`` as VALIDATION_ERROR.

    MCP's ``translate_to_tool_error`` and A2A's dispatcher both catch raw
    ``ValueError`` and wrap it in a synthetic ``AdCPValidationError`` envelope.
    REST mirrors that here so a buyer-facing ``ValueError`` raised by
    application code surfaces with the same wire shape and HTTP 400 status
    on every transport, not the 500 default FastAPI gives unhandled errors.

    Does NOT catch FastAPI's ``RequestValidationError`` (separate class, not a
    ValueError subclass) — that has its own handler below.
    """
    return _envelope_response(request, normalize_to_adcp_error(exc))


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
    errors = exc.errors()
    first = errors[0] if errors else {}
    # Drop ONLY the leading "body"/"query"/"path" location segment (the FastAPI
    # location prefix); join the rest into the JSONPath-lite ``field`` the envelope
    # already uses (e.g. attribution_window.post_click.interval). Stripping at any
    # position would erase a body field literally named "query"/"body"/"path".
    raw_loc = [str(p) for p in first.get("loc", ())]
    loc = raw_loc[1:] if raw_loc and raw_loc[0] in ("body", "query", "path") else raw_loc
    field = ".".join(loc) or None
    message = first.get("msg") or "Request failed schema validation"
    # Code selection by failure semantics, grounded in the AdCP graded
    # error-compliance storyboard: a VALUE/enum/range violation on a
    # structurally-valid field is canonically VALIDATION_ERROR; a missing/
    # malformed/unknown field (structural) is INVALID_REQUEST. The full
    # value-vs-structural reclassification across all fields is a repo-wide
    # follow-up; for now the attribution_window family — reconciled to
    # VALIDATION_ERROR upstream in adcp-req — is mapped explicitly. (salesagent-meho)
    if field and field.startswith("attribution_window"):
        exc_cls, suggestion = AdCPValidationError, VALIDATION_ERROR_SUGGESTION
    else:
        exc_cls, suggestion = AdCPInvalidRequestError, INVALID_REQUEST_SUGGESTION
    adcp_exc = exc_cls(
        message,
        field=field,
        suggestion=suggestion,
        details=build_validation_error_details(errors),
    )
    return _envelope_response(request, adcp_exc)


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    """Cross-transport symmetry: REST wraps raw ``PermissionError`` as AUTH_REQUIRED.

    Mirror of the MCP / A2A boundaries which translate ``PermissionError`` to
    a synthetic ``AdCPAuthorizationError`` envelope. Without this handler a
    raw ``PermissionError`` on the REST path would render as a 500 server
    error instead of the 403 authorization envelope every transport should
    emit for the same condition.
    """
    return _envelope_response(request, normalize_to_adcp_error(exc))


@app.exception_handler(ToolError)
async def tool_error_handler(request: Request, exc: ToolError) -> JSONResponse:
    """Global ToolError handler — catches MCP boundary errors that reach REST.

    The MCP boundary translator (``with_error_logging``) converts typed
    AdCPErrors into ``AdCPToolError`` carrying a two-layer envelope and
    ``status_code``. When MCP-wrapped tools are invoked from REST paths and
    that envelope bubbles up, this handler forwards it unchanged — removing
    the need for every REST route to duplicate a ``try/except ToolError``
    block. Plain ``ToolError`` (no typed source) falls through
    ``handle_tool_error``'s ``_ERROR_CODE_TO_STATUS`` lookup.

    Matches subclasses, so ``AdCPToolError`` is caught here too.
    """
    return handle_tool_error(exc)


# ---------------------------------------------------------------------------
# A2A Integration — add routes directly to the FastAPI app (not as sub-app)
# so middleware and scope["state"] propagate correctly within the same ASGI app.
# ---------------------------------------------------------------------------


# Create the A2A application and add routes
_agent_card = create_agent_card()
_request_handler = AdCPRequestHandler()

# Build A2A routes using a2a-sdk 1.0 route factories
_a2a_rpc_routes = create_jsonrpc_routes(
    request_handler=_request_handler,
    rpc_url="/a2a",
    context_builder=AdCPCallContextBuilder(),
    enable_v0_3_compat=True,
)
_a2a_card_routes = create_agent_card_routes(
    agent_card=_agent_card,
    card_url="/.well-known/agent-card.json",
)

# Add routes directly to the FastAPI app
for route in _a2a_rpc_routes + _a2a_card_routes:
    app.routes.append(route)
logger.info("A2A routes added: /a2a, /.well-known/agent-card.json")


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


_VALID_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*(\:\d{1,5})?$"
)


def _is_valid_hostname(value: str) -> bool:
    """Validate that a string is a safe hostname (with optional port). Rejects path traversal and injection chars."""
    return bool(value) and len(value) <= 253 and _VALID_HOSTNAME_RE.match(value) is not None


def _create_dynamic_agent_card(request: Request):
    """Create agent card with tenant-specific URL from request headers."""

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

    dynamic_card = A2AAgentCard()
    dynamic_card.CopyFrom(_agent_card)
    # Update the URL in supported_interfaces
    if dynamic_card.supported_interfaces:
        dynamic_card.supported_interfaces[0].url = server_url
    return dynamic_card


# Override the SDK's static agent card endpoints with dynamic ones.
# We replace routes by matching path — SDK routes were added above.

_AGENT_CARD_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"}


def _replace_routes():
    """Replace SDK agent card routes with dynamic versions that read request headers."""

    async def dynamic_agent_card(request: Request):
        card = _create_dynamic_agent_card(request)
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
app.include_router(health_router)
app.include_router(health_debug_router)

# ---------------------------------------------------------------------------
# Middleware stack (via add_middleware — outermost = last registered):
#   1. CORSMiddleware (outermost — adds CORS headers to all responses)
#   2. UnifiedAuthMiddleware (extracts auth token, sets scope["state"]["auth_context"])
# ---------------------------------------------------------------------------

app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(RestCompatMiddleware)

_cors_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Admin UI — mount Flask admin via WSGIMiddleware
# ---------------------------------------------------------------------------

flask_admin_app = create_app()
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

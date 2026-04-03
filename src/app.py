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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastmcp.utilities.lifespan import combine_lifespans

from src.core.main import mcp

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
    app.mount("/admin", admin_wsgi)  # type: ignore[arg-type]
    app.mount("/", admin_wsgi)  # type: ignore[arg-type]


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """FastAPI application lifespan — startup and shutdown hooks."""
    _install_admin_mounts()
    logger.info("FastAPI application starting up")
    yield
    logger.info("FastAPI application shutting down")


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

from src.core.exceptions import AdCPError  # noqa: E402


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    """Convert AdCP exceptions to structured JSON error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# ---------------------------------------------------------------------------
# A2A Integration — add routes directly to the FastAPI app (not as sub-app)
# so middleware and scope["state"] propagate correctly within the same ASGI app.
# ---------------------------------------------------------------------------

from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication  # noqa: E402
from starlette.routing import Route  # noqa: E402

from src.a2a_server.adcp_a2a_server import (  # noqa: E402
    AdCPRequestHandler,
    create_agent_card,
)
from src.a2a_server.context_builder import AdCPCallContextBuilder  # noqa: E402
from src.core.domain_config import get_a2a_server_url, get_sales_agent_domain  # noqa: E402

# Create the A2A application and add routes
_agent_card = create_agent_card()
_request_handler = AdCPRequestHandler()

a2a_app = A2AStarletteApplication(
    agent_card=_agent_card,
    http_handler=_request_handler,
    context_builder=AdCPCallContextBuilder(),
)

# Add A2A SDK routes directly to the FastAPI app.
# This gives us /a2a (JSON-RPC), /.well-known/agent-card.json, /agent.json
a2a_app.add_routes_to_app(
    app,
    agent_card_url="/.well-known/agent-card.json",
    rpc_url="/a2a",
    extended_agent_card_url="/agent.json",
)
logger.info("A2A routes added: /a2a, /.well-known/agent-card.json, /agent.json")


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


from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive

_VALID_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*(\:\d{1,5})?$"
)


def _is_valid_hostname(value: str) -> bool:
    """Validate that a string is a safe hostname (with optional port). Rejects path traversal and injection chars."""
    return bool(value) and len(value) <= 253 and _VALID_HOSTNAME_RE.match(value) is not None


def _create_dynamic_agent_card(request: Request):
    """Create agent card with tenant-specific URL from request headers."""

    def get_protocol(hostname: str) -> str:
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

    dynamic_card = _agent_card.model_copy()
    dynamic_card.url = server_url
    return dynamic_card


# Override the SDK's static agent card endpoints with dynamic ones.
# We replace routes by matching path — SDK routes were added above.

_AGENT_CARD_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"}


def _replace_routes():
    """Replace SDK agent card routes with dynamic versions that read request headers."""

    async def dynamic_agent_card(request: Request):
        card = _create_dynamic_agent_card(request)
        return JSONResponse(card.model_dump(mode="json"))

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

from src.routes.api_v1 import router as api_v1_router  # noqa: E402
from src.routes.health import debug_router as health_debug_router  # noqa: E402
from src.routes.health import router as health_router  # noqa: E402

app.include_router(api_v1_router)
app.include_router(health_router)
app.include_router(health_debug_router)

# ---------------------------------------------------------------------------
# Middleware stack (via add_middleware — outermost = last registered):
#   1. CORSMiddleware (outermost — adds CORS headers to all responses)
#   2. UnifiedAuthMiddleware (extracts auth token, sets scope["state"]["auth_context"])
# ---------------------------------------------------------------------------

from src.core.auth_middleware import UnifiedAuthMiddleware  # noqa: E402
from src.routes.rest_compat_middleware import RestCompatMiddleware  # noqa: E402

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

from a2wsgi import WSGIMiddleware  # noqa: E402

from src.admin.app import create_app  # noqa: E402

flask_admin_app = create_app()
admin_wsgi = WSGIMiddleware(flask_admin_app)


# ---------------------------------------------------------------------------
# Landing page routes
# ---------------------------------------------------------------------------

from fastapi.responses import HTMLResponse  # noqa: E402

from src.core.domain_routing import route_landing_page  # noqa: E402
from src.landing import generate_tenant_landing_page  # noqa: E402
from src.landing.landing_page import generate_fallback_landing_page  # noqa: E402


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

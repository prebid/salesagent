"""Central FastAPI application.

Mounts all sub-applications (MCP, A2A, Admin) into a single process.
Replaces the previous multi-process architecture where MCP, A2A, and Admin
ran as separate processes behind nginx.
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans

from src.core.main import mcp

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """FastAPI application lifespan — startup and shutdown hooks."""
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
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)

# Mount MCP at /mcp
app.mount("/mcp", mcp_app)


# ---------------------------------------------------------------------------
# A2A Integration — add routes directly to the FastAPI app (not as sub-app)
# so ContextVars propagate correctly within the same ASGI scope.
# ---------------------------------------------------------------------------

from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication  # noqa: E402
from starlette.routing import Route  # noqa: E402

from src.a2a_server.adcp_a2a_server import (  # noqa: E402
    AdCPRequestHandler,
    _request_auth_token,
    _request_headers,
    create_agent_card,
)
from src.core.domain_config import get_a2a_server_url, get_sales_agent_domain  # noqa: E402

# Create the A2A application and add routes
_agent_card = create_agent_card()
_request_handler = AdCPRequestHandler()

a2a_app = A2AStarletteApplication(
    agent_card=_agent_card,
    http_handler=_request_handler,
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


# ---------------------------------------------------------------------------
# Dynamic agent card endpoints — override SDK defaults to support
# tenant-specific URLs based on request headers.
# ---------------------------------------------------------------------------


def _get_header_case_insensitive(headers, header_name: str) -> str | None:
    """Get header value with case-insensitive lookup."""
    for key, value in headers.items():
        if key.lower() == header_name.lower():
            return value
    return None


def _create_dynamic_agent_card(request: Request):
    """Create agent card with tenant-specific URL from request headers."""

    def get_protocol(hostname: str) -> str:
        return "http" if hostname.startswith("localhost") or hostname.startswith("127.0.0.1") else "https"

    apx_incoming_host = _get_header_case_insensitive(request.headers, "Apx-Incoming-Host")
    if apx_incoming_host:
        protocol = get_protocol(apx_incoming_host)
        server_url = f"{protocol}://{apx_incoming_host}/a2a"
    else:
        host = _get_header_case_insensitive(request.headers, "Host") or ""
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

    new_routes = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in _AGENT_CARD_PATHS:
            new_routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))
        else:
            new_routes.append(route)
    app.router.routes = new_routes


_replace_routes()

# Add /agent.json endpoint (used by some A2A clients for extended agent card)


async def _agent_json_endpoint(request: Request):
    card = _create_dynamic_agent_card(request)
    return JSONResponse(card.model_dump(mode="json"))


app.router.routes.append(Route("/agent.json", _agent_json_endpoint, methods=["GET", "OPTIONS"]))


# ---------------------------------------------------------------------------
# A2A Middleware — auth and messageId compatibility
# ---------------------------------------------------------------------------


@app.middleware("http")
async def a2a_auth_middleware(request: Request, call_next):
    """Extract Bearer token and set authentication context for A2A requests.

    Accepts authentication via either:
    - Authorization: Bearer <token> (standard A2A/HTTP)
    - x-adcp-auth: <token> (AdCP convention, for compatibility with MCP)
    """
    if request.url.path in ["/a2a", "/a2a/"] and request.method == "POST":
        token = None
        auth_source = None

        for key, value in request.headers.items():
            if key.lower() == "authorization":
                auth_header = value.strip()
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    auth_source = "Authorization"
                    break
            elif key.lower() == "x-adcp-auth":
                token = value.strip()
                auth_source = "x-adcp-auth"

        if token:
            _request_auth_token.set(token)
            _request_headers.set(dict(request.headers))
            logger.info(f"Extracted token from {auth_source} header for A2A request: {token[:10]}...")
        else:
            logger.warning(
                f"A2A request to {request.url.path} missing authentication "
                "(checked Authorization and x-adcp-auth headers)"
            )
            _request_auth_token.set(None)
            _request_headers.set(dict(request.headers))

    response = await call_next(request)

    # Clean up context variables
    _request_auth_token.set(None)
    _request_headers.set(None)

    return response


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

        request = StarletteRequest(request.scope, receive=lambda: {"type": "http.request", "body": body})

    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Health and debug routes
# ---------------------------------------------------------------------------

from src.routes.health import router as health_router  # noqa: E402

app.include_router(health_router)

# ---------------------------------------------------------------------------
# CORS — allow all origins (nginx handles production restrictions)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Admin UI — mount Flask admin via WSGIMiddleware
# ---------------------------------------------------------------------------

from starlette.middleware.wsgi import WSGIMiddleware  # noqa: E402

from src.admin.app import create_app  # noqa: E402

flask_admin_app, _ = create_app()
admin_wsgi = WSGIMiddleware(flask_admin_app)

# Mount Flask admin at all paths it handles.
# Order matters: specific routes before catch-all.
_ADMIN_PATHS = ["/admin", "/static", "/auth", "/api", "/callback", "/logout", "/login", "/signup", "/test"]

for _path in _ADMIN_PATHS:
    app.mount(_path, admin_wsgi)

# Tenant-specific admin: /tenant/{tenant_id}/admin/...
app.mount("/tenant", admin_wsgi)


# ---------------------------------------------------------------------------
# Landing page routes
# ---------------------------------------------------------------------------

from fastapi.responses import HTMLResponse  # noqa: E402

from src.core.domain_routing import route_landing_page  # noqa: E402
from src.landing import generate_tenant_landing_page  # noqa: E402
from src.landing.landing_page import generate_fallback_landing_page  # noqa: E402


async def _handle_landing_page(request: Request):
    """Common landing page logic for root and /landing routes."""
    result = route_landing_page(dict(request.headers))
    logger.info(
        f"[LANDING] Routing decision: type={result.type}, host={result.effective_host}, "
        f"tenant={'yes' if result.tenant else 'no'}"
    )

    if result.type in ("custom_domain", "subdomain") and result.tenant:
        try:
            html_content = generate_tenant_landing_page(result.tenant, result.effective_host)
            return HTMLResponse(content=html_content)
        except Exception as e:
            logger.error(f"Error generating landing page: {e}", exc_info=True)
            return HTMLResponse(
                content=generate_fallback_landing_page(
                    f"Error generating landing page for {result.tenant.get('name', 'tenant')}"
                )
            )

    return HTMLResponse(content=generate_fallback_landing_page("No tenant found"))


# NOTE: These landing routes must be added BEFORE the /admin mount catch-all
# so FastAPI matches them first. We insert at position 0 (before mounts).

app.router.routes.insert(0, Route("/", _handle_landing_page, methods=["GET"]))
app.router.routes.insert(1, Route("/landing", _handle_landing_page, methods=["GET"]))

logger.info("FastAPI app created: MCP at /mcp, A2A at /a2a, Admin at /admin")

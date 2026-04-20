"""External-domain -> tenant-subdomain redirect as pure ASGI middleware.

Replaces Flask's ``@app.before_request redirect_external_domain_admin`` at
``src/admin/app.py:211-269``. Canonical spec:
``flask-to-fastapi-foundation-modules.md §11.8``.

LOGIC:

1. Only acts on ``/admin/*`` + ``/tenant/*`` requests. All other paths pass
   through (D1 2026-04-16 — ``/tenant/{tenant_id}/...`` is the canonical
   admin mount; operator bookmarks at ``/admin/*`` still hit this gate).
2. Looks for ``Apx-Incoming-Host`` header (set by the Approximated edge
   proxy when the request originated from a publisher's custom domain).
3. If the Apx host IS a sales-agent subdomain, pass through (no redirect).
4. If the Apx host is an EXTERNAL domain, look up the tenant by
   ``virtual_host``, then 307-redirect to the tenant's subdomain.
5. If no tenant found, tenant has no subdomain, or the lookup errors:
   pass through (let the admin UI show whatever error page it shows).
   Availability guard — middleware is NOT a hard DB dependency.

MIDDLEWARE ORDER:

MUST run BEFORE ``CSRFOriginMiddleware`` — external-domain POSTs are
307-redirected to the canonical subdomain BEFORE CSRF can reject the
request for a missing / mismatched Origin header. This is Critical
Invariant #5 (notes/CLAUDE.md).

MUST run BEFORE ``UnifiedAuthMiddleware`` — redirected requests never see
auth resolution; ``request.state.auth_context`` is not available to this
middleware.

STATUS CODE — 307 (NOT 302):

Preserves the HTTP method and request body across the redirect. 302 would
downgrade POST to GET in legacy user-agents and drop the body, which
breaks form-submit flows across the custom-domain boundary. Canonical §11.8.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-07``
and Critical Invariant #5.

Note on class name: §11.8 canonical block labels this
``ExternalDomainRedirectMiddleware``, but every integration reference in
§11.7.C / §11.36 / the ``MIDDLEWARE_STACK_VERSION`` assertion uses
``ApproximatedExternalDomainMiddleware``. The public name is the latter.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.core.config_loader import get_tenant_by_virtual_host
from src.core.domain_config import get_tenant_url, is_sales_agent_domain

logger = logging.getLogger(__name__)


def _extract_header(scope: dict, name: str) -> str | None:
    """Extract a single header value from an ASGI scope (lowercase compare)."""
    target = name.lower().encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == target:
            return raw_value.decode("latin-1")
    return None


def _is_gated_path(path: str) -> bool:
    """Path gating — ``/admin`` / ``/admin/*`` / ``/tenant/*`` only.

    Starlette does NOT strip a "script name" like WSGI Flask did; we check the
    literal request path. ``/api/v1/*``, ``/mcp/*``, ``/a2a/*``, static assets,
    and every other route pass through untouched.
    """
    return path == "/admin" or path.startswith("/admin/") or path.startswith("/tenant/")


async def _respond_redirect(send: Any, url: str, status: int = 307) -> None:
    """Emit a minimal ASGI redirect response (no body, no side state)."""
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"location", url.encode("latin-1")),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class ApproximatedExternalDomainMiddleware:
    """Redirect ``/admin/*`` + ``/tenant/*`` from external domains to tenant subdomain.

    Fires ONLY on the gated path set. External-domain requests are
    307-redirected to the tenant's canonical subdomain (preserving method +
    body). Sales-agent subdomain requests and all other paths pass through
    without a DB hit.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if not _is_gated_path(path):
            await self.app(scope, receive, send)
            return

        apx_host = _extract_header(scope, "apx-incoming-host")
        if not apx_host:
            await self.app(scope, receive, send)
            return

        if is_sales_agent_domain(apx_host):
            # Subdomain request — normal routing.
            await self.app(scope, receive, send)
            return

        # External domain detected. Look up tenant.
        try:
            tenant = get_tenant_by_virtual_host(apx_host)
        except Exception:
            # Availability guard: DB down / bad config must not brick admin.
            logger.exception("Tenant lookup failed for virtual host %s", apx_host)
            await self.app(scope, receive, send)
            return

        if not tenant:
            logger.warning("No tenant for external domain %s", apx_host)
            await self.app(scope, receive, send)
            return

        subdomain = tenant.get("subdomain") if isinstance(tenant, dict) else getattr(tenant, "subdomain", None)
        if not subdomain:
            logger.warning("Tenant %s has no subdomain configured", tenant)
            await self.app(scope, receive, send)
            return

        query = scope.get("query_string", b"").decode("latin-1")
        full_path = f"{path}?{query}" if query else path

        if os.environ.get("PRODUCTION", "").lower() == "true":
            redirect_url = f"{get_tenant_url(subdomain)}{full_path}"
        else:
            port = os.environ.get("ADCP_SALES_PORT", "8080")
            redirect_url = f"http://{subdomain}.localhost:{port}{full_path}"

        logger.info(
            "Redirecting external domain %s%s to %s",
            apx_host,
            full_path,
            redirect_url,
        )
        await _respond_redirect(send, redirect_url, status=307)

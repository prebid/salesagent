"""STUB (L0-07 Red) — ApproximatedExternalDomainMiddleware scaffolding.

Green replaces with the real path-gated, 307-redirect middleware per
.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.8.

The stub imports the domain-config helpers into module namespace so tests
can patch ``src.admin.middleware.external_domain.is_sales_agent_domain``
etc. identically against Red and Green — the Red failure comes from the
stub's always-fires 302 behavior, not from a patch-target lookup error.
"""

from __future__ import annotations

from typing import Any

from src.core.config_loader import get_tenant_by_virtual_host  # noqa: F401
from src.core.domain_config import (  # noqa: F401
    get_tenant_url,
    is_sales_agent_domain,
)


class ApproximatedExternalDomainMiddleware:
    """Stub — Red behavior: always fires + hard-codes 302 (not 307) + no path gate.

    Tests assert path-gating (no-op on ``/api/*`` / ``/mcp/*``), 307 (not 302)
    redirect semantics, and POST body preservation. This stub redirects every
    request unconditionally with status 302 — every test fails semantically.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 302,  # wrong; Green uses 307
                "headers": [
                    (b"location", b"http://stub.invalid/"),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

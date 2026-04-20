"""``ServedByMiddleware`` — stamps ``X-Served-By: <stack>`` on every response.

At L0 Flask serves 100% of ``/admin/*`` traffic, so the middleware emits
``X-Served-By: flask``. L1a flips the emission per-request based on the
``ADCP_USE_FASTAPI_ADMIN`` feature flag (read via ``src.core.config``)
so the operator dashboard can watch the traffic split live.

Why this is load-bearing at L0:

- Makes "zero Flask traffic" (L2 pre-cut gate) verifiable via header
  analytics — the L1 bake window requires 48h of 100% fastapi hits
  before Flask may be deleted.
- Provides instant rollback observability: flipping the flag changes
  the response-header ratio within one request, so the traffic split
  is visible to any caller running ``curl -I /admin/`` or reading the
  Fly Proxy edge logs.

Scaffold-only at L0 per ``flask-to-fastapi-foundation-modules.md`` §11.36:
the module exists and tests can exercise it, but wiring into the
canonical middleware stack lands at L1a. The ``MIDDLEWARE_STACK_VERSION``
assertion is NOT bumped here.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-18``
and ``implementation-checklist.md §EP-1``, ``§EP-2``.

**Pattern (a) stub-first** — at L0 Red the middleware passes the request
through unchanged; semantic tests assert ``X-Served-By`` presence against
a response that lacks the header. L0 Green adds the header emission.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

HEADER_NAME: str = "x-served-by"


class ServedByMiddleware:
    """Pure-ASGI middleware that stamps ``X-Served-By`` on HTTP responses.

    Constructor takes an explicit ``stack_name`` so tests can stamp either
    ``flask`` or ``fastapi`` deterministically without mutating global state.
    L1a wires it with a per-request resolver that reads the feature flag.
    """

    def __init__(self, app: ASGIApp, stack_name: str = "flask") -> None:
        self.app = app
        self.stack_name = stack_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # L0 Red stub: pass through without touching headers.
        await self.app(scope, receive, send)


__all__ = ["HEADER_NAME", "ServedByMiddleware"]

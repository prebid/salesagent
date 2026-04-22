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
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from src.admin.middleware._ascgi_headers import build_header_wrapper

HEADER_NAME: str = "x-served-by"
_HEADER_BYTES: bytes = HEADER_NAME.encode("latin-1")


class ServedByMiddleware:
    """Pure-ASGI middleware that stamps ``X-Served-By`` on HTTP responses.

    Constructor takes an explicit ``stack_name`` so tests can stamp either
    ``flask`` or ``fastapi`` deterministically without mutating global state.
    L1a wires it with a per-request resolver that reads the feature flag.

    Non-HTTP scopes (lifespan, websocket) pass through unchanged — no
    response-header fabrication on messages that don't carry headers.
    """

    def __init__(self, app: ASGIApp, stack_name: str = "flask") -> None:
        self.app = app
        self.stack_name = stack_name
        self._value_bytes = stack_name.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Replace-mode: the outermost middleware wins so the observed header
        # reflects the actual serving stack even if an inner layer stamped
        # a prior value.
        wrapped_send = build_header_wrapper(
            send,
            to_set=[(_HEADER_BYTES, self._value_bytes)],
            mode="replace",
        )

        await self.app(scope, receive, wrapped_send)


__all__ = ["HEADER_NAME", "ServedByMiddleware"]

"""Inject ``WWW-Authenticate: Bearer`` on 401 responses missing the header.

RFC 6750 §3 requires every 401 from a Bearer-protected resource to carry a
``WWW-Authenticate: Bearer ...`` header so the buyer agent knows which auth
scheme to apply. The MCP leg's :class:`BearerTokenAuthMiddleware` in
``adcp.server.auth`` returns 401 without this header for missing/invalid
tokens — the A2A leg and :class:`SigningVerifyMiddleware` already emit it
correctly. Storyboard ``security_baseline/probe_unauth`` (universal phase)
asserts on header presence.

**This middleware is a workaround for an upstream library defect.** The
right fix lives in ``adcp/server/auth.py:411`` (``BearerTokenAuthMiddleware
._unauthenticated``) — filed at
``adcontextprotocol/adcp-client-python#712``. When that ships and we bump
``adcp``, this middleware becomes a no-op (the upstream 401 will already
carry ``WWW-Authenticate`` and the case-insensitive presence check below
will skip injection). Delete this module and its registration in
``core/main.py:_serve_kwargs`` in the same PR that bumps to the fixed
release.

Design:

* Wraps the ASGI ``send`` callable and inspects the ``http.response.start``
  message. Only acts when ``status == 401``; lifespan / websocket / 2xx /
  redirect / 5xx pass through untouched.
* No-op when the response already carries ``WWW-Authenticate`` (case-
  insensitive lookup) — the A2A and signing-verify paths win and their
  ``error="..."`` parameter survives. Idempotent: stacking the middleware
  twice is harmless.
* Inserts the bare ``Bearer`` challenge — no ``realm=`` (Bearer realms
  are advisory per RFC 6750 §3 and AdCP doesn't standardise one), no
  ``error=`` (the body already carries ``adcp_error`` for the buyer to
  read; ``WWW-Authenticate`` is the scheme advertisement, not the
  diagnostic). Buyers that branch on ``error="invalid_token"`` get the
  signal from the A2A and signing-verify paths where it's contextually
  correct.

Position in the ASGI stack: must run OUTSIDE every buyer-protocol middleware
that can emit 401 so it sees the response before it ships. In
``core.main._serve_kwargs`` it runs AFTER :class:`AdminWSGIMount` — admin
Flask paths (Google-OAuth-gated) short-circuit before this middleware sees
them, since a Bearer-scheme challenge is contextually wrong for those.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_WWW_AUTHENTICATE = b"www-authenticate"
_BEARER_CHALLENGE = b"Bearer"


class WWWAuthenticateMiddleware:
    """ASGI middleware that injects ``WWW-Authenticate: Bearer`` on 401
    responses missing the header (RFC 6750 §3 compliance).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        async def _send_wrapper(message: Message) -> None:
            if message.get("type") == "http.response.start" and message.get("status") == 401:
                headers = list(message.get("headers") or [])
                if not any(name.lower() == _WWW_AUTHENTICATE for name, _ in headers):
                    headers.append((_WWW_AUTHENTICATE, _BEARER_CHALLENGE))
                    message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, _send_wrapper)

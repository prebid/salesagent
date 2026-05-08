"""Audit-log when an inbound request carries two different bearer credentials.

When a buyer (or a misconfigured proxy) sends both ``Authorization: Bearer``
*and* ``x-adcp-auth`` with different tokens, that's a credential-confusion
signal worth surfacing — possibly a proxy stripping/injecting headers, a
buyer mid-migration who left both clients live, or an attacker attempting
to smuggle a different token through middleware that only checks one
header.

This middleware ran inside the deleted ``BearerToAdcpAuthMiddleware`` shim
(removed in #194 when adcp 4.5.0 landed per-leg config). The new SDK
middleware on each leg only reads its configured header, so the
divergence signal disappears unless we restore it here.

Behavior:

* Buyer-protocol HTTP requests only — admin paths short-circuit ahead of
  this middleware via :class:`AdminWSGIMount`. Lifespan / websocket /
  CORS preflight pass through untouched.
* Header values are NEVER logged — only the *fact* that two different
  tokens were present. A SOC-friendly fingerprint is emitted instead
  (8-byte SHA-256 prefix per credential) so two different requests with
  the same divergence pattern correlate without leaking the token.
* No request mutation. The downstream auth chain decides which header
  wins; this middleware only audits.

Position in the ASGI stack:
``AdminWSGIMount`` → ``DualCredentialAuditMiddleware`` → SpecDefaults →
agent-card rewrite → SDK auth → handler. Sitting outside the SDK auth
chain means the audit fires regardless of which leg the request lands
on.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_X_ADCP_AUTH = b"x-adcp-auth"
_AUTHORIZATION = b"authorization"
_BEARER_PREFIX = b"bearer "


def _fingerprint(token: bytes) -> str:
    """Return an 8-hex-char fingerprint of ``token`` for log correlation.

    SHA-256 truncated to 8 hex chars: ample collision resistance for an
    audit signal, narrow enough that the original token can't be
    brute-forced from the log line.
    """
    return hashlib.sha256(token).hexdigest()[:8]


class DualCredentialAuditMiddleware:
    """ASGI middleware that logs WARNING when two different bearer tokens
    arrive in the same request via ``Authorization: Bearer`` *and*
    ``x-adcp-auth``.

    No-op when at most one credential header is present, or when both
    are present with identical post-strip values (the common
    "buyer sends both during cutover" case is benign and we don't
    want to spam the audit log).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        adcp_token: bytes | None = None
        bearer_token: bytes | None = None

        for name, value in scope.get("headers") or ():
            if name == _X_ADCP_AUTH and adcp_token is None:
                stripped = value.strip()
                if stripped:
                    adcp_token = stripped
            elif name == _AUTHORIZATION and bearer_token is None:
                if value.lower().startswith(_BEARER_PREFIX):
                    candidate = value[len(_BEARER_PREFIX) :].strip()
                    if candidate:
                        bearer_token = candidate

        if adcp_token is not None and bearer_token is not None and adcp_token != bearer_token:
            logger.warning(
                "dual_credential_audit: inbound request carries different "
                "tokens in x-adcp-auth and Authorization: Bearer "
                "(x_adcp_fp=%s, bearer_fp=%s, path=%s, method=%s). "
                "Possible proxy misconfig, buyer cutover, or credential "
                "smuggling — investigate.",
                _fingerprint(adcp_token),
                _fingerprint(bearer_token),
                scope.get("path", ""),
                scope.get("method", ""),
            )

        await self._app(scope, receive, send)

"""HTTP 401 OAuth challenges for protected MCP tool calls."""

from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.auth_utils import get_principal_from_token
from src.core.exceptions import AUTH_REQUIRED_SUGGESTION
from src.core.mcp_auth_middleware import AUTH_OPTIONAL_TOOLS
from src.core.oauth_service import get_mcp_oauth_issuer


class MCPOAuthChallengeMiddleware:
    """Return MCP OAuth discovery challenges before FastMCP handles missing auth."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        token_status = _authenticate_bearer_token(scope)
        if token_status == "valid":
            await self.app(scope, receive, send)
            return
        if token_status == "invalid":
            await _send_oauth_challenge(send, error="invalid_token")
            return

        if _has_legacy_auth_header(scope):
            await self.app(scope, receive, send)
            return

        body, replay_receive = await _buffer_request_body(receive)
        if _is_unauthenticated_protected_tool_call(body):
            await _send_oauth_challenge(send)
            return

        await self.app(scope, replay_receive, send)


async def _buffer_request_body(receive: Receive) -> tuple[bytes, Receive]:
    messages: list[Message] = []
    body_parts: list[bytes] = []

    while True:
        message = await receive()
        messages.append(message)
        if message["type"] == "http.request":
            body_parts.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        else:
            break

    async def replay_receive() -> Message:
        if messages:
            return messages.pop(0)
        return await receive()

    return b"".join(body_parts), replay_receive


def _is_unauthenticated_protected_tool_call(body: bytes) -> bool:
    tool_name = _extract_tool_name(body)
    return tool_name is not None and tool_name not in AUTH_OPTIONAL_TOOLS


def _authenticate_bearer_token(scope: Scope) -> str:
    authorization = _headers(scope).get("authorization") or ""
    if not authorization.lower().startswith("bearer "):
        return "missing"
    token = authorization[7:].strip()
    if not token:
        return "missing"

    principal_id, _tenant = get_principal_from_token(token)
    return "valid" if principal_id else "invalid"


def _has_legacy_auth_header(scope: Scope) -> bool:
    return bool(_headers(scope).get("x-adcp-auth"))


def _headers(scope: Scope) -> dict[str, str]:
    return {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope.get("headers", [])}


def _extract_tool_name(body: bytes) -> str | None:
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


async def _send_oauth_challenge(send: Send, *, error: str | None = None) -> None:
    metadata_url = f"{get_mcp_oauth_issuer()}/.well-known/oauth-protected-resource"
    body = json.dumps(
        {
            "errors": [
                {
                    "code": "AUTH_REQUIRED",
                    "message": "Authentication required",
                    "recovery": "correctable",
                    "suggestion": AUTH_REQUIRED_SUGGESTION,
                }
            ]
        }
    ).encode("utf-8")
    authenticate = f'Bearer resource_metadata="{metadata_url}"'
    if error:
        authenticate += f', error="{error}"'
    headers = [
        (b"content-type", b"application/json"),
        (b"www-authenticate", authenticate.encode("latin-1")),
    ]
    await send({"type": "http.response.start", "status": 401, "headers": headers})
    await send({"type": "http.response.body", "body": body})

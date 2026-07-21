"""FastMCP middleware for centralized MCP identity resolution.

Resolves identity once per tool call and stores it on FastMCP context state.
Tool functions read the pre-resolved identity via ctx.get_state('identity')
instead of calling resolve_identity_from_context() directly.
"""

import logging

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from src.core.exceptions import AdCPAuthenticationError, AdCPError
from src.core.tool_error_logging import _reject_at_mcp_boundary
from src.core.transport_helpers import resolve_identity_from_context

logger = logging.getLogger(__name__)

# Discovery tools that work without authentication.
# All other tools require a valid auth token.
AUTH_OPTIONAL_TOOLS = frozenset(
    {
        "get_adcp_capabilities",
        "get_products",
        "list_accounts",
        "list_creative_formats",
        "list_authorized_properties",
    }
)


class MCPAuthMiddleware(Middleware):
    """Resolve identity before tool execution and store on context state.

    After this middleware runs, tools read identity via:
        identity = ctx.get_state('identity')
        context_id = ctx.get_state('context_id')  # may be None
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        tool_name = context.message.name
        require_auth = tool_name not in AUTH_OPTIONAL_TOOLS
        application_context = (context.message.arguments or {}).get("context")

        # AUTH before VERSION (cross-transport parity, #1546). resolve_identity
        # raises for an INVALID token; a MISSING token returns a principal-less
        # identity, so reject that here too — otherwise an auth-required tool
        # falls through to RequestCompatMiddleware's version check and a bad pin
        # is rejected with VERSION_UNSUPPORTED (disclosing supported_versions)
        # before the auth gap is reported. A raw AdCPError raised from a
        # middleware bypasses the tool wrapper's with_error_logging, so it must
        # be translated to the two-layer envelope HERE (same as
        # RequestCompatMiddleware's version-error path) — otherwise the wire
        # error carries no code/recovery. A2A enforces the same order in
        # on_message_send; REST via the version-after-auth deps.
        identity = None
        try:
            identity = resolve_identity_from_context(
                context.fastmcp_context,
                require_valid_token=require_auth,
            )
            if require_auth and (identity is None or not identity.principal_id):
                raise AdCPAuthenticationError("Authentication required")
        except AdCPError as exc:
            _reject_at_mcp_boundary(tool_name, exc, identity, context=application_context)

        if context.fastmcp_context:
            await context.fastmcp_context.set_state("identity", identity, serializable=False)

            # Raw wire arguments, captured BEFORE RequestCompatMiddleware
            # normalizes them — the idempotency payload-hash input (AdCP defines
            # payload equivalence over the request as the buyer sent it).
            if context.message.arguments is not None:
                await context.fastmcp_context.set_state(
                    "raw_wire_payload", dict(context.message.arguments), serializable=False
                )

            # Extract x-context-id from HTTP headers for tools that need it
            try:
                headers = get_http_headers(include_all=True) or {}
                ctx_id = headers.get("x-context-id")
                if ctx_id:
                    await context.fastmcp_context.set_state("context_id", ctx_id, serializable=False)
            except Exception:
                logger.debug("Could not set context_id state", exc_info=True)

        return await call_next(context)

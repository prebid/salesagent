"""FastMCP middleware for AdCP backward-compatibility normalization.

Translates deprecated field names and strips unknown fields in tool
arguments before FastMCP's TypeAdapter validates the function signature.
Runs after MCPAuthMiddleware.
"""

import logging

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from src.core.request_compat import normalize_request_params, strip_unknown_params

logger = logging.getLogger(__name__)


class RequestCompatMiddleware(Middleware):
    """Normalize deprecated fields and strip unknowns before tool dispatch.

    Two-stage pipeline:
    1. Translate deprecated field names via normalize_request_params()
    2. Strip fields not in the tool's JSON Schema via strip_unknown_params()

    When either stage modifies arguments, replaces the context message so
    the TypeAdapter only sees valid, current-version field names.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        arguments = context.message.arguments
        if not arguments:
            return await call_next(context)

        tool_name = context.message.name
        normalized = dict(arguments)
        modified = False

        # Step 1: Translate deprecated fields
        compat_result = normalize_request_params(tool_name, normalized)
        normalized = compat_result.params
        if compat_result.translations_applied:
            modified = True

        # Step 2: Strip unknown fields (schema-aware)
        known_params = await self._get_known_params(context, tool_name)
        if known_params is not None:
            normalized, stripped = strip_unknown_params(normalized, known_params)
            if stripped:
                modified = True
                logger.warning(
                    "Stripped unknown fields from %s: %s",
                    tool_name,
                    ", ".join(stripped),
                )

        if modified:
            new_message = CallToolRequestParams(
                name=tool_name,
                arguments=normalized,
            )
            context = context.copy(message=new_message)

        return await call_next(context)

    async def _get_known_params(
        self,
        context: MiddlewareContext,
        tool_name: str,
    ) -> set[str] | None:
        """Look up tool's declared parameter names from its JSON Schema.

        Returns None if lookup fails (defensive -- skip stripping).
        """
        try:
            fastmcp_ctx = context.fastmcp_context
            if fastmcp_ctx is None:
                return None
            server = fastmcp_ctx.fastmcp
            tool = await server.get_tool(tool_name)
            if tool is None:
                return None
            return set(tool.parameters.get("properties", {}).keys())
        except Exception:
            logger.debug("Could not look up params for %s, skipping strip", tool_name)
            return None

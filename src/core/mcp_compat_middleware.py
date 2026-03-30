"""FastMCP middleware for AdCP backward-compatibility normalization.

Translates deprecated field names in tool arguments before FastMCP's
TypeAdapter validates the function signature. Runs after MCPAuthMiddleware.
"""

import logging

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from src.core.request_compat import normalize_request_params

logger = logging.getLogger(__name__)


class RequestCompatMiddleware(Middleware):
    """Normalize deprecated fields before tool dispatch.

    Calls normalize_request_params() on tool arguments. When translations
    are applied, replaces the context message so the TypeAdapter only sees
    current-version field names.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> ToolResult:
        arguments = context.message.arguments
        if not arguments:
            return await call_next(context)

        result = normalize_request_params(
            context.message.name,
            dict(arguments),
        )

        if result.translations_applied:
            new_message = CallToolRequestParams(
                name=context.message.name,
                arguments=result.params,
            )
            context = context.copy(message=new_message)

        return await call_next(context)

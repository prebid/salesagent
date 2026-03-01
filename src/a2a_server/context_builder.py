"""A2A CallContextBuilder that bridges UnifiedAuthMiddleware to SDK ServerCallContext.

Reads AuthContext from request.state (set by UnifiedAuthMiddleware via scope["state"])
and populates ServerCallContext.state["auth_context"] for use by handler methods.
"""

from __future__ import annotations

import logging

from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder
from a2a.server.context import ServerCallContext
from starlette.requests import Request

from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext

logger = logging.getLogger(__name__)


class AdCPCallContextBuilder(CallContextBuilder):
    """Builds ServerCallContext from request.state.auth_context.

    UnifiedAuthMiddleware sets scope["state"]["auth_context"] which backs
    request.state.auth_context in Starlette. This builder reads it and
    places it into ServerCallContext.state for handler methods.
    """

    def build(self, request: Request) -> ServerCallContext:
        """Build ServerCallContext from a Starlette Request.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            ServerCallContext with auth_context in state.
        """
        auth_ctx: AuthContext | None = getattr(getattr(request, "state", None), AUTH_CONTEXT_STATE_KEY, None)
        if auth_ctx is None:
            auth_ctx = AuthContext.unauthenticated()

        state: dict = {AUTH_CONTEXT_STATE_KEY: auth_ctx}

        # Also populate headers for SDK extensions that may inspect them
        headers = getattr(request, "headers", None)
        if headers is not None:
            state["headers"] = dict(headers)

        # Handle SDK extension headers
        from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions

        requested_extensions: set[str] = set()
        if headers is not None and hasattr(headers, "getlist"):
            requested_extensions = get_requested_extensions(headers.getlist(HTTP_EXTENSION_HEADER))

        return ServerCallContext(
            state=state,
            requested_extensions=requested_extensions,
        )

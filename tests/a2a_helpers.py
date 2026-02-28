"""Shared test helpers for A2A handler tests.

Provides make_a2a_context() to build a ServerCallContext the same way
AdCPCallContextBuilder.build() does in production, but without needing
a Starlette request object.
"""

from a2a.server.context import ServerCallContext

from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext


def make_a2a_context(
    auth_token: str | None = None,
    headers: dict[str, str] | None = None,
) -> ServerCallContext:
    """Build a ServerCallContext for A2A handler tests.

    Mirrors AdCPCallContextBuilder.build() — populates state["auth_context"]
    with an AuthContext containing the given token and headers.

    Args:
        auth_token: Bearer token (None for unauthenticated).
        headers: HTTP headers dict (e.g., {"host": "acme.example.com"}).

    Returns:
        ServerCallContext ready to pass to handler.on_message_send(params, context=ctx).
    """
    auth_ctx = AuthContext(auth_token=auth_token, headers=headers or {})
    return ServerCallContext(state={AUTH_CONTEXT_STATE_KEY: auth_ctx})

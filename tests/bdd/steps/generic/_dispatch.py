"""Shared dispatch helper for BDD domain step definitions.

Provides a single implementation of the transport-aware dispatch pattern
used across UC-004, UC-011, and future domain step files.
"""

from __future__ import annotations

from typing import Any

_SENTINEL = object()


def dispatch_request(ctx: dict, *, identity: Any = _SENTINEL, **kwargs: Any) -> None:
    """Dispatch a request through ctx['transport'] via call_via, or direct call_impl.

    Stores result in ctx["response"] on success, ctx["error"] on failure.
    If ctx["transport"] is a Transport enum, uses call_via directly.
    If it's a string, maps to Transport enum first.
    If absent, falls back to call_impl.

    The ``identity`` kwarg overrides the default identity for multi-agent
    and no-auth scenarios. When provided, it flows through to call_via
    (which uses kwargs.setdefault, so an explicit identity won't be clobbered).
    Use ``identity=None`` for no-auth scenarios.
    """
    if identity is not _SENTINEL:
        kwargs["identity"] = identity

    transport = ctx.get("transport")
    env = ctx["env"]
    if transport is not None:
        from tests.harness.transport import Transport

        if isinstance(transport, Transport):
            pass  # Already a Transport enum — use as-is
        elif isinstance(transport, str):
            transport_map = {
                "MCP": Transport.MCP,
                "mcp": Transport.MCP,
                "A2A": Transport.A2A,
                "a2a": Transport.A2A,
                "REST": Transport.REST,
                "rest": Transport.REST,
                "IMPL": Transport.IMPL,
                "impl": Transport.IMPL,
            }
            transport = transport_map.get(transport, Transport.IMPL)
        try:
            result = env.call_via(transport, **kwargs)
            if result.is_error:
                ctx["error"] = result.error
            else:
                ctx["response"] = result.payload
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            ctx["response"] = env.call_impl(**kwargs)
        except Exception as exc:
            ctx["error"] = exc

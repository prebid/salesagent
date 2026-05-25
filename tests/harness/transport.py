"""Transport enum and TransportResult for multi-transport behavioral tests.

Defines the dispatch transports (IMPL, MCP) and a frozen result
container that separates transport-specific envelope from shared payload.

A2A is no longer dispatched in-process — the framework's
``adcp.server.serve(transport="a2a")`` owns that surface.

REST is no longer a transport — the legacy FastAPI app was deleted in
the kill-nginx cutover. Tools are now reachable only through MCP and
A2A (the two protocols AdCP defines), plus the IMPL shortcut for
business-logic tests that don't need the transport boundary.

Usage::

    result = env.call_via(Transport.MCP, creatives=[...])
    assert result.is_success
    assert result.payload.creatives[0].action == CreativeAction.created
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel


class Transport(str, Enum):
    """Dispatch transports for behavioral tests."""

    IMPL = "impl"  # Direct _impl() call
    MCP = "mcp"  # httpx ASGITransport → FastMCP wrapper → _impl()


# Maps Transport → ResolvedIdentity.protocol value
TRANSPORT_PROTOCOL: dict[Transport, str] = {
    Transport.IMPL: "mcp",  # _impl doesn't inspect protocol; keep default
    Transport.MCP: "mcp",
}


@dataclass(frozen=True)
class TransportResult:
    """Normalized result from any transport dispatch.

    Attributes:
        payload: Pydantic response model (shared assertions target this).
        envelope: Transport-specific metadata (HTTP status, ToolResult, etc.).
        error: Exception raised during dispatch, if any.
        raw_response: Unprocessed transport response (httpx.Response, ToolResult, etc.).
    """

    payload: BaseModel | None = None
    envelope: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    raw_response: Any = None

    @property
    def is_success(self) -> bool:
        return self.error is None and self.payload is not None

    @property
    def is_error(self) -> bool:
        return self.error is not None

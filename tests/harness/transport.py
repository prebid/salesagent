"""Transport enum and TransportResult for multi-transport behavioral tests.

Defines the four dispatch transports (IMPL, A2A, REST, MCP) and a frozen
result container that separates transport-specific envelope from shared payload.

Usage::

    result = env.call_via(Transport.REST, creatives=[...])
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
    A2A = "a2a"  # _raw() A2A wrapper
    REST = "rest"  # FastAPI TestClient → route → _raw() → _impl()
    MCP = "mcp"  # Mock Context → MCP wrapper → _impl()
    E2E_REST = "e2e_rest"  # Real HTTP via httpx → nginx → server
    E2E_MCP = "e2e_mcp"  # Real MCP via httpx → nginx → server (placeholder)
    E2E_A2A = "e2e_a2a"  # Real A2A via httpx → nginx → server (placeholder)


# Maps Transport → ResolvedIdentity.protocol value
TRANSPORT_PROTOCOL: dict[Transport, str] = {
    Transport.IMPL: "mcp",  # _impl doesn't inspect protocol; keep default
    Transport.A2A: "a2a",
    Transport.REST: "rest",
    Transport.MCP: "mcp",
    Transport.E2E_REST: "rest",
    Transport.E2E_MCP: "mcp",
    Transport.E2E_A2A: "a2a",
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

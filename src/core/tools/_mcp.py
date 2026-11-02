"""Shared MCP transport-wrapper helper for building ``ToolResult`` responses."""

from __future__ import annotations

from adcp.types import ProtocolEnvelope
from fastmcp.tools.tool import ToolResult

from src.core.tools._wire import to_wire


def mcp_result(response: ProtocolEnvelope, content: str | None = None) -> ToolResult:
    """Build a ``ToolResult`` with a spec-compliant ``structured_content``.

    ``structured_content`` must be a plain dict via ``to_wire``: FastMCP's
    ``ToolResult`` serializes non-dict ``structured_content`` through pydantic_core's
    generic JSON conversion, which does not apply ``AdCPBaseModel``'s
    ``exclude_none=True`` ``model_dump`` default -- so protocol/spec-optional fields
    the model leaves unset would otherwise serialize as invalid wire ``null`` instead
    of being omitted. The model's own wrap serializer runs on both paths; only that
    default is at stake.

    The parameter is bound to ``ProtocolEnvelope``, not ``pydantic.BaseModel``, because two
    contracts ride on it: that class subclasses ``AdCPBaseModel``, whose ``exclude_none=True``
    default this helper exists to preserve, and it carries the envelope fields ``content``
    reads below. A plain pydantic model routed through here would type-check, re-leak the
    nulls, have no ``message``, and still satisfy every "did it go through mcp_result?"
    structural check -- so the bound is where it has to be caught.
    """
    return ToolResult(
        content=content if content is not None else (getattr(response, "message", None) or ""),
        structured_content=to_wire(response),
    )

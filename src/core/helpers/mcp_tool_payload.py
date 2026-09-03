"""Read the JSON payload an MCP ``tools/call`` result carries.

fastmcp's ``CallToolResult`` may carry the response as ``structured_content``
(already-parsed JSON) or as legacy ``TextContent`` blocks in ``content`` whose
``.text`` is a JSON string. Every MCP tool call in this codebase reads a result
the same way — this is that one place (DRY), replacing what was duplicated
inline in ``preview_creative``/``build_creative`` and now also needed by the
guarded creative-format/signals fetch paths (GH #1802).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.core.utils.mcp_client import MCPCompatibilityError

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult


def extract_tool_payload(result: CallToolResult) -> dict[str, Any]:
    """Return the tool's JSON payload, or ``{}`` if neither shape is present.

    Raises ``MCPCompatibilityError`` when the extracted payload is not a JSON
    object — a creative agent returning a bare array, or a ``TextContent``
    block whose ``.text`` is not valid JSON at all, both mean the same thing:
    the agent didn't answer with the shape this call expects. Left
    unclassified, either surfaces as an ``AttributeError`` two frames later
    when a caller does ``payload.get(...)`` on a list, or a bare
    ``json.JSONDecodeError`` — neither is a typed AdCP failure a caller's
    seam-error mapper can classify.
    """
    structured = result.structured_content
    if structured:
        if not isinstance(structured, dict):
            raise MCPCompatibilityError(f"Expected a JSON object from the tool result, got {type(structured).__name__}")
        return structured

    # `text` stays a getattr: ContentBlock is a UNION (TextContent, ImageContent,
    # EmbeddedResource, ...) and only some members carry `.text`, so this probe is
    # about the MCP wire type, not about a signature that declined to say what it
    # takes. The two probes above were the latter, and are gone.
    for item in result.content or []:
        text = getattr(item, "text", None)
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MCPCompatibilityError(f"Tool result text content is not valid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise MCPCompatibilityError(
                    f"Expected a JSON object from the tool result, got {type(payload).__name__}"
                )
            return payload

    return {}

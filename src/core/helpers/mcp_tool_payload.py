"""Read the JSON payload an MCP ``tools/call`` result carries.

fastmcp's ``CallToolResult`` may carry the response as ``structured_content``
(already-parsed JSON) or as legacy ``TextContent`` blocks in ``content`` whose
``.text`` is a JSON string. Every MCP tool call in this codebase reads a result
the same way — this is that one place (DRY), replacing what was duplicated
inline in ``preview_creative``/``build_creative`` and now also needed by the
guarded creative-format/signals fetch paths (salesagent-4n88).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.core.utils.mcp_client import MCPCompatibilityError

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult


def _require_json_object(payload: Any) -> dict[str, Any]:
    """Return *payload* when it is a JSON object; refuse it otherwise.

    Both non-object shapes — ``structured_content`` carrying a JSON array, and a
    ``TextContent`` block whose text parses to something that is not an object —
    are the same fault (the agent didn't answer with the shape this call
    expects), so they are refused in one place rather than at each branch.

    ``MCPCompatibilityError`` authors no buyer-facing text (ADR-010), and its
    ``AdapterFailureDetails`` has no field for "what shape the agent sent" — its
    axes are which entity the call was about, what the upstream call did, and
    per-item problems. Nothing this function is handed fills one of those (the
    agent URL lives at the call site, not on ``CallToolResult``), so ``details``
    stays unset rather than being filled with a field that means something else.
    """
    if not isinstance(payload, dict):
        raise MCPCompatibilityError()
    return payload


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
        # ``CallToolResult.structured_content`` is ANNOTATED ``dict | None``, so
        # this check is about the value fastmcp actually hands us across
        # versions, not about the annotation — which is why it is a runtime
        # refusal and not an assertion mypy can narrow away.
        return _require_json_object(structured)

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
                raise MCPCompatibilityError(internal_detail=exc) from exc
            return _require_json_object(payload)

    return {}

"""Shared MCP wire-serialization helper for tool wrappers.

FastMCP's ``ToolResult.__init__`` (``fastmcp/tools/base.py``) serializes a
non-dict ``structured_content`` via ``pydantic_core.to_jsonable_python``,
which BYPASSES any ``model_dump()`` override — including the
``exclude_none=True`` default set by ``AdCPBaseModel`` / ``SalesAgentBaseModel``
(``src/core/schemas/_base.py``). Passing a raw Pydantic response model
straight into ``ToolResult(structured_content=response)`` therefore wire-
serializes unset/None fields as JSON ``null`` instead of omitting them,
violating AdCP 3.1.1's absent-means-absent contract (salesagent-rrz8).

A2A (``_serialize_for_a2a``) and REST (``api_v1.py``) both pre-serialize via
``response.model_dump(mode="json")`` before handing data to their transport,
so they're unaffected. MCP tool wrappers must do the same before constructing
``ToolResult`` — this helper is that one logical operation, extracted once
(CLAUDE.md DRY invariant) instead of being re-implemented at every call site.
"""

from fastmcp.tools.tool import ToolResult
from pydantic import BaseModel


def build_tool_result(content: str, response: BaseModel) -> ToolResult:
    """Build a ``ToolResult`` with correctly-serialized ``structured_content``.

    Pre-serializes ``response`` via ``model_dump(mode="json")`` so the
    model's ``exclude_none=True`` override (and any other custom
    serialization) is honored on the MCP wire, matching A2A/REST behavior.

    Typed against ``pydantic.BaseModel`` rather than ``SalesAgentBaseModel``
    (our internal base) because response models here extend AdCP library
    response types directly (e.g. ``ListAccountsResponse(NestedModelSerializerMixin,
    LibraryListAccountsResponse)``) — the common ancestor across all 15
    call sites is Pydantic's own ``BaseModel``, not our internal mixin.

    Args:
        content: Human-readable text summary for the tool result (varies per
            call site — e.g. ``str(response)``, a computed summary string).
        response: The Pydantic response model to serialize as structured
            content.
    """
    return ToolResult(content=content, structured_content=response.model_dump(mode="json"))

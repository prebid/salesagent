"""The live MCP registry, read as ``tool -> (request DTO, advertised parameters)``.

Importing ``src.core.main`` is what registers the tools, so this sees exactly the set a
buyer sees. Every suite that grades "one fact per registered tool" reads membership from
HERE rather than from a dict of tool names it keeps itself: a tool absent from a
hand-kept dict is graded by nothing and reads as green, which is the failure mode two
separate tables in this repo were created to prevent and one of them then reintroduced.

The DTO comes from the registry row, which is what the tool serves, so no consumer can
resolve a DIFFERENT model for a tool than the one production actually builds.
"""

from __future__ import annotations

from functools import cache

from pydantic import BaseModel


@cache
def registered_tool_shapes() -> dict[str, tuple[type[BaseModel], frozenset[str]]]:
    """``tool -> (request DTO, ADVERTISED parameter names)`` for every tool with a DTO.

    Cached because building it imports and registers the whole MCP server; the registry
    does not change within a process.
    """
    import asyncio

    from src.core import main
    from src.core.tools.registry import TOOLS

    return {
        tool.name: (TOOLS[tool.name].dto, frozenset(tool.parameters.get("properties", {})))
        for tool in asyncio.run(main.mcp.list_tools())
    }


def registered_request_dtos() -> dict[str, type[BaseModel]]:
    """``tool -> request DTO``, for callers that do not need the advertised set."""
    return {tool: model for tool, (model, _) in registered_tool_shapes().items()}

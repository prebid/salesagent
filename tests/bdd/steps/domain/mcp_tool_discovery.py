"""Steps for the locally-added MCP tool-discovery feature.

The listed schema is read off the ``tools/list`` result the FastMCP Client received; the
expected value is computed from the registry row's request model
(``TOOLS[name].dto.model_json_schema()``), never from the listing.
"""

from __future__ import annotations

import json

from pytest_bdd import then, when

from src.core.tools.registry import TOOLS


@when("the buyer lists the seller's MCP tools")
def when_list_mcp_tools(ctx: dict) -> None:
    ctx["mcp_tools"] = ctx["env"].list_mcp_tools()


@then("each listed input schema is the tool's request model JSON Schema")
def then_listed_schema_is_request_model_schema(ctx: dict) -> None:
    listed = {tool.name: tool.inputSchema for tool in ctx["mcp_tools"]}
    assert TOOLS, "the registry declares no tools, so there is nothing to grade"
    differing = []
    for name, spec in sorted(TOOLS.items()):
        declared = spec.dto.model_json_schema()
        # A tool missing from the listing raises KeyError here, so the step cannot pass on a
        # listing that drops tools.
        if listed[name] != declared:
            differing.append(
                f"{name}: listed {len(json.dumps(listed[name]))} bytes, request model {len(json.dumps(declared))} bytes"
            )
    assert not differing, "tools/list publishes a schema other than the request model's:\n" + "\n".join(differing)

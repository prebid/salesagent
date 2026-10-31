"""list_creative_formats is advertised as the pin describes it: a deprecated compatibility task.

The SDK publishes "List available creative formats with asset requirements. Returns
format_ids needed for sync_creatives." for the tool, which presents it as a prerequisite.
At the pin the sales-agent task is deprecated and is no purchasability promise
(creative/task-reference/list_creative_formats.mdx: "Sales agents MUST NOT use an
agent-global format list as a purchasability promise"), so MCP and the A2A card advertise
the pinned request schema's own description instead. The expected text is read from the
schema file the SDK ships, the pin itself, not from the code under test.

REST carries no tool description, so these two transports are the whole advertising surface.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import adcp

from src.a2a_server.adcp_a2a_server import _derived_skills
from src.core.main import mcp

_SDK_PREREQUISITE_SENTENCE = "format_ids needed for sync_creatives"


def _pinned_schema(relative: str) -> dict:
    major_minor = ".".join(adcp.get_adcp_spec_version().split(".")[:2])
    return json.loads((Path(adcp.__file__).parent / "_schemas" / major_minor / relative).read_text())


def _mcp_descriptions() -> dict[str, str | None]:
    tools = asyncio.run(mcp.list_tools())
    return {tool.name: tool.description for tool in tools}


def _a2a_descriptions() -> dict[str, str]:
    return {skill.name: skill.description for skill in _derived_skills()}


def test_list_creative_formats_is_advertised_with_the_pinned_deprecation_on_mcp_and_a2a() -> None:
    schema = _pinned_schema("media-buy/list-creative-formats-request.json")
    assert schema.get("deprecated") is True, "the pin no longer marks the sales-agent task deprecated"
    expected = schema["description"]

    for transport, descriptions in (("mcp", _mcp_descriptions()), ("a2a", _a2a_descriptions())):
        advertised = descriptions["list_creative_formats"]
        assert advertised == expected, f"{transport} advertises {advertised!r}, not the pinned description"
        assert _SDK_PREREQUISITE_SENTENCE not in (advertised or ""), (
            f"{transport} still presents list_creative_formats as a prerequisite for sync_creatives"
        )


def test_a_tool_this_seller_has_no_replacement_for_keeps_the_sdk_description() -> None:
    """create_media_buy is deprecated at the pin too, but its replacements are not served here.

    Its pinned description sends a 3.2 caller to buy_products, which this seller does not
    implement, so it keeps the SDK's sentence on both transports.
    """
    from adcp.server.mcp_tools import ADCP_TOOL_DEFINITIONS

    sdk = {definition["name"]: definition["description"] for definition in ADCP_TOOL_DEFINITIONS}
    for transport, descriptions in (("mcp", _mcp_descriptions()), ("a2a", _a2a_descriptions())):
        assert descriptions["create_media_buy"] == sdk["create_media_buy"], (
            f"{transport} replaced create_media_buy's description: {descriptions['create_media_buy']!r}"
        )

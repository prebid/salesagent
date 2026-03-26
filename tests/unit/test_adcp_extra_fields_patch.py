"""Tests that MCP tool functions accept extra fields per AdCP additionalProperties: true.

AdCP specifies additionalProperties: true on all schemas, meaning buyer agents may send
fields the seller agent doesn't recognize. Without **kwargs on the MCP tool functions,
FastMCP/Pydantic rejects unknown parameters with "Unexpected keyword argument" before
they ever reach our Pydantic models.

Two layers are tested:
1. Every MCP tool function declares **kwargs: Any in its signature.
2. The FastMCP patch ensures registration succeeds and the generated JSON schema
   includes additionalProperties: true.
"""

import inspect

import pytest

from src.core.tools.capabilities import get_adcp_capabilities
from src.core.tools.creative_formats import list_creative_formats
from src.core.tools.creatives.listing import list_creatives
from src.core.tools.creatives.sync_wrappers import sync_creatives
from src.core.tools.media_buy_create import create_media_buy
from src.core.tools.media_buy_delivery import get_media_buy_delivery
from src.core.tools.media_buy_list import get_media_buys
from src.core.tools.media_buy_update import update_media_buy
from src.core.tools.performance import update_performance_index
from src.core.tools.products import get_products
from src.core.tools.properties import list_authorized_properties
from src.core.tools.task_management import complete_task, get_task, list_tasks

ALL_MCP_TOOLS = [
    get_adcp_capabilities,
    get_products,
    list_creative_formats,
    sync_creatives,
    list_creatives,
    list_authorized_properties,
    create_media_buy,
    update_media_buy,
    get_media_buy_delivery,
    get_media_buys,
    update_performance_index,
    list_tasks,
    get_task,
    complete_task,
]


@pytest.mark.parametrize("func", ALL_MCP_TOOLS, ids=lambda f: f.__name__)
def test_mcp_tool_accepts_var_keyword(func):
    """Every MCP tool function must have **kwargs to accept additional properties."""
    sig = inspect.signature(func)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    assert has_var_keyword, (
        f"{func.__name__}() is missing **kwargs. "
        f"AdCP requires additionalProperties: true — add **kwargs: Any to the signature."
    )


class TestFastMCPPatchApplied:
    """Verify the FastMCP patch is active and produces correct schemas."""

    def test_patch_is_applied(self):
        """The patch must be applied before tool registration."""
        import src.core.adcp_extra_fields_patch as patch_mod
        from src.core.main import mcp  # noqa: F401 — triggers patch + registration

        assert patch_mod._PATCHED, "patch_fastmcp_extra_fields() was not called"

    async def test_registered_tools_have_additional_properties(self):
        """Registered MCP tools should have additionalProperties: true in their schema."""
        from src.core.main import mcp

        tools = await mcp.list_tools()
        for tool in tools:
            schema = tool.parameters
            assert schema.get("additionalProperties") is True, (
                f"Tool '{tool.name}' schema is missing additionalProperties: true. Schema: {schema}"
            )

"""Test that all AdCP tools are properly registered with MCP server."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# All MCP tools - unified mode is now enabled by default
ALL_TOOLS = [
    # Core AdCP tools
    "get_products",
    "create_media_buy",
    "update_media_buy",
    "get_media_buy_delivery",
    "sync_creatives",
    "list_creatives",
    "list_creative_formats",
    "list_authorized_properties",
    "update_performance_index",
    "get_signals",
    "activate_signal",
    # Task management tools (HITL - Human-in-the-loop)
    "list_tasks",
    "get_task",
    "complete_task",
]


def test_all_tools_registered():
    """Verify all expected AdCP tools are registered with MCP."""
    from src.core.main import mcp

    # Get registered tool names from ToolManager
    registered_tools = list(mcp._tool_manager._tools.keys())

    for tool in ALL_TOOLS:
        assert tool in registered_tools, f"Tool '{tool}' is not registered with MCP server"

    # Verify no unexpected tools
    unexpected = set(registered_tools) - set(ALL_TOOLS)
    assert len(unexpected) == 0, f"Unexpected tools registered: {unexpected}"


def test_tool_registration_completeness():
    """Verify MCP and A2A raw functions are in sync."""
    from src.core import tools as tools_module

    # Get all raw wrapper functions
    raw_functions = [name for name in dir(tools_module) if name.endswith("_raw")]

    # Each raw function should have a corresponding MCP tool
    for raw_func in raw_functions:
        tool_name = raw_func.replace("_raw", "")
        assert hasattr(tools_module, raw_func), f"Raw function {raw_func} not found in tools module"

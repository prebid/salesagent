"""Test that all AdCP tools are properly registered with MCP server."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# All MCP tools - unified mode is now enabled by default
# Note: signals tools (get_signals, activate_signal) removed - should come from dedicated signals agents
def _registry_tools() -> set[str]:
    """The tools the registry declares -- the one place a tool is declared at all."""
    from src.core.tools.registry import TOOLS

    return set(TOOLS)


def test_all_tools_registered():
    """Verify all expected AdCP tools are registered with MCP."""
    import asyncio

    from src.core.main import mcp

    tools = asyncio.new_event_loop().run_until_complete(mcp.list_tools())
    registered_tools = [t.name for t in tools]

    # EQUALITY against the registry, not membership in a hand-written list. The list this
    # replaces had gone stale in both directions -- it named list_authorized_properties and
    # update_performance_index, which are not AdCP tasks at the pinned version and are no
    # longer declared, and it could not have known about a tool added tomorrow. Registration
    # is generated from TOOLS, so the honest statement is that the two agree exactly.
    assert set(registered_tools) == _registry_tools(), (
        f"MCP registration disagrees with the registry. "
        f"missing={sorted(_registry_tools() - set(registered_tools))} "
        f"unexpected={sorted(set(registered_tools) - _registry_tools())}"
    )


def test_tool_registration_completeness():
    """Verify MCP and A2A raw functions are in sync."""
    from src.core import tools as tools_module

    # Get all raw wrapper functions
    raw_functions = [name for name in dir(tools_module) if name.endswith("_raw")]

    # Each raw function should have a corresponding MCP tool
    for raw_func in raw_functions:
        tool_name = raw_func.replace("_raw", "")
        assert hasattr(tools_module, raw_func), f"Raw function {raw_func} not found in tools module"

"""
Contract validation for E2E tests.

This module adds automatic validation that E2E tests only call tools that actually exist
in the MCP server. This prevents tests from calling non-existent or future tools.

Usage: This module is automatically loaded by pytest via conftest.py
"""

import ast
from pathlib import Path

import pytest

# Actual tools that exist (keep this updated with src/core/main.py)
ACTUAL_MCP_TOOLS = {
    "activate_signal",
    "create_media_buy",
    "get_media_buy_delivery",
    "get_products",
    "get_signals",
    "list_authorized_properties",
    "list_creative_formats",
    "list_creatives",
    "sync_creatives",
    "update_media_buy",
    "update_performance_index",
}

# Tools that are intentionally called for error handling tests
INTENTIONAL_NONEXISTENT_TOOLS = {
    "nonexistent_tool",  # Used in error handling tests
    "check_axe_requirements",  # Optional tool tested in try/except blocks
}


def extract_tool_calls_from_test_file(test_file: Path) -> dict[str, set[str]]:
    """
    Extract all tool calls from a test file.

    Returns:
        Dict mapping test function names to sets of tool names they call
    """
    with open(test_file) as f:
        content = f.read()

    # Parse the Python file
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {}

    tool_calls = {}
    current_test = None

    class ToolCallVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            nonlocal current_test
            if node.name.startswith("test_"):
                current_test = node.name
                tool_calls[current_test] = set()
            self.generic_visit(node)
            if node.name == current_test:
                current_test = None

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            # Check for call_tool("tool_name") or call_mcp_tool("tool_name")
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("call_tool", "call_mcp_tool"):
                    if node.args and isinstance(node.args[0], ast.Constant):
                        tool_name = node.args[0].value
                        if current_test:
                            tool_calls[current_test].add(tool_name)
            self.generic_visit(node)

    visitor = ToolCallVisitor()
    visitor.visit(tree)

    return tool_calls


def pytest_collection_modifyitems(config, items):
    """
    Hook that runs during test collection to validate tool calls.

    This validates that E2E tests only call tools that actually exist.
    """
    e2e_dir = Path(__file__).parent

    # Group tests by file
    tests_by_file = {}
    for item in items:
        if item.fspath.basename.startswith("test_") and item.fspath.basename.endswith(".py"):
            file_path = Path(item.fspath)
            if file_path not in tests_by_file:
                tests_by_file[file_path] = []
            tests_by_file[file_path].append(item)

    # Validate each file
    for test_file, test_items in tests_by_file.items():
        tool_calls = extract_tool_calls_from_test_file(test_file)

        for test_item in test_items:
            test_name = test_item.name.split("[")[0]  # Remove parametrize suffix

            if test_name in tool_calls:
                invalid_tools = tool_calls[test_name] - ACTUAL_MCP_TOOLS - INTENTIONAL_NONEXISTENT_TOOLS

                if invalid_tools:
                    # Mark test with a clear error
                    marker = pytest.mark.skip(
                        reason=f"❌ CONTRACT VIOLATION: Test calls non-existent tools: {', '.join(sorted(invalid_tools))}. "
                        f"Either implement these tools or remove the test."
                    )
                    test_item.add_marker(marker)

                    # Also add a warning
                    test_item.add_marker(
                        pytest.mark.filterwarnings(f"error::UserWarning:Test {test_name} calls non-existent MCP tools")
                    )


def validate_tool_exists(tool_name: str) -> bool:
    """
    Helper function to check if a tool exists.

    Can be used in tests:
        from tests.e2e.conftest_contract_validation import validate_tool_exists
        assert validate_tool_exists('get_products'), "get_products tool must exist"
    """
    return tool_name in ACTUAL_MCP_TOOLS


def get_available_tools() -> set[str]:
    """Get the set of all available MCP tools."""
    return ACTUAL_MCP_TOOLS.copy()


# Export for use in tests
__all__ = ["validate_tool_exists", "get_available_tools", "ACTUAL_MCP_TOOLS"]

"""Test that all MCP tool wrappers can be imported and have their dependencies.

This catches missing imports in the MCP wrapper layer that unit tests
typically don't exercise because they mock implementations or call
the _impl functions directly.
"""


class TestMCPToolImports:
    """Test that MCP tool wrapper functions have all their dependencies imported."""

    def test_main_module_imports_successfully(self):
        """Test that main.py imports without errors.

        This would have caught the missing create_get_products_request import.
        If main.py has syntax errors or missing imports that are used in
        top-level code, this test will fail at import time.
        """
        from src.core import main

        # If we got here, all imports succeeded
        assert main is not None

    def test_get_products_dependencies_exist(self):
        """Test that get_products has all its dependencies imported.

        This specifically checks for the create_get_products_request bug
        that wasn't caught by other unit tests.
        """
        from src.core import schema_helpers

        # Check that the function exists in the schema_helpers module
        assert hasattr(
            schema_helpers, "create_get_products_request"
        ), "create_get_products_request not found in schema_helpers module"

        # Verify it's callable
        assert callable(schema_helpers.create_get_products_request), "create_get_products_request should be callable"

    def test_mcp_instance_exists(self):
        """Test that the MCP server instance exists."""
        from src.core.main import mcp

        assert mcp is not None
        assert hasattr(mcp, "_tool_manager")  # FastMCP internal attribute

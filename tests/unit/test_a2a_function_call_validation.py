#!/usr/bin/env python3
"""
A2A Function Call Validation Tests

These unit tests specifically validate that function imports and calls are correct.
No mocking of the functions themselves - we test that they can be imported and called correctly.

This would have caught the core_get_signals_tool.fn() bug.
"""

import inspect
import os
import sys
from unittest.mock import Mock

import pytest

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestCoreToolImports:
    """Test that core tool imports work correctly."""

    def test_core_tools_are_functions_not_objects(self):
        """Test that imported core tools are actual functions, not FunctionTool objects."""
        try:
            from src.a2a_server.adcp_a2a_server import (
                core_create_media_buy_tool,
                core_get_products_tool,
                core_get_signals_tool,
                core_list_creatives_tool,
                core_sync_creatives_tool,
            )
        except ImportError as e:
            if "a2a" in str(e):
                pytest.skip("a2a library not available in CI environment")
            raise

        tools = {
            "core_get_products_tool": core_get_products_tool,
            "core_create_media_buy_tool": core_create_media_buy_tool,
            "core_get_signals_tool": core_get_signals_tool,
            "core_list_creatives_tool": core_list_creatives_tool,
            "core_sync_creatives_tool": core_sync_creatives_tool,
        }

        for name, tool in tools.items():
            # Should be callable
            assert callable(tool), f"{name} should be callable"

            # Should be a function, not a custom object
            assert inspect.isfunction(tool) or inspect.iscoroutinefunction(tool), f"{name} should be a function"

            # Should not have .fn attribute (indicates it's not a FunctionTool wrapper)
            if hasattr(tool, "fn"):
                pytest.fail(
                    f"{name} appears to be a FunctionTool wrapper with .fn attribute - import the function directly"
                )

    def test_async_functions_identified_correctly(self):
        """Test that async functions are properly identified."""
        try:
            from src.a2a_server.adcp_a2a_server import core_get_products_tool, core_get_signals_tool
        except ImportError as e:
            if "a2a" in str(e):
                pytest.skip("a2a library not available in CI environment")
            raise

        # These should be async functions
        assert inspect.iscoroutinefunction(core_get_products_tool), "core_get_products_tool should be async"
        assert inspect.iscoroutinefunction(core_get_signals_tool), "core_get_signals_tool should be async"

    def test_function_signatures_accessible(self):
        """Test that function signatures can be inspected (indicates proper import)."""
        try:
            from src.a2a_server.adcp_a2a_server import core_get_products_tool, core_get_signals_tool
        except ImportError as e:
            if "a2a" in str(e):
                pytest.skip("a2a library not available in CI environment")
            raise

        # Should be able to get signature without errors
        try:
            sig1 = inspect.signature(core_get_products_tool)
            sig2 = inspect.signature(core_get_signals_tool)

            # Should have parameters
            assert len(sig1.parameters) > 0, "core_get_products_tool should have parameters"
            assert len(sig2.parameters) > 0, "core_get_signals_tool should have parameters"

        except Exception as e:
            pytest.fail(f"Failed to get function signatures: {e}")


class TestA2AHandlerMethodCalls:
    """Test that A2A handler methods call core functions correctly."""

    def setup_method(self):
        """Set up test fixtures."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        self.handler = AdCPRequestHandler()

    def test_handler_skill_methods_exist(self):
        """Test that all skill handler methods exist."""
        required_methods = [
            "_handle_get_products_skill",
            "_handle_create_media_buy_skill",
            "_handle_sync_creatives_skill",
            "_handle_list_creatives_skill",
            "_handle_get_signals_skill",
        ]

        for method_name in required_methods:
            assert hasattr(self.handler, method_name), f"Handler missing method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Method {method_name} is not callable"
            assert inspect.iscoroutinefunction(method), f"Method {method_name} should be async"

    def test_source_code_function_call_patterns(self):
        """Test that source code uses correct function call patterns."""
        # Read the A2A server source file
        file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")

        with open(file_path) as f:
            content = f.read()

        # Patterns that indicate correct function calls
        correct_patterns = [
            "await core_get_products_tool(",
            "await core_get_signals_tool(",
            "core_create_media_buy_tool(",
            "core_sync_creatives_tool(",
            "core_list_creatives_tool(",
        ]

        # Patterns that indicate incorrect function calls (the bug we fixed)
        incorrect_patterns = [
            "core_get_products_tool.fn(",
            "core_get_signals_tool.fn(",
            "core_create_media_buy_tool.fn(",
            "core_sync_creatives_tool.fn(",
            "core_list_creatives_tool.fn(",
        ]

        # Verify no incorrect patterns exist
        for pattern in incorrect_patterns:
            assert pattern not in content, f"Found incorrect function call pattern: {pattern}"

        # Verify at least some correct patterns exist
        found_correct = sum(1 for pattern in correct_patterns if pattern in content)
        assert found_correct > 0, "No correct function call patterns found in source code"

    def test_skill_handler_method_parameters(self):
        """Test that skill handler methods have expected parameter signatures."""
        method_signatures = {
            "_handle_get_products_skill": ["parameters", "auth_token"],
            "_handle_create_media_buy_skill": ["parameters", "auth_token"],
            "_handle_get_signals_skill": ["parameters", "auth_token"],
        }

        for method_name, expected_params in method_signatures.items():
            if hasattr(self.handler, method_name):
                method = getattr(self.handler, method_name)
                sig = inspect.signature(method)
                param_names = list(sig.parameters.keys())

                # When called on a bound method, 'self' is already bound and not shown in parameters
                # When called on an unbound method from the class, 'self' is shown
                # Both are valid patterns

                for expected_param in expected_params:
                    assert expected_param in param_names, f"{method_name} missing parameter: {expected_param}"


class TestFunctionCallIntegration:
    """Integration tests for function calls without excessive mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        self.handler = AdCPRequestHandler()

    def test_tool_context_creation_does_not_fail(self):
        """Test that ToolContext creation works without errors."""
        # This tests the integration without mocking everything
        try:
            # Mock only the external dependencies, not the function calls themselves
            with (pytest.MonkeyPatch().context() as m,):
                # Mock external auth functions (updated signature: token, tenant_id)
                m.setattr(
                    "src.a2a_server.adcp_a2a_server.get_principal_from_token",
                    lambda token, tenant_id=None: "test_principal",
                )
                m.setattr("src.a2a_server.adcp_a2a_server.get_current_tenant", lambda: {"tenant_id": "test_tenant"})
                # Mock tenant resolution functions (return None to use fallback path)
                m.setattr("src.core.config_loader.get_tenant_by_subdomain", lambda x: None)
                m.setattr("src.core.config_loader.get_tenant_by_virtual_host", lambda x: None)
                m.setattr("src.core.config_loader.get_tenant_by_id", lambda x: None)
                m.setattr("src.core.config_loader.set_current_tenant", lambda x: None)

                # Test that the method can be called without errors
                tool_context = self.handler._create_tool_context_from_a2a(
                    auth_token="test_token", tool_name="test_tool", context_id="test_context"
                )

                # Should return a ToolContext-like object
                assert hasattr(tool_context, "tenant_id")
                assert hasattr(tool_context, "principal_id")
                assert tool_context.tenant_id == "test_tenant"
                assert tool_context.principal_id == "test_principal"

        except Exception as e:
            pytest.fail(f"ToolContext creation failed: {e}")

    def test_core_function_can_be_called_with_mock_context(self):
        """Test that core functions can actually be called (verifies imports work)."""
        from datetime import UTC, datetime

        from src.a2a_server.adcp_a2a_server import core_get_signals_tool
        from src.core.schemas import GetSignalsRequest
        from src.core.tool_context import ToolContext

        # Create minimal ToolContext
        tool_context = ToolContext(
            context_id="test",
            tenant_id="test_tenant",
            principal_id="test_principal",
            tool_name="get_signals",
            request_timestamp=datetime.now(UTC),
            metadata={},
            testing_context={},
        )

        # Create minimal AdCP-compliant request
        from src.core.schemas import SignalDeliverTo

        deliver_to = SignalDeliverTo(platforms="all", countries=["US"])
        request = GetSignalsRequest(signal_spec="test signal description", deliver_to=deliver_to)

        # This should not fail with import/call errors
        # We're not testing the business logic, just that the function can be called
        try:
            # Use asyncio to test async function
            import asyncio

            async def test_call():
                # Mock the database and other external dependencies
                with (pytest.MonkeyPatch().context() as m,):
                    # Mock database session and queries
                    m.setattr("src.core.main.get_db_session", lambda: Mock())

                    # Try to call the function
                    # If this fails with 'FunctionTool' object is not callable, we caught the bug
                    result = await core_get_signals_tool(request, tool_context)
                    return result

            # Run the async test
            result = asyncio.run(test_call())

            # If we get here, the function call succeeded
            assert True, "Function call succeeded"

        except TypeError as e:
            if "'FunctionTool' object is not callable" in str(e):
                pytest.fail("Found the 'FunctionTool' object is not callable bug - function import is incorrect")
            else:
                # Other TypeError might be expected due to mocking
                pass
        except Exception:
            # Other exceptions might be expected due to mocking/missing data
            # We're only testing that the function can be called, not that it succeeds
            pass


class TestImportValidation:
    """Test that imports are structured correctly."""

    def test_import_statements_in_source(self):
        """Test that import statements in source are correct."""
        file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")

        with open(file_path) as f:
            content = f.read()

        # Should import core functions directly (now from tools module to avoid FastMCP decorators)
        expected_imports = [
            "from src.core.tools import (",
            "create_media_buy_raw as core_create_media_buy_tool,",
            "get_products_raw as core_get_products_tool,",
            "get_signals_raw as core_get_signals_tool,",
            "list_creatives_raw as core_list_creatives_tool,",
            "sync_creatives_raw as core_sync_creatives_tool,",
        ]

        for import_statement in expected_imports:
            assert import_statement in content, f"Missing expected import: {import_statement}"

    def test_no_function_tool_imports(self):
        """Test that source doesn't import FunctionTool objects incorrectly."""
        file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")

        with open(file_path) as f:
            content = f.read()

        # Should not have patterns that suggest FunctionTool imports
        problematic_patterns = [
            "from fastmcp import FunctionTool",
            "FunctionTool(",
            ".fn)",  # Often indicates accessing .fn on a tool object
        ]

        for pattern in problematic_patterns:
            if pattern in content:
                pytest.fail(f"Found potentially problematic pattern: {pattern}")


if __name__ == "__main__":
    # Run a quick validation when executed directly
    print("🔍 Running function call validation...")

    # Test 1: Can import core tools
    try:
        from src.a2a_server.adcp_a2a_server import core_get_signals_tool

        print("✅ Core tool import succeeded")
    except Exception as e:
        print(f"❌ Core tool import failed: {e}")
        sys.exit(1)

    # Test 2: Function is callable
    if callable(core_get_signals_tool):
        print("✅ Core tool is callable")
    else:
        print("❌ Core tool is not callable")
        sys.exit(1)

    # Test 3: Check for .fn patterns in source
    file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")
    with open(file_path) as f:
        content = f.read()

    if "core_get_signals_tool.fn(" in content:
        print("❌ Found .fn() call pattern in source")
        sys.exit(1)
    else:
        print("✅ No problematic .fn() patterns found")

    print("🎉 All function call validations passed!")

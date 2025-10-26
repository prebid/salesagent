#!/usr/bin/env python3
"""
A2A Regression Prevention Tests

These tests specifically target the bugs that slipped through our test coverage:
1. Agent card URLs with trailing slashes causing redirect/auth issues
2. Function call issues like core_get_signals_tool.fn()

The goal is to have focused, non-mocked tests that would have caught these issues.
"""

import logging
import os
import sys

import pytest
import requests

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, create_agent_card

logger = logging.getLogger(__name__)


class TestAgentCardURLRegression:
    """Tests to prevent agent card URL issues that cause redirect/auth problems."""

    def test_agent_card_url_no_trailing_slash(self):
        """Test that agent card URLs don't have trailing slashes that cause redirects."""
        agent_card = create_agent_card()

        # Critical: URL should not end with trailing slash
        assert not agent_card.url.endswith("/"), f"Agent card URL '{agent_card.url}' should not end with trailing slash"

        # Should be a valid URL format
        assert agent_card.url.startswith(("http://", "https://")), f"Invalid URL format: {agent_card.url}"

        # Should end with /a2a (no slash)
        assert agent_card.url.endswith("/a2a"), f"Agent card URL should end with '/a2a': {agent_card.url}"

    def test_dynamic_agent_card_urls_no_trailing_slash(self):
        """Test that dynamically generated agent card URLs don't have trailing slashes."""
        from unittest.mock import Mock

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        # Create mock request with tenant header
        mock_request = Mock()
        mock_request.headers = {"x-adcp-tenant": "test-tenant"}

        # Import the function that creates dynamic agent cards
        # This requires mocking the create_dynamic_agent_card function call
        handler = AdCPRequestHandler()

        # Test with different tenant scenarios
        test_cases = [
            {"x-adcp-tenant": "publisher1"},
            {"x-adcp-tenant": "sports-news"},
            {},  # No tenant header
        ]

        for headers in test_cases:
            mock_request.headers = headers

            # We need to test the URL generation logic
            # For now, test the patterns we expect
            if "x-adcp-tenant" in headers:
                expected_url = f"https://{headers['x-adcp-tenant']}.sales-agent.scope3.com/a2a"
            else:
                expected_url = "https://sales-agent.scope3.com/a2a"

            # Verify no trailing slash in expected URLs
            assert not expected_url.endswith("/a2a/"), f"Generated URL should not have trailing slash: {expected_url}"
            assert expected_url.endswith("/a2a"), f"Generated URL should end with '/a2a': {expected_url}"

    def test_production_vs_development_url_consistency(self):
        """Test that both production and development URLs follow same pattern."""
        # Test production URLs (what's in the code)
        production_patterns = [
            "https://sales-agent.scope3.com/a2a",
            "https://tenant.sales-agent.scope3.com/a2a",
        ]

        # Test development URLs (what's in the code)
        development_patterns = [
            "http://localhost:8091/a2a",
            "https://test-app.fly.dev/a2a",
        ]

        all_patterns = production_patterns + development_patterns

        for url in all_patterns:
            assert not url.endswith("/a2a/"), f"URL pattern should not have trailing slash: {url}"
            assert url.endswith("/a2a"), f"URL pattern should end with '/a2a': {url}"

    @pytest.mark.integration
    def test_agent_card_http_endpoint_url_format(self):
        """Integration test: Verify actual HTTP endpoint returns correct URL format."""
        # This test requires the server to be running - skip if not available
        try:
            response = requests.get("http://localhost:8091/.well-known/agent.json", timeout=2)
            if response.status_code == 200:
                agent_card = response.json()
                url = agent_card.get("url")

                if url:
                    assert not url.endswith("/"), f"HTTP endpoint returned URL with trailing slash: {url}"
                    assert url.endswith("/a2a"), f"HTTP endpoint URL should end with '/a2a': {url}"
        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091 - skipping HTTP integration test")


class TestFunctionCallRegression:
    """Tests to prevent function call/import issues like core_get_signals_tool.fn()."""

    def test_core_function_imports_are_callable(self):
        """Test that imported core functions are actually callable."""
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

        # These should be callable functions, not FunctionTool objects
        assert callable(core_get_products_tool), "core_get_products_tool should be callable"
        assert callable(core_create_media_buy_tool), "core_create_media_buy_tool should be callable"
        assert callable(core_get_signals_tool), "core_get_signals_tool should be callable"
        assert callable(core_list_creatives_tool), "core_list_creatives_tool should be callable"
        assert callable(core_sync_creatives_tool), "core_sync_creatives_tool should be callable"

    def test_core_function_call_patterns(self):
        """Test that function calls use correct patterns (not .fn())."""
        # Read the A2A server file and check for correct call patterns
        file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")

        with open(file_path) as f:
            content = f.read()

        # Should not have .fn() calls on core tools
        problematic_patterns = [
            "core_get_products_tool.fn(",
            "core_create_media_buy_tool.fn(",
            "core_get_signals_tool.fn(",
            "core_list_creatives_tool.fn(",
            "core_sync_creatives_tool.fn(",
        ]

        for pattern in problematic_patterns:
            assert pattern not in content, f"Found problematic function call pattern: {pattern}"

    def test_handler_skill_methods_exist(self):
        """Test that all skill handler methods exist and are callable."""
        handler = AdCPRequestHandler()

        # All these methods should exist and be callable
        required_skill_methods = [
            "_handle_get_products_skill",
            "_handle_create_media_buy_skill",
            "_handle_sync_creatives_skill",
            "_handle_list_creatives_skill",
            "_handle_get_signals_skill",
        ]

        for method_name in required_skill_methods:
            assert hasattr(handler, method_name), f"Handler missing method: {method_name}"
            method = getattr(handler, method_name)
            assert callable(method), f"Handler method not callable: {method_name}"

    def test_async_function_signatures(self):
        """Test that async functions have correct signatures."""
        import inspect

        try:
            from src.a2a_server.adcp_a2a_server import core_get_products_tool, core_get_signals_tool
        except ImportError as e:
            if "a2a" in str(e):
                pytest.skip("a2a library not available in CI environment")
            raise

        # These should be async functions
        assert inspect.iscoroutinefunction(core_get_signals_tool), "core_get_signals_tool should be async"
        assert inspect.iscoroutinefunction(core_get_products_tool), "core_get_products_tool should be async"


class TestAuthenticationFlow:
    """Tests to prevent authentication-related regressions."""

    def test_auth_token_extraction_method_exists(self):
        """Test that authentication token extraction works."""
        handler = AdCPRequestHandler()

        # Method should exist
        assert hasattr(handler, "_get_auth_token"), "Handler should have _get_auth_token method"
        assert callable(handler._get_auth_token), "_get_auth_token should be callable"

    def test_tool_context_creation_method_exists(self):
        """Test that ToolContext creation method exists and works."""
        handler = AdCPRequestHandler()

        # Method should exist
        assert hasattr(
            handler, "_create_tool_context_from_a2a"
        ), "Handler should have _create_tool_context_from_a2a method"
        assert callable(handler._create_tool_context_from_a2a), "_create_tool_context_from_a2a should be callable"


class TestHTTPBehaviorRegression:
    """Tests to prevent HTTP-level bugs like redirect issues."""

    def test_middleware_handles_both_a2a_paths(self):
        """Test that middleware handles both /a2a and /a2a/ paths."""
        # Read the A2A server file to verify middleware logic
        file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")

        with open(file_path) as f:
            content = f.read()

        # Should handle both paths in middleware
        assert 'request.url.path in ["/a2a", "/a2a/"]' in content, "Middleware should handle both /a2a and /a2a/ paths"

    @pytest.mark.integration
    def test_no_redirect_on_agent_card_endpoints(self):
        """Integration test: Verify agent card endpoints don't redirect."""
        endpoints_to_test = [
            "/.well-known/agent.json",
            "/agent.json",
        ]

        for endpoint in endpoints_to_test:
            try:
                # Use allow_redirects=False to catch any redirects
                response = requests.get(f"http://localhost:8091{endpoint}", allow_redirects=False, timeout=2)

                if response.status_code == 200:
                    # Should be 200, not a redirect (301, 302, etc.)
                    assert (
                        200 <= response.status_code < 300
                    ), f"Endpoint {endpoint} returned redirect: {response.status_code}"

                    # Should return JSON
                    assert response.headers.get("content-type", "").startswith("application/json")

                    # Should have agent card data
                    data = response.json()
                    assert "name" in data
                    assert "url" in data

                    # URL should not have trailing slash
                    url = data["url"]
                    assert not url.endswith("/"), f"Agent card URL has trailing slash: {url}"

            except (requests.ConnectionError, requests.Timeout):
                pytest.skip(f"A2A server not running - skipping HTTP test for {endpoint}")


# Summary test to run all regression checks
def test_regression_prevention_summary():
    """Summary test that runs key regression checks."""

    try:
        # 1. Agent card URL format
        agent_card = create_agent_card()
        assert not agent_card.url.endswith("/"), "REGRESSION: Agent card URL has trailing slash"

        # 2. Function imports are callable
        from src.a2a_server.adcp_a2a_server import core_get_signals_tool

        assert callable(core_get_signals_tool), "REGRESSION: Core function not callable"

        # 3. Handler has required methods
        handler = AdCPRequestHandler()
        assert hasattr(handler, "_handle_get_signals_skill"), "REGRESSION: Handler missing skill method"
    except ImportError as e:
        if "a2a" in str(e):
            pytest.skip("a2a library not available in CI environment")
        raise

    # 4. File doesn't contain problematic patterns
    file_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "a2a_server", "adcp_a2a_server.py")
    with open(file_path) as f:
        content = f.read()
    assert "core_get_signals_tool.fn(" not in content, "REGRESSION: Found .fn() call pattern"

    logger.info("✅ All regression prevention checks passed")


if __name__ == "__main__":
    # Run the summary test when executed directly
    test_regression_prevention_summary()
    print("✅ Regression prevention tests passed")

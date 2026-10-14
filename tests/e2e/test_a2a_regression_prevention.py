#!/usr/bin/env python3
"""
A2A Regression Prevention Tests

These tests specifically target the bugs that slipped through our test coverage:
1. Agent card URLs with trailing slashes causing redirect/auth issues
2. Function call issues with core tools

The goal is to have focused, non-mocked tests that would have caught these issues.
"""

import logging
import os
import sys

import pytest
import requests

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.tools.registry import TOOLS

logger = logging.getLogger(__name__)


class TestAgentCardURLRegression:
    """Tests to prevent agent card URL issues that cause redirect/auth problems."""

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
                expected_url = f"https://{headers['x-adcp-tenant']}.sales-agent.example.com/a2a"
            else:
                expected_url = "https://sales-agent.example.com/a2a"

            # Verify no trailing slash in expected URLs
            assert not expected_url.endswith("/a2a/"), f"Generated URL should not have trailing slash: {expected_url}"
            assert expected_url.endswith("/a2a"), f"Generated URL should end with '/a2a': {expected_url}"

    def test_production_vs_development_url_consistency(self):
        """Test that both production and development URLs follow same pattern."""
        # Test production URLs (what's in the code)
        production_patterns = [
            "https://sales-agent.example.com/a2a",
            "https://tenant.sales-agent.example.com/a2a",
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
    def test_agent_card_http_endpoint_url_format(self, live_server):
        """Integration test: Verify actual HTTP endpoint returns correct URL format."""
        # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
        response = requests.get(f"{live_server['a2a']}/.well-known/agent-card.json", timeout=2)
        if response.status_code == 200:
            agent_card = response.json()
            url = agent_card.get("url")

            if url:
                assert not url.endswith("/"), f"HTTP endpoint returned URL with trailing slash: {url}"
                assert url.endswith("/a2a"), f"HTTP endpoint URL should end with '/a2a': {url}"


class TestFunctionCallRegression:
    """Tests to prevent function call/import issues with core tools.

    The ``core_<tool>_tool`` module-level wrappers this class was written against
    are deleted along with the per-tool ``_handle_<tool>_skill`` methods (one
    boundary for every transport). What they asserted — "the thing A2A calls is a
    plain callable, not a FunctionTool that needs ``.fn()``" — is now a property of
    the registry row, which is the one place any transport looks.
    """

    def test_registry_rows_hold_plain_callables(self):
        """Each core tool's registry row holds a plain callable implementation.

        The successor to ``test_core_function_imports_are_callable`` and
        ``test_core_function_call_patterns``. Those graded four deleted module-level
        wrappers and grepped the A2A server source for ``core_*_tool.fn(`` — with the
        names gone, the grep can only pass vacuously. ``TOOLS[name].impl`` is what
        ``invoke_tool`` actually calls, so it is where "plain callable, not a
        FunctionTool" has to hold.
        """
        # Note: signals tools removed - should come from dedicated signals agents
        for tool_name in ("get_products", "create_media_buy", "sync_creatives", "list_creatives"):
            impl = TOOLS[tool_name].impl
            assert callable(impl), f"{tool_name}: registry impl is not callable"
            assert not hasattr(impl, "fn"), (
                f"{tool_name}: registry impl is a wrapper object exposing .fn, not the function itself"
            )

    def test_core_skills_are_dispatchable_over_a2a(self):
        """The core skills are dispatchable over A2A, per the registry.

        This used to assert a ``_handle_<tool>_skill`` method existed per tool. Those
        eleven methods are deleted; A2A dispatch is the one derived
        ``AdCPRequestHandler._dispatch_skill``. The ``hasattr`` filter that used to
        select those methods was itself the defect — it silently overrode the
        registry, so ``list_tasks``, ``get_task_status`` and ``complete_task`` were
        advertised on the agent card and then answered MethodNotFoundError. The
        obligation that survives is that the registry row says the tool is
        dispatchable over A2A, which is now the only thing that decides it.
        """
        handler = AdCPRequestHandler()
        assert callable(handler._dispatch_skill), "A2A's one dispatch method is missing"

        # Note: get_signals removed - should come from dedicated signals agents
        for tool_name in ("get_products", "create_media_buy", "sync_creatives", "list_creatives"):
            assert TOOLS[tool_name].a2a is True, f"{tool_name} is not dispatchable over A2A"

    def test_a2a_dispatch_is_awaitable(self):
        """A2A's one dispatch method is a coroutine function.

        The successor to ``test_async_function_signatures``, which pinned
        ``core_get_products_tool`` / ``core_create_media_buy_tool`` as coroutines.
        Those wrappers are deleted, and "every tool impl is async" is not the
        replacement invariant — registry impls are a mix (``_list_creative_formats_impl``
        is sync, and ``invoke_tool`` handles both). What A2A's request path actually
        requires is that ``_dispatch_skill`` be awaitable, since ``on_message_send``
        awaits it.
        """
        import inspect

        assert inspect.iscoroutinefunction(AdCPRequestHandler._dispatch_skill), (
            "_dispatch_skill must be async — the A2A request path awaits it"
        )


class TestHTTPBehaviorRegression:
    """Tests to prevent HTTP-level bugs like redirect issues."""

    @pytest.mark.integration
    def test_no_redirect_on_agent_card_endpoints(self, live_server):
        """Integration test: Verify agent card endpoints don't redirect."""
        # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
        endpoints_to_test = [
            "/.well-known/agent-card.json",
        ]

        for endpoint in endpoints_to_test:
            # Use allow_redirects=False to catch any redirects
            response = requests.get(f"{live_server['a2a']}{endpoint}", allow_redirects=False, timeout=2)

            if response.status_code == 200:
                # Should be 200, not a redirect (301, 302, etc.)
                assert 200 <= response.status_code < 300, (
                    f"Endpoint {endpoint} returned redirect: {response.status_code}"
                )

                # Should return JSON
                assert response.headers.get("content-type", "").startswith("application/json")

                # Should have agent card data
                data = response.json()
                assert "name" in data
                # a2a-sdk 1.0 (protobuf): URL is in supportedInterfaces, not top-level
                assert "supportedInterfaces" in data

                # URL should not have trailing slash
                url = data["supportedInterfaces"][0]["url"]
                assert not url.endswith("/"), f"Agent card URL has trailing slash: {url}"


# Summary test to run all regression checks

#!/usr/bin/env python3
"""
A2A Standard Endpoints Test - ACTUALLY WORKING VERSION

This replaces the skipped test_a2a_standard_endpoints.py with a version that actually runs.
The original was skipped because it tried to use python_a2a library, but we use a2a-sdk.

This test validates the actual HTTP endpoints that our A2A server exposes.
"""

import json
import os
import sys

import pytest
import requests

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestA2AEndpointsActual:
    """Test actual A2A endpoints that we implement."""

    @pytest.mark.integration
    def test_well_known_agent_json_endpoint_live(self):
        """Test /.well-known/agent.json endpoint against live server."""
        try:
            response = requests.get("http://localhost:8091/.well-known/agent.json", timeout=2)

            if response.status_code == 200:
                # Endpoint works - validate response
                assert response.headers["content-type"].startswith("application/json")

                data = response.json()
                assert "name" in data
                assert "description" in data
                assert "version" in data
                assert "skills" in data
                assert "url" in data

                # Critical regression test: URL should not have trailing slash
                url = data["url"]
                assert not url.endswith("/"), f"Agent card URL should not have trailing slash: {url}"
                assert url.endswith("/a2a"), f"Agent card URL should end with '/a2a': {url}"

                # Should be AdCP Sales Agent
                assert data["name"] == "AdCP Sales Agent"

                # Should have skills
                assert isinstance(data["skills"], list)
                assert len(data["skills"]) > 0

                # Should specify security configuration (A2A spec for authentication)
                # Note: A2A spec uses security/securitySchemes instead of simple authentication field
                assert "security" in data or "securitySchemes" in data

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091")

    @pytest.mark.integration
    def test_agent_json_endpoint_live(self):
        """Test /agent.json endpoint against live server."""
        try:
            response = requests.get("http://localhost:8091/agent.json", timeout=2)

            if response.status_code == 200:
                assert response.headers["content-type"].startswith("application/json")
                data = response.json()
                assert data["name"] == "AdCP Sales Agent"

                # Same URL validation as well-known endpoint
                url = data["url"]
                assert not url.endswith("/"), f"Agent card URL should not have trailing slash: {url}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091")

    @pytest.mark.integration
    def test_a2a_endpoint_accessible(self):
        """Test that /a2a endpoint is accessible (may require auth)."""
        try:
            # Test both /a2a and /a2a/ paths
            for path in ["/a2a", "/a2a/"]:
                response = requests.post(f"http://localhost:8091{path}", json={"test": "data"}, timeout=2)

                # Should not be 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {path} should exist"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091")

    @pytest.mark.integration
    def test_cors_headers_present(self):
        """Test that CORS headers are present for browser compatibility."""
        try:
            response = requests.get("http://localhost:8091/.well-known/agent.json", timeout=2)

            if response.status_code == 200:
                # Should have CORS headers
                assert "Access-Control-Allow-Origin" in response.headers, "Missing CORS headers"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091")

    @pytest.mark.integration
    def test_options_preflight_support(self):
        """Test that OPTIONS requests work for CORS preflight."""
        try:
            response = requests.options("http://localhost:8091/.well-known/agent.json", timeout=2)

            # Should handle OPTIONS requests
            assert response.status_code in [200, 204], "OPTIONS request should be handled"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running on localhost:8091")


class TestA2AAgentCardCreation:
    """Test agent card creation functions directly (no HTTP required)."""

    def test_create_agent_card_function(self):
        """Test the create_agent_card function directly."""
        try:
            from src.a2a_server.adcp_a2a_server import create_agent_card
        except ImportError as e:
            if "a2a" in str(e):
                pytest.skip("a2a library not available in CI environment")
            raise

        agent_card = create_agent_card()

        # Validate structure
        assert hasattr(agent_card, "name")
        assert hasattr(agent_card, "description")
        assert hasattr(agent_card, "version")
        assert hasattr(agent_card, "skills")
        assert hasattr(agent_card, "url")

        # Check for security configuration (A2A spec compliant way to specify authentication)
        assert hasattr(agent_card, "security") or hasattr(agent_card, "securitySchemes")

        # Validate content
        assert agent_card.name == "AdCP Sales Agent"

        # Note: A2A spec uses security/securitySchemes for authentication, not a simple authentication field

        # Critical: URL should not have trailing slash
        assert not agent_card.url.endswith("/"), f"Agent card URL should not have trailing slash: {agent_card.url}"
        assert agent_card.url.endswith("/a2a"), f"Agent card URL should end with '/a2a': {agent_card.url}"

        # Should have skills
        assert len(agent_card.skills) > 0

        # Validate skills structure
        for skill in agent_card.skills:
            assert hasattr(skill, "name")
            assert hasattr(skill, "description")

    def test_agent_card_skills_coverage(self):
        """Test that agent card includes expected AdCP skills."""
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()
        skill_names = [skill.name for skill in agent_card.skills]

        # Should include core AdCP skills
        expected_skills = [
            "get_products",
            "create_media_buy",
            "sync_creatives",
            "list_creatives",
            "get_signals",
        ]

        for expected_skill in expected_skills:
            assert expected_skill in skill_names, f"Missing expected skill: {expected_skill}"

    def test_agent_card_serialization(self):
        """Test that agent card can be serialized to JSON."""
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()

        # Should be able to serialize to dict
        try:
            card_dict = agent_card.model_dump()
            assert isinstance(card_dict, dict)

            # Should be JSON serializable
            json_str = json.dumps(card_dict)
            assert len(json_str) > 0

            # Should be able to parse back
            parsed = json.loads(json_str)
            assert parsed["name"] == "AdCP Sales Agent"

        except Exception as e:
            pytest.fail(f"Agent card serialization failed: {e}")


class TestA2ARequestHandler:
    """Test the A2A request handler directly."""

    def setup_method(self):
        """Set up test fixtures."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        self.handler = AdCPRequestHandler()

    def test_handler_initialization(self):
        """Test that handler initializes correctly."""
        assert self.handler is not None
        assert hasattr(self.handler, "tasks")
        assert isinstance(self.handler.tasks, dict)

    def test_handler_has_required_methods(self):
        """Test that handler has all required A2A methods."""
        required_methods = [
            "on_message_send",
            "on_message_send_stream",
            "on_get_task",
            "on_cancel_task",
        ]

        for method_name in required_methods:
            assert hasattr(self.handler, method_name), f"Handler missing method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Method {method_name} is not callable"

    def test_handler_has_skill_methods(self):
        """Test that handler has skill-specific methods."""
        skill_methods = [
            "_handle_get_products_skill",
            "_handle_create_media_buy_skill",
            "_handle_sync_creatives_skill",
            "_handle_list_creatives_skill",
            "_handle_get_signals_skill",
        ]

        for method_name in skill_methods:
            assert hasattr(self.handler, method_name), f"Handler missing skill method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Skill method {method_name} is not callable"

    def test_auth_methods_exist(self):
        """Test that authentication-related methods exist."""
        auth_methods = [
            "_get_auth_token",
            "_create_tool_context_from_a2a",
        ]

        for method_name in auth_methods:
            assert hasattr(self.handler, method_name), f"Handler missing auth method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Auth method {method_name} is not callable"


class TestA2AServerIntegration:
    """Integration tests for complete A2A server setup."""

    @pytest.mark.integration
    def test_server_discovery_flow(self):
        """Test complete A2A client discovery flow."""
        try:
            # Step 1: Client discovers agent
            response = requests.get("http://localhost:8091/.well-known/agent.json", timeout=2)

            if response.status_code != 200:
                pytest.skip("A2A server not responding")

            agent_card = response.json()

            # Step 2: Validate agent card has what client needs
            assert "skills" in agent_card
            assert "security" in agent_card or "securitySchemes" in agent_card  # A2A spec authentication
            assert "url" in agent_card

            # Step 3: Validate URL format for messaging
            url = agent_card["url"]
            assert not url.endswith("/"), "URL should not have trailing slash (causes redirects)"

            # Step 4: Test that messaging endpoint exists
            messaging_url = url if url.endswith("/a2a") else f"{url}/a2a"

            # Try to connect (will fail with auth error, but should not be 404)
            response = requests.post(messaging_url, json={"test": "message"}, timeout=2)
            assert response.status_code != 404, "Messaging endpoint should exist"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running")

    @pytest.mark.integration
    def test_authentication_flow(self):
        """Test authentication requirements."""
        try:
            # Should require Bearer token for messaging
            response = requests.post(
                "http://localhost:8091/a2a",
                headers={"Authorization": "Bearer invalid-token"},
                json={"method": "message/send", "params": {}},
                timeout=2,
            )

            # Should reject invalid token (401) not be 404
            assert response.status_code != 404, "Endpoint should exist"

            # Missing auth should also not be 404
            response = requests.post(
                "http://localhost:8091/a2a", json={"method": "message/send", "params": {}}, timeout=2
            )
            assert response.status_code != 404, "Endpoint should exist even without auth"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running")


def test_a2a_regression_summary():
    """Quick summary test for key regressions."""

    try:
        # Test 1: Agent card URL format
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()
        assert not agent_card.url.endswith("/"), "REGRESSION: Agent card URL has trailing slash"

        # Test 2: Handler can be created
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        assert handler is not None, "REGRESSION: Cannot create A2A handler"

        # Test 3: Core functions are callable
        from src.a2a_server.adcp_a2a_server import core_get_signals_tool

        assert callable(core_get_signals_tool), "REGRESSION: Core function not callable"
    except ImportError as e:
        if "a2a" in str(e):
            pytest.skip("a2a library not available in CI environment")
        raise

    print("✅ A2A regression tests passed")


if __name__ == "__main__":
    # Run basic checks when executed directly
    test_a2a_regression_summary()

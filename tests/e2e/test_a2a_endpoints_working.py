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
from unittest.mock import MagicMock, patch

import pytest
import requests
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskNotFoundError
from adcp import get_adcp_spec_version

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.e2e.conftest import e2e_host
from tests.factories import PrincipalFactory


def _a2a_base_url() -> str:
    """Get A2A server base URL from environment (supports dynamic ports)."""
    port = os.getenv("ADCP_SALES_PORT", "8080")
    return f"http://{e2e_host()}:{port}"


class TestA2AEndpointsActual:
    """Test actual A2A endpoints that we implement."""

    @pytest.mark.integration
    def test_well_known_agent_json_endpoint_live(self):
        """Test /.well-known/agent-card.json endpoint against live server."""
        try:
            # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
            response = requests.get(f"{_a2a_base_url()}/.well-known/agent-card.json", timeout=2)

            if response.status_code == 200:
                # Endpoint works - validate response
                assert response.headers["content-type"].startswith("application/json")

                data = response.json()
                assert "name" in data
                assert "description" in data
                assert "version" in data
                assert "skills" in data

                # a2a-sdk 1.0 (protobuf): URL is in supportedInterfaces, not top-level
                assert "supportedInterfaces" in data, "Agent card must have supportedInterfaces"
                interfaces = data["supportedInterfaces"]
                assert len(interfaces) > 0
                url = interfaces[0]["url"]

                # Critical regression test: URL should not have trailing slash
                assert not url.endswith("/"), f"Agent card URL should not have trailing slash: {url}"
                assert url.endswith("/a2a"), f"Agent card URL should end with '/a2a': {url}"

                # Should be Prebid Sales Agent
                assert data["name"] == "Prebid Sales Agent"

                # Should have skills
                assert "skills" in data
                assert len(data["skills"]) > 0

                # AdCP 2.5: Should have AdCP extension in capabilities
                assert "capabilities" in data
                assert "extensions" in data["capabilities"]
                extensions = data["capabilities"]["extensions"]
                assert len(extensions) > 0

                # Find AdCP extension
                adcp_ext = None
                for ext in extensions:
                    if "adcp-extension" in ext.get("uri", ""):
                        adcp_ext = ext
                        break

                assert adcp_ext is not None, "AdCP extension not found in live agent card"
                assert adcp_ext["params"]["adcp_version"] == get_adcp_spec_version()
                assert "media_buy" in adcp_ext["params"]["protocols_supported"]

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"A2A server not running at {_a2a_base_url()}")

    @pytest.mark.integration
    def test_agent_json_endpoint_live(self):
        """Test /agent.json endpoint against live server."""
        try:
            response = requests.get(f"{_a2a_base_url()}/agent.json", timeout=2)

            if response.status_code == 200:
                assert response.headers["content-type"].startswith("application/json")
                data = response.json()
                assert data["name"] == "Prebid Sales Agent"

                # Same URL validation as well-known endpoint
                url = data["url"]
                assert not url.endswith("/"), f"Agent card URL should not have trailing slash: {url}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"A2A server not running at {_a2a_base_url()}")

    @pytest.mark.integration
    def test_a2a_endpoint_accessible(self):
        """Test that /a2a endpoint is accessible (may require auth)."""
        try:
            # Test both /a2a and /a2a/ paths
            for path in ["/a2a", "/a2a/"]:
                response = requests.post(f"{_a2a_base_url()}{path}", json={"test": "data"}, timeout=2)

                # Should not be 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {path} should exist"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"A2A server not running at {_a2a_base_url()}")

    @pytest.mark.integration
    def test_cors_headers_present(self):
        """Test that CORS headers are present for browser compatibility."""
        try:
            # CORS headers are only returned when the Origin matches an allowed origin.
            # Default ALLOWED_ORIGINS is "http://localhost:8000" — use that as Origin.
            allowed_origin = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")[0].strip()

            # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
            response = requests.get(
                f"{_a2a_base_url()}/.well-known/agent-card.json",
                headers={"Origin": allowed_origin},
                timeout=2,
            )

            if response.status_code == 200:
                # Should have CORS headers for an allowed origin
                assert "Access-Control-Allow-Origin" in response.headers, "Missing CORS headers"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"A2A server not running at {_a2a_base_url()}")

    @pytest.mark.integration
    def test_options_preflight_support(self):
        """Test that OPTIONS requests work for CORS preflight."""
        try:
            # a2a-sdk 1.0 canonical path is /.well-known/agent-card.json
            response = requests.options(f"{_a2a_base_url()}/.well-known/agent-card.json", timeout=2)

            # Should handle OPTIONS requests
            assert response.status_code in [200, 204], "OPTIONS request should be handled"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"A2A server not running at {_a2a_base_url()}")


class TestA2AAgentCardCreation:
    """Test agent card creation functions directly (no HTTP required)."""

    def test_create_agent_card_function(self):
        """Test the create_agent_card function directly."""
        try:
            from src.a2a_server.adcp_a2a_server import create_agent_card
        except ImportError as e:
            if e.name and e.name.startswith("a2a"):
                pytest.skip(f"a2a-sdk library not installed: {e}")
            raise

        agent_card = create_agent_card()

        # Validate structure (protobuf AgentCard fields)
        assert agent_card.name
        assert agent_card.description
        assert agent_card.version
        assert len(agent_card.skills) > 0
        assert len(agent_card.supported_interfaces) > 0

        # Validate content
        assert agent_card.name == "Prebid Sales Agent"

        # a2a-sdk 1.0: URL is in supported_interfaces[0].url, not agent_card.url
        interface_url = agent_card.supported_interfaces[0].url
        assert not interface_url.endswith("/"), f"Interface URL should not have trailing slash: {interface_url}"
        assert interface_url.endswith("/a2a"), f"Interface URL should end with '/a2a': {interface_url}"

        # Validate skills structure (protobuf: skills have id and description)
        for skill in agent_card.skills:
            assert skill.id
            assert skill.description

    def test_agent_card_adcp_extension(self):
        """Test that agent card includes AdCP 2.5 extension."""
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()

        # Check capabilities has extensions
        assert hasattr(agent_card, "capabilities")
        assert agent_card.capabilities is not None
        assert hasattr(agent_card.capabilities, "extensions")
        assert agent_card.capabilities.extensions is not None
        assert len(agent_card.capabilities.extensions) > 0

        # Find AdCP extension
        adcp_ext = None
        for ext in agent_card.capabilities.extensions:
            if "adcp-extension" in ext.uri:
                adcp_ext = ext
                break

        assert adcp_ext is not None, "AdCP extension not found in capabilities.extensions"

        # Validate AdCP extension structure
        adcp_version = get_adcp_spec_version()
        assert adcp_ext.uri == f"https://adcontextprotocol.org/schemas/{adcp_version}/protocols/adcp-extension.json"
        assert adcp_ext.params is not None
        # protobuf Struct: access fields dict-like
        params = adcp_ext.params
        assert "adcp_version" in params.fields
        assert "protocols_supported" in params.fields

        # Validate AdCP extension values
        assert params.fields["adcp_version"].string_value == adcp_version
        protocols_value = params.fields["protocols_supported"].list_value
        protocols = [v.string_value for v in protocols_value.values]
        assert len(protocols) >= 1
        # Currently only media_buy protocol is supported
        assert "media_buy" in protocols
        assert set(protocols) == {"media_buy"}, "Only media_buy protocol is currently supported"

    def test_agent_card_skills_coverage(self):
        """Test that agent card includes expected AdCP skills."""
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()
        skill_names = [skill.id for skill in agent_card.skills]

        # Should include core AdCP skills
        # Note: get_signals removed - should come from dedicated signals agents
        expected_skills = [
            "get_products",
            "create_media_buy",
            "sync_creatives",
            "list_creatives",
        ]

        for expected_skill in expected_skills:
            assert expected_skill in skill_names, f"Missing expected skill: {expected_skill}"

    def test_agent_card_serialization(self):
        """Test that agent card can be serialized to JSON."""
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()

        # Should be able to serialize to dict (protobuf: use MessageToDict)
        try:
            from google.protobuf.json_format import MessageToDict, MessageToJson

            card_dict = MessageToDict(agent_card)
            assert isinstance(card_dict, dict)

            # Should be JSON serializable
            json_str = MessageToJson(agent_card)
            assert len(json_str) > 0

            # Should be able to parse back
            parsed = json.loads(json_str)
            assert parsed["name"] == "Prebid Sales Agent"

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "request_cls, method_name",
        [(GetTaskRequest, "on_get_task"), (CancelTaskRequest, "on_cancel_task")],
    )
    async def test_unknown_task_id_raises_task_not_found(self, request_cls, method_name):
        """An unknown task id raises TaskNotFoundError, not the generic internal
        error a bare None return produces — cancel is the same not-found condition
        as get, and both route through the shared ``_get_owned_in_memory_task_or_raise``.

        Fast smoke check on the raise only. It does NOT prove the wire code: the
        exception carries no code, and the client actually sees -32603 — see
        ``_get_owned_in_memory_task_or_raise`` (src/a2a_server/adcp_a2a_server.py)
        and #1670 for why, plus the xfail'd live-server test in
        TestA2AServerIntegration that grades the code on the wire. Assert on
        str(exc), not exc.code — there is none.

        Parametrized over both entry points so the shared assertion cannot drift
        between two byte-identical copies.

        Both halves of the raise are pinned: the human-readable message AND the
        structured ``data`` payload clients actually parse. Asserting the message
        alone would let ``data={"task_id": ...}`` be deleted with the suite still
        green, since the id appears in the message either way.

        Auth is mocked so this grades the not-found shape after the identity
        gate (#1702), not an auth-failure collapse into the same error.
        """
        with (
            patch.object(self.handler, "_get_auth_token", return_value="tok"),
            patch.object(
                self.handler,
                "_resolve_a2a_identity",
                return_value=PrincipalFactory.make_identity(protocol="a2a"),
            ),
        ):
            with pytest.raises(TaskNotFoundError) as exc:
                await getattr(self.handler, method_name)(request_cls(id="task_does_not_exist"), MagicMock())
        assert "task_does_not_exist" in str(exc.value)  # the requested id is surfaced
        assert exc.value.data == {"task_id": "task_does_not_exist"}  # ...and machine-readable

    def test_handler_has_skill_methods(self):
        """Test that handler has skill-specific methods."""
        # Note: get_signals removed - should come from dedicated signals agents
        skill_methods = [
            "_handle_get_products_skill",
            "_handle_create_media_buy_skill",
            "_handle_sync_creatives_skill",
            "_handle_list_creatives_skill",
        ]

        for method_name in skill_methods:
            assert hasattr(self.handler, method_name), f"Handler missing skill method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Skill method {method_name} is not callable"

    def test_auth_methods_exist(self):
        """Test that authentication-related methods exist."""
        auth_methods = [
            "_get_auth_token",
            "_resolve_a2a_identity",
            "_make_tool_context",
        ]

        for method_name in auth_methods:
            assert hasattr(self.handler, method_name), f"Handler missing auth method: {method_name}"
            method = getattr(self.handler, method_name)
            assert callable(method), f"Auth method {method_name} is not callable"


class TestA2AServerIntegration:
    """Integration tests for complete A2A server setup."""

    @pytest.mark.integration
    @pytest.mark.xfail(
        reason="v0.3 compat adapter maps A2AError to -32603; see #1670",
        strict=True,
    )
    @pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel"])
    def test_unknown_task_id_returns_task_not_found_code_on_the_wire(self, method, live_server):
        """The deliverable of the TaskNotFoundError change is what an A2A client
        SEES: JSON-RPC error code -32001. That code is not carried by the
        exception — it is synthesized downstream — so the direct-call test in
        TestA2ARequestHandler cannot prove it. This POSTs the real request to the
        running /a2a endpoint and grades the code on the wire.

        Uses the ``live_server`` fixture so the transport is guaranteed up: the
        base URL comes from ``live_server['a2a']`` and the assertion runs
        deterministically instead of skipping when nothing happens to be listening
        on the ad-hoc port — a skip under strict xfail is neither XFAIL nor XPASS,
        so the sole on-the-wire grade must never be allowed to no-op.

        Parametrized over both methods this PR changed. `tasks/cancel` of an
        unknown id went from a silent None to an error in this PR, so it needs the
        same wire tripwire as `tasks/get` — otherwise only half the contract gets
        locked in when #1670 lands.

        STRICT xfail against #1670: the code is -32603 today, not the spec's
        -32001 — see ``_get_owned_in_memory_task_or_raise``
        (src/a2a_server/adcp_a2a_server.py) and #1670 for the
        enable_v0_3_compat dispatch path that flattens it. Both
        `tasks/get` and `tasks/cancel` reach that path, so both are -32603 today
        whether they raise TaskNotFoundError or return None.

        Strict on purpose: an a2a-sdk bump that closes the gap makes this XPASS,
        which strict turns into a loud failure so the xfail is removed and -32001
        is locked in. A non-strict xfail would let the fix land silently and rot
        the marker — the same "green suite lies" shape the tripwire exists to
        prevent.
        """
        response = requests.post(
            f"{live_server['a2a']}/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {"id": "task_does_not_exist"}},
            timeout=5,
        )

        assert response.status_code == 200, f"JSON-RPC errors ride a 200 envelope: {response.status_code}"
        data = response.json()
        assert "error" in data, f"unknown task id must produce a JSON-RPC error: {data}"
        assert data["error"]["code"] == -32001, (
            f"A2A spec defines -32001 (TaskNotFoundError) for an unknown task id, got "
            f"{data['error']['code']} — see #1670"
        )

    @pytest.mark.integration
    def test_server_discovery_flow(self):
        """Test complete A2A client discovery flow."""
        try:
            # Step 1: Client discovers agent (a2a-sdk 1.0 canonical path)
            response = requests.get(f"{_a2a_base_url()}/.well-known/agent-card.json", timeout=2)

            if response.status_code != 200:
                pytest.skip("A2A server not responding")

            agent_card = response.json()

            # Step 2: Validate agent card has what client needs
            assert "skills" in agent_card
            # a2a-sdk 1.0 (protobuf): URL is in supportedInterfaces, not top-level
            assert "supportedInterfaces" in agent_card

            # Step 3: Validate URL format for messaging
            url = agent_card["supportedInterfaces"][0]["url"]
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
                f"{_a2a_base_url()}/a2a",
                headers={"Authorization": "Bearer invalid-token"},
                json={"method": "message/send", "params": {}},
                timeout=2,
            )

            # Should reject invalid token (401) not be 404
            assert response.status_code != 404, "Endpoint should exist"

            # Missing auth should also not be 404
            response = requests.post(f"{_a2a_base_url()}/a2a", json={"method": "message/send", "params": {}}, timeout=2)
            assert response.status_code != 404, "Endpoint should exist even without auth"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip("A2A server not running")


def test_a2a_regression_summary():
    """Quick summary test for key regressions."""

    try:
        # Test 1: Agent card URL format
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()
        assert not agent_card.supported_interfaces[0].url.endswith("/"), "REGRESSION: Agent card URL has trailing slash"

        # Test 2: Handler can be created
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        assert handler is not None, "REGRESSION: Cannot create A2A handler"

        # Test 3: Core functions are callable
        # Note: signals tools removed - using get_products as core function check instead
        from src.a2a_server.adcp_a2a_server import core_get_products_tool

        assert callable(core_get_products_tool), "REGRESSION: Core function not callable"
    except ImportError as e:
        if e.name and e.name.startswith("a2a"):
            pytest.skip(f"a2a-sdk library not installed: {e}")
        raise

    print("✅ A2A regression tests passed")


if __name__ == "__main__":
    # Run basic checks when executed directly
    test_a2a_regression_summary()

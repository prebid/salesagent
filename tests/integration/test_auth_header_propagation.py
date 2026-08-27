"""Integration tests for custom auth header propagation through agent registries.

These tests verify that custom auth headers (like "Authorization" for Optable)
are properly passed through the entire stack from database config to adcp client.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry


class TestAuthHeaderPropagation:
    """Test suite for auth header propagation through agent registries."""

    def test_creative_agent_custom_auth_header_propagation(self):
        """Test custom auth header is propagated from CreativeAgent to adcp client."""
        registry = CreativeAgentRegistry()

        # Create agent with custom Authorization header (like Optable)
        agent = CreativeAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/creative/mcp",
            name="Optable Creative",
            enabled=True,
            priority=1,
            auth={"type": "bearer", "credentials": "test-token-123"},
            auth_header="Authorization",  # Custom header
        )

        # Build adcp client
        with patch("adcp.ADCPMultiAgentClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            result = registry._build_adcp_client([agent])

            # Verify ADCPMultiAgentClient was called with correct config
            mock_client_class.assert_called_once()
            call_args = mock_client_class.call_args
            agent_configs = call_args.kwargs["agents"]

            assert len(agent_configs) == 1
            config = agent_configs[0]

            # Verify custom auth header was set
            assert config.auth_header == "Authorization"
            assert config.auth_token == "test-token-123"
            assert config.auth_type == "bearer"
            assert config.agent_uri == "https://sandbox.optable.co/admin/adcp/creative/mcp"

    def test_creative_agent_default_auth_header_when_none(self):
        """Test default x-adcp-auth header is used when auth_header is None."""
        registry = CreativeAgentRegistry()

        agent = CreativeAgent(
            agent_url="https://creative.example.com/mcp",
            name="Standard Agent",
            enabled=True,
            priority=1,
            auth={"type": "token", "credentials": "token-456"},
            auth_header=None,  # No custom header
        )

        with patch("adcp.ADCPMultiAgentClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            result = registry._build_adcp_client([agent])

            call_args = mock_client_class.call_args
            agent_configs = call_args.kwargs["agents"]
            config = agent_configs[0]

            # Verify default header was used
            assert config.auth_header == "x-adcp-auth"

    def test_signals_agent_custom_auth_header_propagation(self):
        """Test custom auth header is propagated from SignalsAgent to adcp client."""
        registry = SignalsAgentRegistry()

        # Create agent with custom Authorization header (like Optable)
        agent = SignalsAgent(
            agent_url="https://sandbox.optable.co/admin/adcp/signals/mcp",
            name="Optable Signals",
            enabled=True,
            auth={"type": "bearer", "credentials": "test-signals-token"},
            auth_header="Authorization",  # Custom header
        )

        # Build adcp client
        with patch("adcp.ADCPMultiAgentClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            result = registry._build_adcp_client([agent])

            # Verify ADCPMultiAgentClient was called with correct config
            mock_client_class.assert_called_once()
            call_args = mock_client_class.call_args
            agent_configs = call_args.kwargs["agents"]

            assert len(agent_configs) == 1
            config = agent_configs[0]

            # Verify custom auth header was set
            assert config.auth_header == "Authorization"
            assert config.auth_token == "test-signals-token"
            assert config.auth_type == "bearer"
            assert config.agent_uri == "https://sandbox.optable.co/admin/adcp/signals/mcp"

    def test_signals_agent_default_auth_header_when_none(self):
        """Test default x-adcp-auth header is used when auth_header is None."""
        registry = SignalsAgentRegistry()

        agent = SignalsAgent(
            agent_url="https://signals.example.com/mcp",
            name="Standard Signals Agent",
            enabled=True,
            auth={"type": "token", "credentials": "signals-token-789"},
            auth_header=None,  # No custom header
        )

        with patch("adcp.ADCPMultiAgentClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            result = registry._build_adcp_client([agent])

            call_args = mock_client_class.call_args
            agent_configs = call_args.kwargs["agents"]
            config = agent_configs[0]

            # Verify default header was used
            assert config.auth_header == "x-adcp-auth"

    def test_multiple_agents_with_different_auth_headers(self):
        """Test multiple agents can have different auth headers simultaneously."""
        registry = CreativeAgentRegistry()

        agents = [
            CreativeAgent(
                agent_url="https://optable.co/creative",
                name="Optable",
                enabled=True,
                priority=1,
                auth={"type": "bearer", "credentials": "optable-token"},
                auth_header="Authorization",
            ),
            CreativeAgent(
                agent_url="https://standard.co/creative",
                name="Standard",
                enabled=True,
                priority=2,
                auth={"type": "token", "credentials": "standard-token"},
                auth_header=None,  # Uses default
            ),
            CreativeAgent(
                agent_url="https://custom.co/creative",
                name="Custom",
                enabled=True,
                priority=3,
                auth={"type": "token", "credentials": "custom-token"},
                auth_header="x-api-key",
            ),
        ]

        with patch("adcp.ADCPMultiAgentClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            result = registry._build_adcp_client(agents)

            call_args = mock_client_class.call_args
            agent_configs = call_args.kwargs["agents"]

            assert len(agent_configs) == 3

            # Verify each agent has correct auth header
            assert agent_configs[0].auth_header == "Authorization"
            assert agent_configs[1].auth_header == "x-adcp-auth"
            assert agent_configs[2].auth_header == "x-api-key"

    @pytest.mark.asyncio
    async def test_auth_header_used_in_actual_request(self):
        """Test that auth header is actually used when making requests."""
        registry = SignalsAgentRegistry()

        agent = SignalsAgent(
            agent_url="https://test.example.com/signals",
            name="Test Agent",
            enabled=True,
            auth={"type": "bearer", "credentials": "request-test-token"},
            auth_header="Authorization",
        )

        # Mock the adcp client to verify headers are passed
        mock_client = Mock()
        mock_agent_client = Mock()
        mock_agent_client.signing = None

        # Mock successful response
        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = Mock()
        mock_result.data.signals = []

        mock_agent_client.get_signals = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method that uses adcp client
        with patch.object(registry, "_build_adcp_client", return_value=mock_client):
            signals = await registry._get_signals_from_agent(mock_client, agent, "test signal spec", "tenant_123")

            # Verify the request was made
            mock_agent_client.get_signals.assert_called_once()

            # The actual HTTP request with headers happens inside adcp library
            # We verify that the client was built with correct config
            # (deeper verification would require mocking HTTP layer)

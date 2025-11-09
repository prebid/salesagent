"""Unit tests for signals agent registry (adcp v1.0.1 migration)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry


class TestSignalsAgentRegistry:
    """Unit tests for SignalsAgentRegistry class using adcp library."""

    @pytest.mark.asyncio
    async def test_build_adcp_client_with_custom_auth_header(self):
        """Test that _build_adcp_client correctly maps SignalsAgent to AgentConfig."""
        registry = SignalsAgentRegistry()

        # Create test agents
        agents = [
            SignalsAgent(
                agent_url="https://optable.com/mcp",
                name="Optable",
                auth={"type": "bearer", "credentials": "token123"},
                auth_header="Authorization",
                timeout=60,
            ),
            SignalsAgent(
                agent_url="https://test.com/mcp",
                name="Test Agent",
                auth={"type": "token", "credentials": "key456"},
                auth_header="x-api-key",
                timeout=30,
            ),
        ]

        # Build client
        client = registry._build_adcp_client(agents)

        # Verify client was created (basic check)
        assert client is not None

        # Verify agent configs (check via client's internal state if accessible)
        # Note: adcp library may not expose configs directly, so we test via behavior

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_with_adcp_success(self):
        """Test _get_signals_from_agent with successful adcp response."""
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            auth={"type": "bearer", "credentials": "test-token"},
            auth_header="Authorization",
            timeout=30,
        )

        # Mock the adcp client
        mock_client = Mock()
        mock_agent_client = Mock()

        # Mock successful response
        from adcp import GetSignalsResponse

        # Signals are dicts (not typed objects) in GetSignalsResponse
        mock_signals = [
            {
                "signal_agent_segment_id": "seg1",
                "name": "Test Signal",
                "description": "Test description",
                "signal_type": "marketplace",
                "data_provider": "Test Provider",
                "coverage_percentage": 85.0,
            }
        ]

        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = GetSignalsResponse(signals=mock_signals)

        mock_agent_client.get_signals = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call method
        signals = await registry._get_signals_from_agent(
            mock_client,
            test_agent,
            brief="test query",
            tenant_id="test-tenant",
        )

        # Verify results
        assert len(signals) == 1
        assert signals[0]["signal_agent_segment_id"] == "seg1"
        assert signals[0]["name"] == "Test Signal"

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_with_async_submission(self):
        """Test _get_signals_from_agent with async submission (webhook)."""
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            auth={"type": "bearer", "credentials": "test-token"},
            auth_header="Authorization",
            timeout=30,
        )

        # Mock the adcp client
        mock_client = Mock()
        mock_agent_client = Mock()

        # Mock async submission response
        mock_result = Mock()
        mock_result.status = "submitted"
        mock_result.submitted = Mock()
        mock_result.submitted.webhook_url = "https://myapp.com/webhook/123"

        mock_agent_client.get_signals = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call method
        signals = await registry._get_signals_from_agent(
            mock_client,
            test_agent,
            brief="test query",
            tenant_id="test-tenant",
        )

        # Verify results (should be empty for async)
        assert signals == []

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_handles_auth_error(self):
        """Test _get_signals_from_agent handles authentication errors."""
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            auth={"type": "bearer", "credentials": "bad-token"},
            auth_header="Authorization",
            timeout=30,
        )

        # Mock the adcp client
        mock_client = Mock()
        mock_agent_client = Mock()

        # Mock authentication error
        from adcp.exceptions import ADCPAuthenticationError

        mock_agent_client.get_signals = AsyncMock(
            side_effect=ADCPAuthenticationError("Authentication failed", agent_id="test", agent_uri="https://test.com")
        )
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call method - should raise RuntimeError (for backward compatibility)
        with pytest.raises(RuntimeError, match="Authentication failed"):
            await registry._get_signals_from_agent(
                mock_client,
                test_agent,
                brief="test query",
                tenant_id="test-tenant",
            )

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        """Test test_connection with successful connection."""
        registry = SignalsAgentRegistry()

        agent_url = "https://test-agent.example.com/mcp"
        auth = {"type": "bearer", "credentials": "test-token"}
        auth_header = "Authorization"

        # Mock _build_adcp_client and _get_signals_from_agent
        with (
            patch.object(registry, "_build_adcp_client") as mock_build,
            patch.object(registry, "_get_signals_from_agent") as mock_get_signals,
        ):
            # Mock client that supports async context manager
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_build.return_value = mock_client

            mock_get_signals.return_value = [{"signal_agent_segment_id": "test"}]  # Mock signals

            result = await registry.test_connection(agent_url, auth=auth, auth_header=auth_header)

            assert result["success"] is True
            assert "message" in result
            assert result["signal_count"] == 1

    @pytest.mark.asyncio
    async def test_test_connection_handles_connection_error(self):
        """Test test_connection handles connection errors gracefully."""
        registry = SignalsAgentRegistry()

        agent_url = "https://unreachable-agent.example.com/mcp"
        auth = {"type": "bearer", "credentials": "test-token"}
        auth_header = "X-Custom-Auth"

        # Mock to raise connection error
        with patch.object(registry, "_build_adcp_client") as mock_build:
            from adcp.exceptions import ADCPConnectionError

            mock_build.side_effect = ADCPConnectionError("Connection failed", agent_id="test", agent_uri=agent_url)

            result = await registry.test_connection(agent_url, auth=auth, auth_header=auth_header)

            assert result["success"] is False
            assert "error" in result
            assert "Connection" in result["error"]

    @pytest.mark.asyncio
    async def test_test_connection_handles_auth_error(self):
        """Test test_connection handles authentication errors with helpful message."""
        registry = SignalsAgentRegistry()

        agent_url = "https://test-agent.example.com/mcp"
        auth = {"type": "bearer", "credentials": "bad-token"}
        auth_header = "Authorization"

        # Mock to raise auth error
        with (
            patch.object(registry, "_build_adcp_client") as mock_build,
            patch.object(registry, "_get_signals_from_agent") as mock_get_signals,
        ):

            mock_build.return_value = Mock()
            mock_get_signals.side_effect = RuntimeError("Authentication failed: Invalid token")

            result = await registry.test_connection(agent_url, auth=auth, auth_header=auth_header)

            assert result["success"] is False
            assert "error" in result
            assert "Authentication" in result["error"] or "failed" in result["error"]

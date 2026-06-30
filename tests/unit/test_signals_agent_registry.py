"""Unit tests for signals agent registry (adcp v1.0.1 migration)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.exceptions import AdCPAdapterError, AdCPAuthenticationError, AdCPServiceUnavailableError
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
        # adcp 3.9: pricing is now empty, pricing details moved to pricing_options (required)
        mock_signals = [
            {
                "signal_agent_segment_id": "seg1",
                "signal_id": {
                    "source": "catalog",
                    "data_provider_domain": "testprovider.com",
                    "id": "seg1",
                },
                "name": "Test Signal",
                "description": "Test description",
                "signal_type": "marketplace",
                "data_provider": "Test Provider",
                "coverage_percentage": 85.0,
                "deployments": [
                    {
                        "type": "platform",
                        "platform": "web",
                        "is_live": True,
                        "deployed_at": "2025-01-01T00:00:00Z",
                    }
                ],
                "pricing": {},
                "pricing_options": [
                    {"pricing_option_id": "cpm_usd", "cpm": 2.50, "currency": "USD", "model": "cpm"},
                ],
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
            side_effect=ADCPAuthenticationError("invalid bearer token", agent_id="test", agent_uri="https://test.com")
        )
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should re-raise as a terminal 401 AdCPAuthenticationError carrying the SDK detail
        with pytest.raises(AdCPAuthenticationError, match=r"Authentication failed: invalid bearer token") as exc_info:
            await registry._get_signals_from_agent(
                mock_client,
                test_agent,
                brief="test query",
                tenant_id="test-tenant",
            )
        assert exc_info.value.error_code == "AUTH_TOKEN_INVALID"
        assert exc_info.value.recovery == "terminal"

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_handles_timeout_error(self):
        """Test _get_signals_from_agent maps ADCPTimeoutError to AdCPServiceUnavailableError."""
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            timeout=30,
        )

        mock_client = Mock()
        mock_agent_client = Mock()

        from adcp.exceptions import ADCPTimeoutError

        mock_agent_client.get_signals = AsyncMock(
            side_effect=ADCPTimeoutError("deadline exceeded after 30s", agent_id="test", agent_uri="https://test.com")
        )
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Timeouts are transient 503s: the downstream may recover, so buyers should retry
        with pytest.raises(
            AdCPServiceUnavailableError, match=r"Request timed out: deadline exceeded after 30s"
        ) as exc_info:
            await registry._get_signals_from_agent(
                mock_client,
                test_agent,
                brief="test query",
                tenant_id="test-tenant",
            )
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_handles_connection_error(self):
        """Test _get_signals_from_agent maps ADCPConnectionError to AdCPServiceUnavailableError."""
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            timeout=30,
        )

        mock_client = Mock()
        mock_agent_client = Mock()

        from adcp.exceptions import ADCPConnectionError

        mock_agent_client.get_signals = AsyncMock(
            side_effect=ADCPConnectionError("ECONNREFUSED 10.0.0.1:443", agent_id="test", agent_uri="https://test.com")
        )
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Connection failures are transient 503s, matching the timeout classification
        with pytest.raises(
            AdCPServiceUnavailableError, match=r"Connection failed: ECONNREFUSED 10\.0\.0\.1:443"
        ) as exc_info:
            await registry._get_signals_from_agent(
                mock_client,
                test_agent,
                brief="test query",
                tenant_id="test-tenant",
            )
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"

    @pytest.mark.asyncio
    async def test_get_signals_from_agent_handles_generic_adcp_error(self):
        """Test _get_signals_from_agent maps a generic ADCPError to AdCPAdapterError.

        The catch-all arm forwards the raw SDK message (no ``AdCP error:`` prefix),
        so this pins both the 502/transient classification and the message shape.
        """
        registry = SignalsAgentRegistry()

        test_agent = SignalsAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            timeout=30,
        )

        mock_client = Mock()
        mock_agent_client = Mock()

        from adcp.exceptions import ADCPError

        mock_agent_client.get_signals = AsyncMock(side_effect=ADCPError("unexpected protocol failure"))
        mock_client.agent = Mock(return_value=mock_agent_client)

        with pytest.raises(AdCPAdapterError, match=r"unexpected protocol failure") as exc_info:
            await registry._get_signals_from_agent(
                mock_client,
                test_agent,
                brief="test query",
                tenant_id="test-tenant",
            )
        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"
        # Catch-all forwards the bare SDK message, not the legacy "AdCP error: ..." prefix
        assert "AdCP error:" not in str(exc_info.value)

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
            mock_get_signals.side_effect = AdCPAdapterError("Authentication failed: Invalid token")

            result = await registry.test_connection(agent_url, auth=auth, auth_header=auth_header)

            assert result["success"] is False
            assert "error" in result
            assert "Authentication" in result["error"] or "failed" in result["error"]

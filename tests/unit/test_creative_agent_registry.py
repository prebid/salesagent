"""Unit tests for Creative Agent Registry adcp library integration."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry


class TestCreativeAgentRegistry:
    """Test suite for Creative Agent Registry adcp integration."""

    def test_build_adcp_client_with_custom_auth_header(self):
        """Test _build_adcp_client correctly maps custom auth headers."""
        registry = CreativeAgentRegistry()

        # Test agent with custom auth header
        test_agents = [
            CreativeAgent(
                agent_url="https://test-agent.example.com/mcp",
                name="Test Agent",
                enabled=True,
                priority=1,
                auth={"type": "bearer", "credentials": "test-token-123"},
                auth_header="Authorization",  # Custom header
            )
        ]

        client = registry._build_adcp_client(test_agents)

        # Verify client was created
        assert client is not None

        # Verify agent config is correct (check via client._agents if accessible)
        # Note: We can't easily verify internal AgentConfig without accessing private attrs
        # But we can verify the method doesn't raise and returns a client
        assert hasattr(client, "agent")

    def test_build_adcp_client_with_default_auth_header(self):
        """Test _build_adcp_client uses default x-adcp-auth when no custom header."""
        registry = CreativeAgentRegistry()

        test_agents = [
            CreativeAgent(
                agent_url="https://default-agent.example.com/mcp",
                name="Default Agent",
                enabled=True,
                priority=1,
                auth={"type": "token", "credentials": "token-456"},
                auth_header=None,  # No custom header
            )
        ]

        client = registry._build_adcp_client(test_agents)

        assert client is not None
        assert hasattr(client, "agent")

    def test_build_adcp_client_with_no_auth(self):
        """Test _build_adcp_client handles agents without auth."""
        registry = CreativeAgentRegistry()

        test_agents = [
            CreativeAgent(
                agent_url="https://public-agent.example.com/mcp",
                name="Public Agent",
                enabled=True,
                priority=1,
                auth=None,
                auth_header=None,
            )
        ]

        client = registry._build_adcp_client(test_agents)

        assert client is not None

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_with_adcp_success(self):
        """Test _fetch_formats_from_agent with successful adcp response."""
        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock ADCPMultiAgentClient
        mock_client = Mock()
        mock_agent_client = Mock()

        # Mock format data as dicts (as returned by adcp library)
        # Using spec-compliant renders array for dimensions (not top-level dimensions field)
        mock_formats = [
            {
                "format_id": {"agent_url": "https://test-agent.example.com/mcp", "id": "display_300x250"},
                "name": "Display 300x250",
                "type": "display",
                "renders": [{"role": "primary", "dimensions": {"width": 300, "height": 250, "unit": "px"}}],
            },
            {
                "format_id": {"agent_url": "https://test-agent.example.com/mcp", "id": "display_728x90"},
                "name": "Display 728x90",
                "type": "display",
                "renders": [{"role": "primary", "dimensions": {"width": 728, "height": 90, "unit": "px"}}],
            },
        ]

        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = Mock()
        mock_result.data.formats = mock_formats

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method
        formats = await registry._fetch_formats_from_agent(mock_client, test_agent, max_width=1920, max_height=1080)

        # Verify results
        assert len(formats) == 2
        assert formats[0].format_id.id == "display_300x250"
        assert formats[1].format_id.id == "display_728x90"

        # Verify agent_url was set
        # Note: Can't directly check since Format is constructed, but method should set it

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_with_async_submission(self):
        """Test _fetch_formats_from_agent handles async webhook submission."""
        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock async submission response
        mock_client = Mock()
        mock_agent_client = Mock()

        mock_result = Mock()
        mock_result.status = "submitted"
        mock_result.submitted = Mock()
        mock_result.submitted.webhook_url = "https://webhook.example.com/callback"

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method
        formats = await registry._fetch_formats_from_agent(mock_client, test_agent)

        # Should return empty list for webhook submission
        assert formats == []

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_auth_error(self):
        """Test _fetch_formats_from_agent handles authentication errors."""
        from adcp.exceptions import ADCPAuthenticationError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock authentication error
        mock_client = Mock()
        mock_agent_client = Mock()

        auth_error = ADCPAuthenticationError("Invalid credentials")
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=auth_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError (wrapped)
        with pytest.raises(RuntimeError, match="Authentication failed"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_timeout_error(self):
        """Test _fetch_formats_from_agent handles timeout errors."""
        from adcp.exceptions import ADCPTimeoutError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock timeout error
        mock_client = Mock()
        mock_agent_client = Mock()

        timeout_error = ADCPTimeoutError(
            message="Request timed out",
            agent_id="Test Agent",
            agent_uri="https://test-agent.example.com/mcp",
            timeout=30.0,
        )
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=timeout_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError with timeout message
        with pytest.raises(RuntimeError, match="Request timed out"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_connection_error(self):
        """Test _fetch_formats_from_agent handles connection errors."""
        from adcp.exceptions import ADCPConnectionError

        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock connection error
        mock_client = Mock()
        mock_agent_client = Mock()

        conn_error = ADCPConnectionError("Connection refused")
        mock_agent_client.list_creative_formats = AsyncMock(side_effect=conn_error)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="Connection failed"):
            await registry._fetch_formats_from_agent(mock_client, test_agent)

    @pytest.mark.asyncio
    async def test_fetch_formats_from_agent_handles_model_dump(self):
        """Test _fetch_formats_from_agent handles Pydantic model responses."""
        registry = CreativeAgentRegistry()

        test_agent = CreativeAgent(
            agent_url="https://test-agent.example.com/mcp",
            name="Test Agent",
            enabled=True,
            priority=1,
        )

        # Mock format data as Pydantic models with model_dump()
        mock_client = Mock()
        mock_agent_client = Mock()

        mock_format_model = Mock()
        mock_format_model.model_dump = Mock(
            return_value={
                "format_id": {"agent_url": "https://test-agent.example.com/mcp", "id": "display_300x250"},
                "name": "Display 300x250",
                "type": "display",
                "renders": [{"role": "primary", "dimensions": {"width": 300, "height": 250, "unit": "px"}}],
            }
        )

        mock_result = Mock()
        mock_result.status = "completed"
        mock_result.data = Mock()
        mock_result.data.formats = [mock_format_model]

        mock_agent_client.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client.agent = Mock(return_value=mock_agent_client)

        # Call the method
        formats = await registry._fetch_formats_from_agent(mock_client, test_agent)

        # Verify model_dump was called
        mock_format_model.model_dump.assert_called_once()

        # Verify format was constructed
        assert len(formats) == 1
        assert formats[0].format_id.id == "display_300x250"

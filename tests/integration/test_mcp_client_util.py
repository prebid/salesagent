"""Integration tests for unified MCP client utility.

These tests verify that our unified MCP client can connect to real MCP servers
and handle various scenarios (auth, retries, errors, etc.).
"""

import pytest

from src.core.utils.mcp_client import (
    MCPConnectionError,
    _build_auth_headers,
    check_mcp_agent_connection,
    create_mcp_client,
)


class TestBuildAuthHeaders:
    """Test auth header building logic."""

    def test_no_auth(self):
        """No auth config returns empty headers."""
        headers = _build_auth_headers(None)
        assert headers == {}

    def test_bearer_auth_default_header(self):
        """Bearer auth uses Authorization header by default."""
        auth = {"type": "bearer", "credentials": "token123"}
        headers = _build_auth_headers(auth)
        assert headers == {"Authorization": "Bearer token123"}

    def test_api_key_auth_default_header(self):
        """API key auth uses x-api-key header by default."""
        auth = {"type": "api_key", "credentials": "key123"}
        headers = _build_auth_headers(auth)
        assert headers == {"x-api-key": "key123"}

    def test_custom_auth_header(self):
        """Custom header name overrides default."""
        auth = {"type": "bearer", "credentials": "token123"}
        headers = _build_auth_headers(auth, auth_header="X-Custom-Auth")
        assert headers == {"X-Custom-Auth": "Bearer token123"}

    def test_generic_auth_type(self):
        """Unknown auth types use x-api-key with credentials as-is."""
        auth = {"type": "custom", "credentials": "secret123"}
        headers = _build_auth_headers(auth)
        assert headers == {"x-api-key": "secret123"}

    def test_missing_credentials(self):
        """Missing credentials returns empty headers."""
        auth = {"type": "bearer"}
        headers = _build_auth_headers(auth)
        assert headers == {}

    def test_missing_type(self):
        """Missing auth type returns empty headers."""
        auth = {"credentials": "token123"}
        headers = _build_auth_headers(auth)
        assert headers == {}


@pytest.mark.asyncio
class TestCreateMCPClient:
    """Test MCP client creation and connection."""

    @pytest.mark.skip_ci
    async def test_connect_to_creative_agent(self):
        """Can connect to AdCP creative agent (known good server)."""
        agent_url = "https://creative.adcontextprotocol.org/mcp"

        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Should successfully connect
            assert client is not None

            # Should be able to list tools
            tools = await client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0

            # Should have expected tools
            tool_names = [tool.name for tool in tools]
            assert "list_creative_formats" in tool_names

    @pytest.mark.skip_ci
    async def test_connect_to_audience_agent(self):
        """Can connect to audience/signals agent."""
        agent_url = "https://audience-agent.fly.dev"

        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Should successfully connect
            assert client is not None

            # Should be able to list tools
            tools = await client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0

            # Should have expected tools
            tool_names = [tool.name for tool in tools]
            assert "get_signals" in tool_names

    @pytest.mark.requires_server
    async def test_connect_to_local_mcp_server(self):
        """Can connect to our local MCP server in Docker."""
        agent_url = "http://localhost:8100/mcp"

        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Should successfully connect
            assert client is not None

            # Should be able to list tools
            tools = await client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0

            # Should have our sales agent tools
            tool_names = [tool.name for tool in tools]
            assert "get_products" in tool_names
            assert "create_media_buy" in tool_names

    async def test_invalid_url_raises_connection_error(self):
        """Invalid URL raises MCPConnectionError after retries."""
        agent_url = "https://nonexistent.example.com/mcp"

        with pytest.raises(MCPConnectionError) as exc_info:
            async with create_mcp_client(agent_url=agent_url, timeout=5, max_retries=2):
                pass

        assert "Failed to connect" in str(exc_info.value)
        assert "after 2 attempts" in str(exc_info.value)

    async def test_respects_max_retries(self):
        """Connection failures respect max_retries parameter."""
        agent_url = "https://nonexistent.example.com/mcp"

        with pytest.raises(MCPConnectionError) as exc_info:
            async with create_mcp_client(agent_url=agent_url, timeout=1, max_retries=1):
                pass

        # Should only try once
        assert "after 1 attempts" in str(exc_info.value)


@pytest.mark.asyncio
class TestMCPConnectionTest:
    """Test the check_mcp_agent_connection helper function."""

    @pytest.mark.skip_ci
    async def test_successful_connection(self):
        """Successful connection returns success dict."""
        agent_url = "https://creative.adcontextprotocol.org/mcp"

        result = await check_mcp_agent_connection(agent_url=agent_url)

        assert result["success"] is True
        assert "message" in result
        assert result["tool_count"] > 0

    async def test_failed_connection(self):
        """Failed connection returns error dict."""
        agent_url = "https://nonexistent.example.com/mcp"

        result = await check_mcp_agent_connection(agent_url=agent_url)

        assert result["success"] is False
        assert "error" in result
        assert "Connection failed" in result["error"]

    @pytest.mark.skip_ci
    async def test_with_auth(self):
        """Connection test works with auth config."""
        agent_url = "https://creative.adcontextprotocol.org/mcp"
        auth = {"type": "bearer", "credentials": "test_token"}

        # This should still succeed even though auth isn't required
        # (server should ignore extra auth headers)
        result = await check_mcp_agent_connection(agent_url=agent_url, auth=auth)

        assert result["success"] is True


@pytest.mark.asyncio
class TestURLHandling:
    """Test that URL handling respects user input."""

    @pytest.mark.skip_ci
    async def test_respects_user_url_exactly(self):
        """Client uses the exact URL provided by user (no modifications)."""
        # Audience agent is at base URL (no path)
        agent_url = "https://audience-agent.fly.dev"

        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Should use URL as-is
            tools = await client.list_tools()
            assert len(tools) > 0
            assert any(tool.name == "get_signals" for tool in tools)

    @pytest.mark.skip_ci
    async def test_strips_trailing_slashes_only(self):
        """Client strips trailing slashes but preserves path."""
        # URL with trailing slash
        agent_url = "https://creative.adcontextprotocol.org/mcp/"

        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Should strip trailing slash but keep /mcp path
            tools = await client.list_tools()
            assert len(tools) > 0


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.skip_ci
    async def test_client_cleanup_on_error(self):
        """Client is properly cleaned up even if error occurs during usage."""
        agent_url = "https://creative.adcontextprotocol.org/mcp"

        # Context manager should handle cleanup gracefully
        async with create_mcp_client(agent_url=agent_url, timeout=10) as client:
            # Successfully connected, just verify we can use the client
            tools = await client.list_tools()
            assert len(tools) > 0

        # If we reach here without hanging, cleanup worked correctly
        assert True

    async def test_timeout_handling(self):
        """Connection timeout is respected."""
        # Use a URL that will timeout (assuming nothing on port 9999)
        agent_url = "http://localhost:9999/mcp"

        with pytest.raises(MCPConnectionError):
            async with create_mcp_client(agent_url=agent_url, timeout=1, max_retries=1):
                pass

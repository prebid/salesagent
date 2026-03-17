"""Unit tests for creative agent TextContent fallback.

Tests the fallback path when the adcp SDK 3.6.0 rejects TextContent
responses from creative agents that don't return structuredContent.

Fixes: salesagent-c6i
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry


@pytest.fixture
def registry():
    return CreativeAgentRegistry()


@pytest.fixture
def agent():
    return CreativeAgent(
        agent_url="https://creative.example.com",
        name="test-agent",
        auth={"type": "token", "credentials": "test-token"},
        auth_header="x-test-auth",
    )


SAMPLE_FORMATS_JSON = '{"formats": [{"format_id": {"agent_url": "https://creative.example.com", "id": "display_image"}, "name": "Display Image", "type": "display"}]}'


class TestStructuredContentFallbackTrigger:
    """Test that the structuredContent error triggers the fallback."""

    @pytest.mark.asyncio
    async def test_failed_status_with_structured_content_error_triggers_fallback(self, registry, agent):
        """SDK returns TaskResult(status='failed', error='...structuredContent...') → triggers fallback."""
        mock_result = MagicMock()
        mock_result.status = "failed"
        mock_result.error = "MCP tool list_creative_formats did not return structuredContent. This SDK requires..."

        mock_agent_proxy = MagicMock()
        mock_agent_proxy.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client = MagicMock()
        mock_client.agent.return_value = mock_agent_proxy

        with (
            patch.object(registry, "_build_adcp_client", return_value=mock_client),
            patch.object(registry, "_fetch_formats_raw_mcp", new_callable=AsyncMock, return_value=[]) as mock_fallback,
        ):
            await registry._fetch_formats_from_agent(mock_client, agent)
            mock_fallback.assert_called_once_with(agent)

    @pytest.mark.asyncio
    async def test_failed_status_with_other_error_raises_value_error(self, registry, agent):
        """SDK returns TaskResult(status='failed', error='some other error') → raises ValueError."""
        mock_result = MagicMock()
        mock_result.status = "failed"
        mock_result.error = "Connection refused"
        mock_result.message = None

        mock_agent_proxy = MagicMock()
        mock_agent_proxy.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client = MagicMock()
        mock_client.agent.return_value = mock_agent_proxy

        with patch.object(registry, "_build_adcp_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Creative agent format fetch failed"):
                await registry._fetch_formats_from_agent(mock_client, agent)


class TestFetchFormatsRawMcp:
    """Test the raw HTTP fallback method."""

    @pytest.mark.asyncio
    async def test_json_response_parses_formats(self, registry, agent):
        """Raw HTTP returns JSON with result.content[].text → formats parsed."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": SAMPLE_FORMATS_JSON}],
            },
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            formats = await registry._fetch_formats_raw_mcp(agent)
            assert len(formats) == 1
            assert formats[0].format_id.id == "display_image"

    @pytest.mark.asyncio
    async def test_sse_response_parses_formats(self, registry, agent):
        """Raw HTTP returns SSE with data: {...} → formats parsed."""
        import json

        sse_payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": SAMPLE_FORMATS_JSON}]}}
        )
        sse_text = f"data: {sse_payload}\n\n"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "text/event-stream"}
        mock_response.text = sse_text

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            formats = await registry._fetch_formats_raw_mcp(agent)
            assert len(formats) == 1
            assert formats[0].format_id.id == "display_image"

    @pytest.mark.asyncio
    async def test_unexpected_format_returns_empty(self, registry, agent):
        """Raw HTTP returns unexpected format → returns empty list."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            formats = await registry._fetch_formats_raw_mcp(agent)
            assert formats == []

    @pytest.mark.asyncio
    async def test_auth_headers_forwarded(self, registry, agent):
        """Verify auth credentials are included in the HTTP request."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            await registry._fetch_formats_raw_mcp(agent)
            call_kwargs = mock_http.post.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert headers.get("x-test-auth") == "test-token"


class TestParseMcpToolResult:
    """Test the MCP tool result parser."""

    def test_parses_text_content(self, registry):
        """Content with text type → parsed formats."""
        import logging

        result = {"content": [{"type": "text", "text": SAMPLE_FORMATS_JSON}]}
        formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert len(formats) == 1
        assert formats[0].name == "Display Image"

    def test_no_text_content_returns_empty(self, registry):
        """Content with no text items → returns []."""
        import logging

        result = {"content": [{"type": "image", "data": "..."}]}
        formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert formats == []

    def test_empty_content_returns_empty(self, registry):
        """Empty content list → returns []."""
        import logging

        result = {"content": []}
        formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert formats == []

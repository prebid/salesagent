"""Unit tests for creative agent TextContent fallback.

Tests the fallback path when the adcp SDK 3.6.0 rejects TextContent
responses from creative agents that don't return structuredContent.

Fixes: salesagent-c6i
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.exceptions import AdCPAdapterError


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
        """SDK returns TaskResult(status='failed', error='some other error') → raises AdCPAdapterError."""
        mock_result = MagicMock()
        mock_result.status = "failed"
        mock_result.error = "Connection refused"
        mock_result.message = None

        mock_agent_proxy = MagicMock()
        mock_agent_proxy.list_creative_formats = AsyncMock(return_value=mock_result)
        mock_client = MagicMock()
        mock_client.agent.return_value = mock_agent_proxy

        with patch.object(registry, "_build_adcp_client", return_value=mock_client):
            with pytest.raises(AdCPAdapterError, match="Creative agent format fetch failed"):
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
    async def test_unexpected_format_raises_runtime_error(self, registry, agent):
        """Raw HTTP returns unexpected format (no 'result' key) → raises AdCPAdapterError.

        Fix for salesagent-kwws: silent return [] masked failures as 'no formats'.
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(AdCPAdapterError, match="No parseable result"):
                await registry._fetch_formats_raw_mcp(agent)

    @pytest.mark.asyncio
    async def test_auth_headers_forwarded(self, registry, agent):
        """Verify auth credentials are included in the HTTP request."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": '{"formats": []}'}]},
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            await registry._fetch_formats_raw_mcp(agent)
            call_kwargs = mock_http.post.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert headers.get("x-test-auth") == "test-token"


class TestFetchFormatsRawMcpErrorHandling:
    """Test error handling in the raw HTTP fallback."""

    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self, registry, agent):
        """httpx timeout → RuntimeError with message."""
        import httpx

        mock_http = AsyncMock()
        mock_http.post.side_effect = httpx.ReadTimeout("timed out")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(RuntimeError, match="Request timed out"):
                await registry._fetch_formats_raw_mcp(agent)

    @pytest.mark.asyncio
    async def test_connection_error_raises_runtime_error(self, registry, agent):
        """httpx connection error → RuntimeError with message."""
        import httpx

        mock_http = AsyncMock()
        mock_http.post.side_effect = httpx.ConnectError("connection refused")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(RuntimeError, match="Connection failed"):
                await registry._fetch_formats_raw_mcp(agent)

    @pytest.mark.asyncio
    async def test_http_status_error_raises_runtime_error(self, registry, agent):
        """httpx HTTP 500 → RuntimeError with status code."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response
        )

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(RuntimeError, match="HTTP error: 500"):
                await registry._fetch_formats_raw_mcp(agent)


class TestParseMcpToolResult:
    """Test the MCP tool result parser."""

    def test_parses_text_content(self, registry):
        """Content with text type → parsed formats."""
        import logging

        result = {"content": [{"type": "text", "text": SAMPLE_FORMATS_JSON}]}
        formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert len(formats) == 1
        assert formats[0].name == "Display Image"

    def test_no_text_content_raises(self, registry):
        """Content with no text items → raises AdCPAdapterError.

        Fix for salesagent-kwws: silent return [] masked failures as 'no formats'.
        """
        import logging

        result = {"content": [{"type": "image", "data": "..."}]}
        with pytest.raises(AdCPAdapterError, match="No text content"):
            registry._parse_mcp_tool_result(result, logging.getLogger())

    def test_empty_content_raises(self, registry):
        """Empty content list → raises AdCPAdapterError.

        Fix for salesagent-kwws: silent return [] masked failures as 'no formats'.
        """
        import logging

        result = {"content": []}
        with pytest.raises(AdCPAdapterError, match="No text content"):
            registry._parse_mcp_tool_result(result, logging.getLogger())


def _mcp_text_result(payload: dict) -> dict:
    """Wrap a list_creative_formats payload as an MCP tools/call TextContent result."""
    import json

    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


# Two fully-known formats the pinned adcp library understands completely.
_KNOWN_FORMAT_A = {
    "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
    "name": "Medium Rectangle",
    "assets": [{"item_type": "individual", "asset_id": "primary", "asset_type": "image", "required": True}],
}
_KNOWN_FORMAT_B = {
    "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90_image"},
    "name": "Leaderboard",
    "assets": [{"item_type": "individual", "asset_id": "primary", "asset_type": "image", "required": True}],
}
# AdCP-additive asset_type the canonical reference agent serves but the pinned
# (and latest) adcp closed Literal union does NOT model. This is the exact
# production defect class from salesagent-w8yn.
_ADDITIVE_FORMAT = {
    "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "tracking_pixel"},
    "name": "Tracking Pixel",
    "assets": [{"item_type": "individual", "asset_id": "pixel", "asset_type": "pixel_tracker", "required": True}],
}


class TestTolerantPerFormatIngestion:
    """Hermetic regression for salesagent-w8yn (Postel / asymmetric strictness).

    One unknown AdCP-additive asset_type must NOT nuke the whole
    list_creative_formats response. Fully-understood formats are returned;
    formats whose ONLY problem is an unrecognized additive asset_type are
    dropped (never mis-represented) with ONE aggregated WARNING; genuinely
    malformed formats still fail LOUD.
    """

    def test_unknown_additive_asset_type_does_not_discard_known_formats(self, registry, caplog):
        """Mixed batch: 2 known + 1 additive(pixel_tracker) → 2 returned, no exception, one warning."""
        import logging

        result = _mcp_text_result({"formats": [_KNOWN_FORMAT_A, _ADDITIVE_FORMAT, _KNOWN_FORMAT_B]})

        with caplog.at_level(logging.WARNING):
            formats = registry._parse_mcp_tool_result(result, logging.getLogger())

        ids = sorted(f.format_id.id for f in formats)
        assert ids == ["display_300x250_image", "display_728x90_image"], (
            "Known formats must survive; only the additive-asset_type format is dropped"
        )
        # Exactly one structured aggregating WARNING naming the unsupported value + skipped count.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"Expected ONE aggregated warning, got {len(warnings)}: {warnings}"
        msg = warnings[0].getMessage()
        assert "pixel_tracker" in msg, f"Warning must name the unsupported asset_type: {msg}"
        assert "1" in msg, f"Warning must report the skipped count: {msg}"

    def test_all_known_formats_pass_through_unchanged(self, registry):
        """No additive types → all formats returned, zero warnings (no behavior change)."""
        import logging

        result = _mcp_text_result({"formats": [_KNOWN_FORMAT_A, _KNOWN_FORMAT_B]})
        formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert sorted(f.format_id.id for f in formats) == ["display_300x250_image", "display_728x90_image"]

    def test_genuinely_malformed_format_still_fails_loud(self, registry):
        """A real schema bug (not an additive enum) must NOT be masked — fail loud."""
        import logging

        malformed = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "broken"},
            "name": 12345,  # wrong type — a genuine contract violation, not additive growth
            "assets": [{"item_type": "individual", "asset_id": "primary", "asset_type": "image", "required": True}],
        }
        result = _mcp_text_result({"formats": [_KNOWN_FORMAT_A, malformed]})
        with pytest.raises(Exception, match="(?i)valid"):
            registry._parse_mcp_tool_result(result, logging.getLogger())

    def test_additive_type_with_structurally_broken_asset_fails_loud(self, registry):
        """Unknown asset_type AND a structurally broken asset → not purely additive → fail loud."""
        import logging

        broken_additive = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "broken_pixel"},
            "name": "Broken Pixel",
            # asset_type unknown AND asset_id/required missing — substituting a known
            # asset_type would STILL fail, so this is not benign additive growth.
            "assets": [{"item_type": "individual", "asset_type": "pixel_tracker"}],
        }
        result = _mcp_text_result({"formats": [_KNOWN_FORMAT_A, broken_additive]})
        with pytest.raises(Exception, match="(?i)valid"):
            registry._parse_mcp_tool_result(result, logging.getLogger())

    def test_all_formats_additive_returns_empty_with_warning(self, registry, caplog):
        """Every format additive → empty list (NOT crash), one aggregated warning."""
        import logging

        result = _mcp_text_result({"formats": [_ADDITIVE_FORMAT]})
        with caplog.at_level(logging.WARNING):
            formats = registry._parse_mcp_tool_result(result, logging.getLogger())
        assert formats == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "pixel_tracker" in warnings[0].getMessage()


class TestSchemaValidationFailureTriggersFallback:
    """salesagent-w8yn: a wholesale schema-parse FAILED from the adcp client must
    fall back to the raw-MCP path (where per-format tolerance applies), not only
    transport-class errors."""

    @pytest.mark.asyncio
    async def test_schema_mismatch_failed_status_triggers_raw_fallback(self, registry, agent):
        """SDK returns status='failed' with a schema-validation error → raw-MCP fallback."""
        mock_result = MagicMock()
        mock_result.status = "failed"
        # The exact wholesale-validation signature observed live (2700 errors).
        mock_result.error = "Response doesn't match expected schema ListCreativeFormatsResponse"
        mock_result.message = None

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

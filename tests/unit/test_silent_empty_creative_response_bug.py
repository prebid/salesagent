"""Regression tests for #1177: silent return {} masks broken MCP responses in preview/build_creative.

CreativeAgentRegistry.preview_creative and CreativeAgentRegistry.build_creative
returned an empty dict when the MCP response had neither a structured_content
field nor parseable content items. Downstream callers in
src/core/tools/creatives/_processing.py treated the empty dict as falsy and
fell into the "Preview generation returned no previews" branch — emitting a
misleading buyer-facing error ("Preview generation failed: no previews
returned and no media_url provided") when the actual cause was a broken
agent response that should have surfaced as an explicit AdCPAdapterError.

These tests pin the new contract: an unparseable MCP response (no
structured_content AND no content items) raises AdCPAdapterError and
preserves the agent_url in the message.

Companion regression suite to test_silent_empty_format_bug.py for the
remaining preview/build call sites in the same file.

Bug: prebid/salesagent#1177
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgentRegistry
from src.core.exceptions import AdCPAdapterError


@pytest.fixture
def registry():
    return CreativeAgentRegistry()


def _mcp_result(*, structured_content=None, content=None):
    """Build a minimal MCP CallToolResult-shaped mock."""
    result = MagicMock()
    if structured_content is not None:
        result.structured_content = structured_content
    else:
        # Force the hasattr / truthiness check to fall through.
        result.structured_content = None
    result.content = content if content is not None else []
    return result


def _mock_mcp_client(call_tool_result):
    """Build an async-context-manager mock that yields a client whose
    call_tool returns *call_tool_result*."""
    client = MagicMock()
    client.call_tool = AsyncMock(return_value=call_tool_result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


class TestPreviewCreativeRaisesOnEmptyMcpResponse:
    """preview_creative must raise AdCPAdapterError (not return {}) when MCP response is unparseable."""

    @pytest.mark.asyncio
    async def test_no_structured_content_and_no_content_raises(self, registry):
        """structured_content=None and content=[] → raise AdCPAdapterError."""
        agent_url = "https://creative.example.com"
        result = _mcp_result(structured_content=None, content=[])
        cm, _ = _mock_mcp_client(result)

        with patch("src.core.creative_agent_registry.create_mcp_client", return_value=cm):
            with pytest.raises(AdCPAdapterError, match="No parseable preview result"):
                await registry.preview_creative(agent_url=agent_url, format_id="display_300x250", creative_manifest={})

    @pytest.mark.asyncio
    async def test_error_message_includes_agent_url(self, registry):
        """Error message must include agent_url for debuggability."""
        agent_url = "https://broken.example.com"
        result = _mcp_result(structured_content=None, content=[])
        cm, _ = _mock_mcp_client(result)

        with patch("src.core.creative_agent_registry.create_mcp_client", return_value=cm):
            with pytest.raises(AdCPAdapterError) as exc_info:
                await registry.preview_creative(agent_url=agent_url, format_id="display_300x250", creative_manifest={})

        assert agent_url in str(exc_info.value)


class TestBuildCreativeRaisesOnEmptyMcpResponse:
    """build_creative must raise AdCPAdapterError (not return {}) when MCP response is unparseable."""

    @pytest.mark.asyncio
    async def test_no_structured_content_and_no_content_raises(self, registry):
        """structured_content=None and content=[] → raise AdCPAdapterError."""
        agent_url = "https://creative.example.com"
        result = _mcp_result(structured_content=None, content=[])
        cm, _ = _mock_mcp_client(result)

        with patch("src.core.creative_agent_registry.create_mcp_client", return_value=cm):
            with pytest.raises(AdCPAdapterError, match="No parseable build result"):
                await registry.build_creative(
                    agent_url=agent_url,
                    format_id="display_300x250_generative",
                    message="build me a banner",
                    gemini_api_key="test-key",
                )

    @pytest.mark.asyncio
    async def test_error_message_includes_agent_url(self, registry):
        """Error message must include agent_url for debuggability."""
        agent_url = "https://broken.example.com"
        result = _mcp_result(structured_content=None, content=[])
        cm, _ = _mock_mcp_client(result)

        with patch("src.core.creative_agent_registry.create_mcp_client", return_value=cm):
            with pytest.raises(AdCPAdapterError) as exc_info:
                await registry.build_creative(
                    agent_url=agent_url,
                    format_id="display_300x250_generative",
                    message="build me a banner",
                    gemini_api_key="test-key",
                )

        assert agent_url in str(exc_info.value)

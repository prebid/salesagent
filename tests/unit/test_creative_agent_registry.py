"""Unit tests for Creative Agent Registry.

``TestCreativeAgentRegistry`` (build/fetch against a mocked ``adcp.ADCPMultiAgentClient``)
was retired by GH #1802: the OPERATOR agent path no longer constructs
that SDK client at all — it dials through the guarded MCP seam
(``src.core.utils.mcp_client.call_mcp_tool``, reached through
``src.core.utils.operator_mcp.call_operator_mcp_tool``) instead, via
``_fetch_formats_operator``. Its behavioral contracts (auth propagation,
connection-alias routing, error taxonomy) are re-expressed as integration
tests against a real local origin in
``tests/integration/test_creative_agent_operator_seam.py`` — the project's
stated preference over mocking the thing under test's own dependency.
"""

import pytest
from pydantic import AnyUrl

from src.core.creative_agent_registry import CreativeAgentRegistry


class TestCacheKeyAcceptsAnyUrl:
    """Regression tests for #1106: _cache_key must accept Pydantic AnyUrl.

    FormatId.agent_url is AnyUrl (not a str subclass in Pydantic v2).
    When GAM line item creation resolves formats, the AnyUrl flows through
    format_resolver → creative_agent_registry._cache_key → yarl.URL().
    yarl.URL() rejects non-str input with TypeError.
    """

    def test_cache_key_accepts_pydantic_anyurl(self):
        """_cache_key must not crash when given AnyUrl instead of str."""
        registry = CreativeAgentRegistry()
        agent_url = AnyUrl("https://creative.adcontextprotocol.org/")
        result = registry._cache_key(agent_url)
        assert result == "https://creative.adcontextprotocol.org"

    def test_cache_key_normalizes_anyurl_same_as_str(self):
        """AnyUrl and equivalent str must produce the same cache key."""
        registry = CreativeAgentRegistry()
        str_key = registry._cache_key("https://creative.adcontextprotocol.org/")
        anyurl_key = registry._cache_key(AnyUrl("https://creative.adcontextprotocol.org/"))
        assert str_key == anyurl_key

    @pytest.mark.asyncio
    async def test_get_format_accepts_anyurl_agent_url(self, monkeypatch):
        """get_format must not crash when agent_url is AnyUrl (GAM line item path)."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        registry = CreativeAgentRegistry()

        # Patch _fetch to avoid real HTTP — we only test the cache_key path
        async def mock_fetch(*args, **kwargs):
            return []

        monkeypatch.setattr(registry, "_fetch_formats_operator", mock_fetch)

        result = await registry.get_format(AnyUrl("https://creative.adcontextprotocol.org/"), "display_300x250_image")
        assert result is None  # Not found, but no TypeError

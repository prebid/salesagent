"""CREATIVE_AGENT_URL aliases the public default agent's CONNECTION .

The pinned test/CI stacks set CREATIVE_AGENT_URL to an in-network agent serving
the same reference catalog as the public default. The registry honored that for
its default-agent LISTING, but any caller passing the public canonical URL
explicitly (format_id.agent_url from the reference catalog -> get_format,
build_creative, preview_creative) still connected to the LIVE public host —
which rate-limits under CI load and flaked the required E2E check.

The public default URL is a connection ALIAS for the configured agent: the
wire-level federation identity (format_id.agent_url) is unchanged; only the
transport connection reroutes.

GH #1802 moved the OPERATOR agent path off ``adcp.ADCPMultiAgentClient``
onto the guarded MCP seam (``call_mcp_tool``, reached through
``src.core.utils.operator_mcp.call_operator_mcp_tool``) — the routing/aliasing
tests below mock that seam instead of the deleted SDK client. They patch the
dial as ``operator_mcp`` imports it, one frame below the registry, so the
alias resolution AND its handoff to the shared seam function both stay under
test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import (
    PUBLIC_DEFAULT_AGENT_URL,
    CreativeAgentRegistry,
    _connection_agent_url,
)
from tests.factories.format import FormatFactory

_PINNED = "http://creative-agent:8080/api/creative-agent"
_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"


class TestConnectionAgentUrl:
    def test_public_url_aliases_to_configured_agent(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == _PINNED

    def test_public_url_with_slash_variant_aliases(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL + "/") == _PINNED

    def test_non_public_url_unchanged(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url("https://other-agent.example.com") == "https://other-agent.example.com"

    def test_env_unset_unchanged(self, monkeypatch):
        monkeypatch.delenv("CREATIVE_AGENT_URL", raising=False)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == PUBLIC_DEFAULT_AGENT_URL

    def test_env_equal_to_public_unchanged(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", PUBLIC_DEFAULT_AGENT_URL)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == PUBLIC_DEFAULT_AGENT_URL


def _mock_call_mcp_tool(formats_payload: dict) -> AsyncMock:
    """A ``call_mcp_tool(...)`` awaitable mock returning *formats_payload* as ``structured_content``."""
    return AsyncMock(return_value=MagicMock(structured_content=formats_payload, content=[]))


class TestRegistryConnectionRouting:
    @pytest.mark.asyncio
    async def test_fetch_path_connects_to_configured_agent(self, monkeypatch):
        """_fetch_formats_operator must dial the ALIASED url for the public agent
        while the cache/federation identity stays on the canonical url."""
        from src.core.creative_agent_registry import CreativeAgent

        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()

        call_tool = _mock_call_mcp_tool({"formats": []})
        with patch(_SEAM_DIAL, call_tool) as cmc:
            await registry._fetch_formats_operator(CreativeAgent(agent_url=PUBLIC_DEFAULT_AGENT_URL, name="x"))

        assert cmc.call_args.kwargs["agent_url"] == _PINNED

    @pytest.mark.asyncio
    async def test_preview_creative_connects_to_configured_agent(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()

        mock_call_mcp_tool = AsyncMock(return_value=MagicMock(content=[]))

        with patch(_SEAM_DIAL, mock_call_mcp_tool) as cmc:
            try:
                await registry.preview_creative(PUBLIC_DEFAULT_AGENT_URL, "display_300x250", {"assets": {}})
            except Exception:
                pass  # response parsing is not under test — only connection + payload
        assert cmc.call_args.kwargs["agent_url"] == _PINNED

        # The payload's format_id is the federation-identity OBJECT carrying the
        # CANONICAL agent_url (not the connection alias) — the pinned reference
        # agent rejects a bare string, which the live public host tolerated
        # (the mismatch the in-network pinning unmasked). The identity keeps the
        # CANONICAL agent_url and is the FormatId serialization
        # (model_dump(mode="json")): Pydantic AnyUrl yields the trailing-slash
        # form for the path-less public URL — verified tolerated by the pinned
        # reference agent (probe 2026-07-13).
        payload = cmc.call_args.kwargs["arguments"]
        assert payload["format_id"] == {
            "agent_url": PUBLIC_DEFAULT_AGENT_URL + "/",
            "id": "display_300x250",
        }


async def _fetch_formats_with_mocked_seam(registry, agent):
    """Drive registry.get_formats_for_agent twice with only call_mcp_tool mocked.

    The swap itself (_connection_agent_url / _fetch_formats_operator) and the
    real request-building/parsing run unmocked; only call_mcp_tool — the true
    external that would dial the network — is replaced. Returns the patched
    mock twice (transport-config evidence and fetch-count observation are the
    same call now that connect and call share one function), and both call
    results.
    """
    formats_payload = {"formats": [FormatFactory.build().model_dump(mode="json")]}
    call_tool = _mock_call_mcp_tool(formats_payload)
    with patch(_SEAM_DIAL, call_tool) as constructor:
        first = await registry.get_formats_for_agent(agent)
        second = await registry.get_formats_for_agent(agent)
    return constructor, call_tool, first, second


class TestAliasPreservesIdentity:
    """Executable behavioural exemption for the sanctioned env-driven host swap.

    _connection_agent_url (creative_agent_registry.py) returns a DIFFERENT
    string for the public default agent when CREATIVE_AGENT_URL is set — a
    destination change ahead of the seam that the destination-rewrite guard
    cannot see (no urlunparse/._replace spelling). This class is the
    exemption's executable justification (GH #1589 / GH #1802): the
    swap touches ONLY the transport config of ONLY the public default agent,
    while the identity/cache-key URL stays byte-identical to the canonical
    public URL — graded through the registry's real fetch surface.
    """

    @pytest.mark.asyncio
    async def test_aliased_fetch_keeps_cache_identity_on_canonical_url(self, monkeypatch):
        from src.core.creative_agent_registry import CreativeAgent
        from src.core.schemas import canonical_agent_url

        monkeypatch.delenv("ADCP_TESTING", raising=False)
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(agent_url=PUBLIC_DEFAULT_AGENT_URL, name="AdCP Standard Creative Agent")

        constructor, fetch, first, second = await _fetch_formats_with_mocked_seam(registry, agent)

        # Transport: call_mcp_tool dials the pinned alias.
        assert constructor.call_args.kwargs["agent_url"] == _PINNED

        # Identity: the cache key is byte-identical to the CANONICAL public
        # URL — the alias appears nowhere in the cache.
        assert set(registry._format_cache) == {canonical_agent_url(PUBLIC_DEFAULT_AGENT_URL)}
        assert canonical_agent_url(_PINNED) not in registry._format_cache

        # And that identity is load-bearing: the second call under the
        # canonical URL is a cache hit — exactly one fetch went out.
        assert fetch.await_count == 1
        assert second == first

    @pytest.mark.asyncio
    async def test_non_default_agent_dials_and_caches_its_own_url(self, monkeypatch):
        """With the env set, a NON-default agent is untouched end to end."""
        from src.core.creative_agent_registry import CreativeAgent
        from src.core.schemas import canonical_agent_url

        monkeypatch.delenv("ADCP_TESTING", raising=False)
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()
        partner_url = "https://partner-agent.example.com"
        agent = CreativeAgent(agent_url=partner_url, name="Partner Agent")

        constructor, fetch, first, second = await _fetch_formats_with_mocked_seam(registry, agent)

        assert constructor.call_args.kwargs["agent_url"] == partner_url
        assert set(registry._format_cache) == {canonical_agent_url(partner_url)}
        assert fetch.await_count == 1
        assert second == first

    @pytest.mark.asyncio
    async def test_env_unset_public_agent_dials_canonical_url(self, monkeypatch):
        """Without the env var, even the public default agent is not rerouted."""
        from src.core.creative_agent_registry import CreativeAgent

        monkeypatch.delenv("ADCP_TESTING", raising=False)
        monkeypatch.delenv("CREATIVE_AGENT_URL", raising=False)
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(agent_url=PUBLIC_DEFAULT_AGENT_URL, name="AdCP Standard Creative Agent")

        constructor, _fetch, _first, _second = await _fetch_formats_with_mocked_seam(registry, agent)

        assert constructor.call_args.kwargs["agent_url"] == PUBLIC_DEFAULT_AGENT_URL

"""CREATIVE_AGENT_URL aliases the public default agent's CONNECTION (salesagent-9qe2).

The pinned test/CI stacks set CREATIVE_AGENT_URL to an in-network agent serving
the same reference catalog as the public default. The registry honored that for
its default-agent LISTING, but any caller passing the public canonical URL
explicitly (format_id.agent_url from the reference catalog -> get_format,
build_creative, preview_creative) still connected to the LIVE public host —
which rate-limits under CI load and flaked the required E2E check.

The public default URL is a connection ALIAS for the configured agent: the
wire-level federation identity (format_id.agent_url) is unchanged; only the
transport connection reroutes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import (
    PUBLIC_DEFAULT_AGENT_URL,
    CreativeAgentRegistry,
    _connection_agent_url,
)

_PINNED = "http://creative-agent:8080/api/creative-agent"


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


class TestRegistryConnectionRouting:
    def test_fetch_path_connects_to_configured_agent(self, monkeypatch):
        """_build_adcp_client must receive the ALIASED url for the public agent
        while the cache/federation identity stays on the canonical url."""
        from src.core.creative_agent_registry import CreativeAgent

        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()
        captured: list[str] = []

        with (
            patch("src.core.helpers.adapter_helpers.build_agent_config") as bac,
            patch("src.core.creative_agent_registry.ADCPMultiAgentClient"),
        ):
            bac.side_effect = lambda agent: captured.append(agent.agent_url) or MagicMock()
            registry._build_adcp_client([CreativeAgent(agent_url=PUBLIC_DEFAULT_AGENT_URL, name="x", enabled=True)])

        assert captured == [_PINNED]

    @pytest.mark.asyncio
    async def test_preview_creative_connects_to_configured_agent(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        registry = CreativeAgentRegistry()

        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(
            return_value=MagicMock(call_tool=AsyncMock(return_value=MagicMock(content=[])))
        )
        client_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("src.core.creative_agent_registry.create_mcp_client", return_value=client_cm) as cmc:
            try:
                await registry.preview_creative(PUBLIC_DEFAULT_AGENT_URL, "display_300x250", {"assets": {}})
            except Exception:
                pass  # response parsing is not under test — only connection + payload
        assert cmc.call_args.kwargs["agent_url"] == _PINNED

        # The payload's format_id is the federation-identity OBJECT carrying the
        # CANONICAL agent_url (not the connection alias) — the pinned reference
        # agent rejects a bare string, which the live public host tolerated
        # (the mismatch the in-network pinning unmasked). The identity is the
        # FormatId serialization (model_dump(mode="json")): Pydantic AnyUrl
        # yields the trailing-slash form for the path-less public URL
        # (salesagent-ehdq — verified tolerated by the pinned reference agent).
        call_tool = client_cm.__aenter__.return_value.call_tool
        payload = call_tool.call_args.args[1]
        assert payload["format_id"] == {
            "agent_url": PUBLIC_DEFAULT_AGENT_URL + "/",
            "id": "display_300x250",
        }

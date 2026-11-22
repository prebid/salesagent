"""CREATIVE_AGENT_URL is where the operator agent is DIALLED; CREATIVE_AGENT_ORIGIN is who it IS.

The pinned test/CI stacks set CREATIVE_AGENT_URL to the MCP endpoint of an in-network agent
serving the same reference catalog as the public default. A reference naming the operator
agent -- recognized by ORIGIN, the agent's identity, never by path -- connects there instead
of the live public host, which rate-limits under CI load and flaked the required E2E check.

The wire-level federation identity (format_id.agent_url) is unchanged; only the transport
connection reroutes. The endpoint itself is never an identity: before the two settings were
split, CREATIVE_AGENT_URL carried both meanings, so it had to hold the bare
``/api/creative-agent`` path the operator agent's references carry -- where the reference
agent serves no MCP -- and every dial spent its whole retry budget before the '/mcp' fallback.

salesagent-4n88 moved the OPERATOR agent path off ``adcp.ADCPMultiAgentClient``
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
from pydantic import ValidationError

from src.core.config import IntegrationSettings
from src.core.creative_agent_registry import (
    PUBLIC_DEFAULT_AGENT_URL,
    PUBLIC_DEFAULT_MCP_ENDPOINT,
    CreativeAgentRegistry,
    _connection_agent_url,
    is_operator_agent,
)
from tests.factories.format import FormatFactory

#: An MCP endpoint, shaped like the one docker-compose.e2e.yml configures: a path the
#: references never carry, at an origin that is not the operator agent's identity.
_PINNED = "https://creative-agent.adcp.test:8443/api/creative-agent/mcp"
_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"


@pytest.fixture(autouse=True)
def _identity_is_the_public_agent(monkeypatch):
    """Every test starts with no configured identity, so the operator agent is the public one."""
    monkeypatch.delenv("CREATIVE_AGENT_ORIGIN", raising=False)


class TestConnectionAgentUrl:
    def test_public_url_aliases_to_configured_endpoint(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == _PINNED

    def test_public_url_with_slash_variant_aliases(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL + "/") == _PINNED

    def test_a_path_on_the_operator_origin_still_aliases(self, monkeypatch):
        """The path is not part of an agent's identity: the origin decides."""
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL + "/api/creative-agent") == _PINNED

    def test_non_operator_url_unchanged(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        assert _connection_agent_url("https://other-agent.example.com") == "https://other-agent.example.com"

    def test_env_unset_dials_the_public_endpoint_not_the_identity(self, monkeypatch):
        monkeypatch.delenv("CREATIVE_AGENT_URL", raising=False)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == PUBLIC_DEFAULT_MCP_ENDPOINT

    def test_the_endpoint_is_not_an_identity(self, monkeypatch):
        """A reference naming the dialled endpoint's own agent is not the operator agent."""
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        stack_agent = "https://creative-agent.adcp.test:8443/api/creative-agent"

        assert not is_operator_agent(stack_agent)
        assert _connection_agent_url(stack_agent) == stack_agent

    def test_a_configured_origin_is_the_identity(self, monkeypatch):
        """CREATIVE_AGENT_ORIGIN replaces the public agent as the operator agent."""
        monkeypatch.setenv("CREATIVE_AGENT_URL", _PINNED)
        monkeypatch.setenv("CREATIVE_AGENT_ORIGIN", "https://creative.seller.example")

        assert _connection_agent_url("https://CREATIVE.seller.example/api/creative-agent") == _PINNED
        assert not is_operator_agent(PUBLIC_DEFAULT_AGENT_URL)
        assert _connection_agent_url(PUBLIC_DEFAULT_AGENT_URL) == PUBLIC_DEFAULT_AGENT_URL


class TestOperatorOriginSetting:
    """CREATIVE_AGENT_ORIGIN is refused at settings load unless it is an origin."""

    @pytest.mark.parametrize(
        "value",
        [
            "https://creative.seller.example/",
            "https://creative.seller.example/api/creative-agent",
            "https://creative.seller.example/api/creative-agent/mcp",
            "https://creative.seller.example?tenant=a",
            "https://creative.seller.example#agent",
            "https://ops@creative.seller.example",
            "creative.seller.example",
        ],
    )
    def test_anything_but_an_origin_is_refused(self, value):
        with pytest.raises(ValidationError, match="CREATIVE_AGENT_ORIGIN must be an origin"):
            IntegrationSettings(creative_agent_origin=value)

    @pytest.mark.parametrize(
        "value", ["https://creative.adcontextprotocol.org", "https://creative.seller.example:8443"]
    )
    def test_an_origin_is_accepted(self, value):
        assert IntegrationSettings(creative_agent_origin=value).creative_agent_origin == value


class TestUnreachableReferencedAgent:
    """An unreachable agent the request's format_ids name is REFERENCE_NOT_FOUND on format_ids.

    The operator agent fails under its identity, an origin, while a reference to it carries
    the path its catalog publishes, so the two are matched by origin. Any other agent is
    matched as spelled. ``failed_url`` is the registry's own default agent, never a string
    written here.
    """

    _OPERATOR = "https://creative.seller.example"

    @staticmethod
    def _route(failed_url: str, requested_agent_url: str) -> list:
        from src.core.creative_agent_registry import FormatFetchResult
        from src.core.errors.codes import AppErrorCode
        from src.core.schemas import Error, ListCreativeFormatsRequest
        from src.core.tools.creative_formats import _route_agent_failures

        fetch_result = FormatFetchResult(
            formats=[],
            errors=[Error.of(AppErrorCode.AGENT_UNREACHABLE, field="formats")],
            failed_agent_urls=[failed_url],
        )
        req = ListCreativeFormatsRequest(format_ids=[{"agent_url": requested_agent_url, "id": "display_300x250_image"}])
        return [(error.code, error.field) for error in _route_agent_failures(fetch_result, req)]

    def test_a_mount_path_reference_to_the_operator_agent_is_reference_not_found(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_ORIGIN", self._OPERATOR)
        operator = CreativeAgentRegistry().DEFAULT_AGENT.agent_url

        routed = self._route(operator, f"{self._OPERATOR}/api/creative-agent")

        assert routed == [("REFERENCE_NOT_FOUND", "format_ids")]

    def test_a_reference_to_another_agent_keeps_the_advisory(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_ORIGIN", self._OPERATOR)
        operator = CreativeAgentRegistry().DEFAULT_AGENT.agent_url

        routed = self._route(operator, "https://creative.other.example/api/creative-agent")

        assert routed == [("AGENT_UNREACHABLE", "formats")]

    def test_another_agent_is_matched_with_its_path(self, monkeypatch):
        monkeypatch.setenv("CREATIVE_AGENT_ORIGIN", self._OPERATOR)
        partner = "https://partner.example/api/creative-agent"

        assert self._route(partner, partner) == [("REFERENCE_NOT_FOUND", "format_ids")]
        assert self._route(partner, "https://partner.example/other-agent") == [("AGENT_UNREACHABLE", "formats")]


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
        # (model_dump(mode="json")).
        #
        # VERBATIM, no trailing slash. It used to be ``+ "/"`` because Pydantic's
        # ``AnyUrl`` appended one to a path-less origin; adcp 9 (upstream #1396, "keep the
        # format-reference agent_url as the wire string") types the field ``str``, so the
        # serialization now carries exactly the URL that went in. The slash-suffixed form
        # was recorded as "verified tolerated by the pinned reference agent (probe
        # 2026-07-13)" — tolerated, never required — so emitting the canonical bytes is
        # strictly closer to what ``core/format-id.json`` asks for.
        payload = cmc.call_args.kwargs["arguments"]
        assert payload["format_id"] == {
            "agent_url": PUBLIC_DEFAULT_AGENT_URL,
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
    exemption's executable justification (GH #1589 / salesagent-9qe2): the
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
    async def test_env_unset_public_agent_dials_its_mcp_endpoint(self, monkeypatch):
        """Without the env var the public agent is dialled at its MCP endpoint, never at its identity origin."""
        from src.core.creative_agent_registry import CreativeAgent

        monkeypatch.delenv("ADCP_TESTING", raising=False)
        monkeypatch.delenv("CREATIVE_AGENT_URL", raising=False)
        registry = CreativeAgentRegistry()
        agent = CreativeAgent(agent_url=PUBLIC_DEFAULT_AGENT_URL, name="AdCP Standard Creative Agent")

        constructor, _fetch, _first, _second = await _fetch_formats_with_mocked_seam(registry, agent)

        assert constructor.call_args.kwargs["agent_url"] == PUBLIC_DEFAULT_MCP_ENDPOINT

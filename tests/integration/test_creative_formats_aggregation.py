"""Integration tests: UC-005-MAIN-MCP-03 formats aggregated from multiple agents.

Covers:
- UC-005-MAIN-MCP-03: Formats aggregated from all registered agents
"""

from __future__ import annotations

import pytest

from src.adapters.broadstreet.formats import BROADSTREET_CANONICAL_FORMAT_IDS
from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.schemas import Format, FormatId, ListCreativeFormatsResponse
from tests.factories import AdapterConfigFactory, TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.MCP]

DEFAULT_AGENT_URL = DEFAULT_CREATIVE_AGENT_URL
CUSTOM_AGENT_URL = "https://custom-dco.example.com"
BROADSTREET_CANONICAL_FORMAT_SET = set(BROADSTREET_CANONICAL_FORMAT_IDS)


def _make_format(
    agent_url: str,
    format_id: str,
    name: str,
    *,
    is_standard: bool = True,
    **kwargs,
) -> Format:
    """Helper to create a Format from a specific agent URL."""
    return Format(
        format_id=FormatId(agent_url=agent_url, id=format_id),
        name=name,
        is_standard=is_standard,
    )


def _broadstreet_canonical_formats(formats: list[Format]) -> list[Format]:
    """Return the canonical reference-agent formats Broadstreet contributes."""
    return [fmt for fmt in formats if fmt.format_id.id in BROADSTREET_CANONICAL_FORMAT_SET]


# ---------------------------------------------------------------------------
# Multi-Agent Aggregation -- Covers: UC-005-MAIN-MCP-03
# ---------------------------------------------------------------------------


class TestMultiAgentAggregation:
    """UC-005-MAIN-MCP-03: formats aggregated from all registered agents.

    Covers: UC-005-MAIN-MCP-03

    Given the tenant has a default creative agent AND at least one
    tenant-specific creative agent registered, when the Buyer calls
    list_creative_formats, then the response contains formats from
    BOTH the default agent and tenant-specific agents.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_formats_from_multiple_agents_both_present(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: formats from 2 different agent URLs both appear."""
        default_format = _make_format(
            DEFAULT_AGENT_URL,
            "display_300x250",
            "Standard Display 300x250",
        )
        custom_format = _make_format(
            CUSTOM_AGENT_URL,
            "custom_banner",
            "Custom DCO Banner",
            is_standard=False,
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([default_format, custom_format])

            result = env.call_via(transport)

        assert result.is_success
        format_ids = {f.format_id.id for f in result.payload.formats}
        assert "display_300x250" in format_ids
        assert "custom_banner" in format_ids

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_format_agent_urls_preserved(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: each format retains its originating agent_url."""
        default_format = _make_format(
            DEFAULT_AGENT_URL,
            "display_300x250",
            "Standard Display",
        )
        custom_format = _make_format(
            CUSTOM_AGENT_URL,
            "custom_banner",
            "Custom Banner",
            is_standard=False,
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([default_format, custom_format])

            result = env.call_via(transport)

        assert result.is_success
        agent_urls_by_id = {f.format_id.id: str(f.format_id.agent_url) for f in result.payload.formats}
        assert DEFAULT_AGENT_URL in agent_urls_by_id["display_300x250"]
        assert CUSTOM_AGENT_URL in agent_urls_by_id["custom_banner"]

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_dedup_across_agents(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: same format ID from different agents kept distinct."""
        format_from_default = _make_format(
            DEFAULT_AGENT_URL,
            "display_300x250",
            "Default Display",
        )
        format_from_custom = _make_format(
            CUSTOM_AGENT_URL,
            "display_300x250",
            "Custom Display",
            is_standard=False,
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([format_from_default, format_from_custom])

            result = env.call_via(transport)

        assert result.is_success
        # Both formats with same ID but different agent_url should be present
        matching = [f for f in result.payload.formats if f.format_id.id == "display_300x250"]
        assert len(matching) == 2
        urls = {str(f.format_id.agent_url) for f in matching}
        assert len(urls) == 2

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_mixed_format_types_from_multiple_agents(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: formats of different types from different agents aggregated."""
        display_format = _make_format(
            DEFAULT_AGENT_URL,
            "display_728x90",
            "Leaderboard",
        )
        video_format = _make_format(
            CUSTOM_AGENT_URL,
            "video_preroll",
            "Pre-roll Video",
            is_standard=False,
        )
        audio_format = _make_format(
            "https://audio-agent.example.com",
            "audio_companion",
            "Audio Companion",
            is_standard=False,
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([display_format, video_format, audio_format])

            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 3


class TestAdapterFormatsMerged:
    """UC-005-MAIN-MCP-03: adapter-supported canonical formats merged alongside agent formats.

    Covers: UC-005-MAIN-MCP-03

    When the tenant has a Broadstreet adapter, the canonical formats supported
    by Broadstreet are merged into the aggregated format list alongside
    creative agent formats.
    """

    def test_broadstreet_formats_merged_with_agent_formats(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet canonical formats merged alongside agent formats."""
        agent_format = _make_format(
            DEFAULT_AGENT_URL,
            "display_300x250",
            "Standard Display",
        )

        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            env.set_registry_formats([agent_format])
            response = env.call_impl()

        assert isinstance(response, ListCreativeFormatsResponse)

        # Agent format present
        format_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250" in format_ids

        # Broadstreet-supported canonical formats present
        broadstreet_formats = _broadstreet_canonical_formats(response.formats)
        assert {f.format_id.id for f in broadstreet_formats} == BROADSTREET_CANONICAL_FORMAT_SET
        assert all(str(f.format_id.agent_url).rstrip("/") == DEFAULT_AGENT_URL for f in broadstreet_formats)

        # Total = agent formats + Broadstreet-supported canonical formats
        assert len(response.formats) == 1 + len(BROADSTREET_CANONICAL_FORMAT_IDS)

    def test_broadstreet_merge_dedupes_reference_agent_aliases(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet merge de-dupes canonical reference-agent aliases."""
        agent_format = _make_format(
            "https://adcontextprotocol.org/agents/formats/mcp/",
            "display_image",
            "Display Image",
        )

        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            env.set_registry_formats([agent_format])
            response = env.call_impl()

        matching = [fmt for fmt in response.formats if fmt.format_id.id == "display_image"]
        assert len(matching) == 1
        assert len(response.formats) == len(BROADSTREET_CANONICAL_FORMAT_IDS)

    def test_broadstreet_formats_are_canonical_reference_agent_formats(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet adapter contributes canonical reference-agent formats."""
        with CreativeFormatsEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            AdapterConfigFactory(tenant=tenant, adapter_type="broadstreet")

            env.set_registry_formats([])
            response = env.call_impl()

        broadstreet_formats = _broadstreet_canonical_formats(response.formats)
        assert {f.format_id.id for f in broadstreet_formats} == BROADSTREET_CANONICAL_FORMAT_SET
        for fmt in broadstreet_formats:
            assert str(fmt.format_id.agent_url).rstrip("/") == DEFAULT_AGENT_URL
            assert not fmt.format_id.id.startswith("broadstreet_")

"""Integration tests: UC-005-MAIN-MCP-03 formats aggregated from multiple agents.

Covers:
- UC-005-MAIN-MCP-03: Formats aggregated from all registered agents
"""

from __future__ import annotations

import pytest

from src.core.schemas import Format, FormatId, ListCreativeFormatsResponse
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"
CUSTOM_AGENT_URL = "https://custom-dco.example.com"


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
    """UC-005-MAIN-MCP-03: adapter-specific formats merged alongside agent formats.

    Covers: UC-005-MAIN-MCP-03

    When the tenant has an adapter (Broadstreet) that provides templates,
    those are merged into the aggregated format list alongside creative
    agent formats.
    """

    def test_broadstreet_formats_merged_with_agent_formats(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet adapter formats merged alongside agent formats."""
        from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        agent_format = _make_format(
            DEFAULT_AGENT_URL,
            "display_300x250",
            "Standard Display",
        )

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            # Create Broadstreet adapter config in DB
            with get_db_session() as session:
                config = AdapterConfig(
                    tenant_id="test_tenant",
                    adapter_type="broadstreet",
                )
                session.add(config)
                session.commit()

            env.set_registry_formats([agent_format])
            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)

        # Agent format present
        format_ids = {f.format_id.id for f in response.formats}
        assert "display_300x250" in format_ids

        # Broadstreet formats present
        broadstreet_formats = [f for f in response.formats if "broadstreet" in str(f.format_id.agent_url)]
        assert len(broadstreet_formats) == len(BROADSTREET_TEMPLATES)

        # Total = agent formats + adapter formats
        assert len(response.formats) == 1 + len(BROADSTREET_TEMPLATES)

    def test_broadstreet_formats_are_non_standard(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet adapter formats marked as non-standard."""
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            with get_db_session() as session:
                config = AdapterConfig(
                    tenant_id="test_tenant",
                    adapter_type="broadstreet",
                )
                session.add(config)
                session.commit()

            env.set_registry_formats([])
            response = env.call_impl()

        broadstreet_formats = [f for f in response.formats if "broadstreet" in str(f.format_id.agent_url)]
        assert len(broadstreet_formats) > 0
        for fmt in broadstreet_formats:
            assert fmt.is_standard is False

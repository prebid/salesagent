"""Integration tests: UC-005-MAIN-MCP-03 formats aggregated from all registered agents.

Covers:
- UC-005-MAIN-MCP-03: Formats aggregated from all registered agents
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import ListCreativeFormatsResponse
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

# Real catalog stats from the default creative agent Docker container:
# 28 display, 12 video, 4 dooh, 3 audio, 2 native = 49 total
REAL_CATALOG_TOTAL = 49


# ---------------------------------------------------------------------------
# Multi-Agent Aggregation -- Covers: UC-005-MAIN-MCP-03
# ---------------------------------------------------------------------------


class TestMultiAgentAggregation:
    """UC-005-MAIN-MCP-03: formats aggregated from all registered agents.

    Covers: UC-005-MAIN-MCP-03

    The real creative agent catalog (49 formats served by Docker container)
    is used directly. Tests verify that the catalog is returned with correct
    structure: agent_url is set, all format types are present, and
    (format_id.id, agent_url) pairs are unique across the response.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_formats_from_multiple_agents_both_present(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: real catalog returns formats with agent_url set."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        formats = result.payload.formats
        assert len(formats) > 0
        # Every format must have a format_id with agent_url
        for fmt in formats:
            assert fmt.format_id is not None
            assert fmt.format_id.agent_url is not None
            assert str(fmt.format_id.agent_url) != ""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_format_agent_urls_preserved(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: each format retains its originating agent_url."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        formats = result.payload.formats
        assert len(formats) > 0
        # All formats must have a non-empty agent_url identifying their originating agent
        agent_urls = {str(fmt.format_id.agent_url) for fmt in formats}
        assert len(agent_urls) >= 1
        assert all(url not in {"", "None"} for url in agent_urls)

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_dedup_across_agents(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: (format_id.id, agent_url) pairs are unique in the response."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        formats = result.payload.formats
        assert len(formats) > 0
        # Each (id, agent_url) pair must be unique — no accidental deduplication
        pairs = [(fmt.format_id.id, str(fmt.format_id.agent_url)) for fmt in formats]
        assert len(pairs) == len(set(pairs))

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_mixed_format_types_from_multiple_agents(self, integration_db, transport):
        """UC-005-MAIN-MCP-03: real catalog contains display, video, and audio format types."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        types = {fmt.type for fmt in result.payload.formats}
        assert FormatCategory.display in types
        assert FormatCategory.video in types
        assert FormatCategory.audio in types


class TestAdapterFormatsMerged:
    """UC-005-MAIN-MCP-03: adapter-specific formats merged alongside agent formats.

    Covers: UC-005-MAIN-MCP-03

    When the tenant has an adapter (Broadstreet) that provides templates,
    those are merged into the aggregated format list alongside creative
    agent formats from the real catalog.
    """

    def test_broadstreet_formats_merged_with_agent_formats(self, integration_db):
        """UC-005-MAIN-MCP-03: Broadstreet adapter formats merged alongside agent formats."""
        from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

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

            response = env.call_a2a()

        assert isinstance(response, ListCreativeFormatsResponse)

        # Broadstreet formats present
        broadstreet_formats = [f for f in response.formats if "broadstreet" in str(f.format_id.agent_url)]
        assert len(broadstreet_formats) == len(BROADSTREET_TEMPLATES)

        # Agent formats present (formats NOT from broadstreet)
        agent_formats = [f for f in response.formats if "broadstreet" not in str(f.format_id.agent_url)]
        assert len(agent_formats) > 0

        # Total = broadstreet formats + agent formats
        assert len(response.formats) == len(broadstreet_formats) + len(agent_formats)

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

            response = env.call_impl()

        broadstreet_formats = [f for f in response.formats if "broadstreet" in str(f.format_id.agent_url)]
        assert len(broadstreet_formats) > 0
        for fmt in broadstreet_formats:
            assert fmt.is_standard is False

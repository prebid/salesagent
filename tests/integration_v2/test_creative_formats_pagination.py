"""Integration tests: list_creative_formats pagination and creative agent referrals.

Covers:
- UC-005-MAIN-MCP-13: creative_agents referrals in response
- UC-005-MAIN-MCP-14: cursor-based pagination
- UC-005-MAIN-MCP-15: default pagination (max_results=50)
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.pagination_request import PaginationRequest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_format(
    format_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
) -> Format:
    """Helper to create a Format object with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id),
        name=name,
        type=type,
        is_standard=True,
    )


def _make_formats(count: int, prefix: str = "fmt") -> list[Format]:
    """Create N formats with sequential IDs for pagination tests."""
    return [_make_format(f"{prefix}_{i:03d}", f"Format {i:03d}") for i in range(count)]


# ---------------------------------------------------------------------------
# Creative Agent Referrals -- Covers: UC-005-MAIN-MCP-13
# ---------------------------------------------------------------------------


class TestCreativeAgentReferrals:
    """UC-005-MAIN-MCP-13: creative_agents referrals included in response.

    Covers: UC-005-MAIN-MCP-13
    """

    @staticmethod
    def _configure_registry_agents(env):
        """Configure mock registry to return agent list for _get_tenant_agents."""
        from src.core.creative_agent_registry import CreativeAgent as RegistryAgent

        mock_agents = [
            RegistryAgent(
                agent_url="https://creative.adcontextprotocol.org",
                name="AdCP Standard Creative Agent",
                enabled=True,
                priority=1,
            ),
            RegistryAgent(
                agent_url="https://custom-dco.example.com",
                name="Custom DCO Agent",
                enabled=True,
                priority=2,
            ),
        ]
        env.mock["registry"].return_value._get_tenant_agents.return_value = mock_agents

    def test_response_includes_creative_agents(self, integration_db):
        """UC-005-MAIN-MCP-13: response includes creative_agents with agent info."""
        formats = [_make_format("d1", "Display Banner")]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        assert len(response.creative_agents) >= 1

    def test_creative_agent_has_url(self, integration_db):
        """UC-005-MAIN-MCP-13: each referral includes agent URL."""
        formats = [_make_format("d1", "Display Banner")]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        for agent in response.creative_agents:
            assert agent.agent_url is not None

    def test_creative_agent_has_capabilities(self, integration_db):
        """UC-005-MAIN-MCP-13: each referral includes capability information."""
        formats = [_make_format("d1", "Display Banner")]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        for agent in response.creative_agents:
            assert agent.capabilities is not None
            assert len(agent.capabilities) > 0
            # Verify expected capability types
            cap_values = {c.value for c in agent.capabilities}
            assert "validation" in cap_values
            assert "assembly" in cap_values
            assert "preview" in cap_values
            assert "delivery" in cap_values

    def test_creative_agent_has_name(self, integration_db):
        """UC-005-MAIN-MCP-13: each referral includes agent name."""
        formats = [_make_format("d1", "Display Banner")]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        for agent in response.creative_agents:
            assert agent.agent_name is not None

    def test_multiple_agents_in_referrals(self, integration_db):
        """UC-005-MAIN-MCP-13: multiple agents appear as referrals."""
        formats = [_make_format("d1", "Display Banner")]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        assert len(response.creative_agents) == 2
        urls = {str(a.agent_url) for a in response.creative_agents}
        assert "https://creative.adcontextprotocol.org/" in urls or "https://creative.adcontextprotocol.org" in urls


# ---------------------------------------------------------------------------
# Cursor-Based Pagination -- Covers: UC-005-MAIN-MCP-14
# ---------------------------------------------------------------------------


class TestPaginationCursorBased:
    """UC-005-MAIN-MCP-14: cursor-based pagination on list_creative_formats.

    Covers: UC-005-MAIN-MCP-14
    """

    def test_max_results_limits_response(self, integration_db):
        """UC-005-MAIN-MCP-14: max_results=10 returns at most 10 formats."""
        formats = _make_formats(25)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert len(response.formats) == 10

    def test_pagination_includes_cursor(self, integration_db):
        """UC-005-MAIN-MCP-14: response includes cursor when more results exist."""
        formats = _make_formats(25)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert response.pagination is not None
        assert response.pagination.has_more is True
        assert response.pagination.cursor is not None

    def test_pagination_total_count(self, integration_db):
        """UC-005-MAIN-MCP-14: pagination includes total_count across all pages."""
        formats = _make_formats(25)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert response.pagination is not None
        assert response.pagination.total_count == 25

    def test_cursor_navigates_to_next_page(self, integration_db):
        """UC-005-MAIN-MCP-14: using cursor from first page returns next page."""
        formats = _make_formats(25)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            # First page
            req1 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            page1 = env.call_impl(req=req1)
            assert len(page1.formats) == 10
            cursor = page1.pagination.cursor

            # Second page using cursor
            req2 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10, cursor=cursor))
            page2 = env.call_impl(req=req2)
            assert len(page2.formats) == 10

            # Pages should contain different formats
            page1_ids = {f.format_id.id for f in page1.formats}
            page2_ids = {f.format_id.id for f in page2.formats}
            assert page1_ids.isdisjoint(page2_ids)

    def test_last_page_has_no_cursor(self, integration_db):
        """UC-005-MAIN-MCP-14: last page has has_more=False and no cursor."""
        formats = _make_formats(15)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            # First page (10 items)
            req1 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            page1 = env.call_impl(req=req1)
            cursor = page1.pagination.cursor

            # Second page (5 remaining items)
            req2 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10, cursor=cursor))
            page2 = env.call_impl(req=req2)

        assert len(page2.formats) == 5
        assert page2.pagination is not None
        assert page2.pagination.has_more is False
        assert page2.pagination.cursor is None

    def test_all_items_returned_across_pages(self, integration_db):
        """UC-005-MAIN-MCP-14: iterating all pages yields all items."""
        formats = _make_formats(25)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            all_ids: set[str] = set()
            cursor = None
            page_count = 0

            while True:
                req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10, cursor=cursor))
                response = env.call_impl(req=req)
                all_ids.update(f.format_id.id for f in response.formats)
                page_count += 1

                if not response.pagination.has_more:
                    break
                cursor = response.pagination.cursor

        assert len(all_ids) == 25
        assert page_count == 3  # 10 + 10 + 5

    def test_exact_page_boundary(self, integration_db):
        """UC-005-MAIN-MCP-14: total items exactly divisible by max_results."""
        formats = _make_formats(20)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            # First page
            req1 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            page1 = env.call_impl(req=req1)
            assert len(page1.formats) == 10
            assert page1.pagination.has_more is True

            # Second page (exactly fills)
            req2 = ListCreativeFormatsRequest(
                pagination=PaginationRequest(max_results=10, cursor=page1.pagination.cursor)
            )
            page2 = env.call_impl(req=req2)
            assert len(page2.formats) == 10
            assert page2.pagination.has_more is False


# ---------------------------------------------------------------------------
# Default Pagination -- Covers: UC-005-MAIN-MCP-15
# ---------------------------------------------------------------------------


class TestPaginationDefault:
    """UC-005-MAIN-MCP-15: default pagination (max_results=50)."""

    def test_default_max_results_is_50(self, integration_db):
        """UC-005-MAIN-MCP-15: no pagination params returns at most 50 formats."""
        formats = _make_formats(75)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()

        assert len(response.formats) == 50

    def test_default_pagination_has_more(self, integration_db):
        """UC-005-MAIN-MCP-15: pagination cursor indicates more results."""
        formats = _make_formats(75)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()

        assert response.pagination is not None
        assert response.pagination.has_more is True
        assert response.pagination.cursor is not None
        assert response.pagination.total_count == 75

    def test_default_pagination_under_limit(self, integration_db):
        """UC-005-MAIN-MCP-15: fewer than 50 formats returns all with has_more=False."""
        formats = _make_formats(30)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            response = env.call_impl()

        assert len(response.formats) == 30
        assert response.pagination is not None
        assert response.pagination.has_more is False
        assert response.pagination.cursor is None
        assert response.pagination.total_count == 30

    def test_default_pagination_complete_traversal(self, integration_db):
        """UC-005-MAIN-MCP-15: traversal with default yields all 75 formats."""
        formats = _make_formats(75)
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            all_ids: set[str] = set()
            cursor = None

            while True:
                req = ListCreativeFormatsRequest(pagination=PaginationRequest(cursor=cursor)) if cursor else None
                response = env.call_impl(req=req)
                all_ids.update(f.format_id.id for f in response.formats)

                if not response.pagination.has_more:
                    break
                cursor = response.pagination.cursor

        assert len(all_ids) == 75

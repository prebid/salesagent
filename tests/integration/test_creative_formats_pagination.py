"""Integration tests: list_creative_formats pagination and creative agent referrals.

Covers:
- UC-005-MAIN-MCP-13: creative_agents referrals in response
- UC-005-MAIN-MCP-14: cursor-based pagination
- UC-005-MAIN-MCP-15: default pagination (max_results=50)

Real catalog stats (served by Docker creative-agent sidecar):
- 28 display, 12 video, 4 dooh, 3 audio, 2 native = 49 total
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.pagination_request import PaginationRequest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Real catalog constants (served by Docker creative-agent sidecar)
REAL_TOTAL = 49
REAL_DISPLAY = 28
REAL_VIDEO = 12
REAL_DOOH = 4
REAL_AUDIO = 3
REAL_NATIVE = 2


# ---------------------------------------------------------------------------
# Creative Agent Referrals -- Covers: UC-005-MAIN-MCP-13
# ---------------------------------------------------------------------------


class TestCreativeAgentReferrals:
    """UC-005-MAIN-MCP-13: creative_agents referrals included in response.

    Covers: UC-005-MAIN-MCP-13
    """

    @staticmethod
    def _configure_registry_agents(env):
        """Patch _get_tenant_agents on the real registry to return a known agent list."""
        from unittest.mock import patch as _patch

        from src.core.creative_agent_registry import CreativeAgent as RegistryAgent
        from src.core.creative_agent_registry import get_creative_agent_registry

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
        registry = get_creative_agent_registry()
        patcher = _patch.object(registry, "_get_tenant_agents", return_value=mock_agents)
        patcher.start()
        env._patchers.append(patcher)

    def test_response_includes_creative_agents(self, integration_db):
        """UC-005-MAIN-MCP-13: response includes creative_agents with agent info."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        assert len(response.creative_agents) >= 1

    def test_creative_agent_has_url(self, integration_db):
        """UC-005-MAIN-MCP-13: each referral includes agent URL."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        for agent in response.creative_agents:
            assert agent.agent_url is not None

    def test_creative_agent_has_capabilities(self, integration_db):
        """UC-005-MAIN-MCP-13: each referral includes capability information."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
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
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            self._configure_registry_agents(env)
            response = env.call_impl()

        assert response.creative_agents is not None
        for agent in response.creative_agents:
            assert agent.agent_name is not None

    def test_multiple_agents_in_referrals(self, integration_db):
        """UC-005-MAIN-MCP-13: multiple agents appear as referrals."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
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

    All tests use the real catalog (49 formats total: 28 display, 12 video,
    4 dooh, 3 audio, 2 native).

    Covers: UC-005-MAIN-MCP-14
    """

    def test_max_results_limits_response(self, integration_db):
        """UC-005-MAIN-MCP-14: max_results=10 returns at most 10 formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert len(response.formats) == 10

    def test_pagination_includes_cursor(self, integration_db):
        """UC-005-MAIN-MCP-14: response includes cursor when more results exist."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            # Real catalog has 49 formats; max_results=10 leaves 39 more
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert response.pagination is not None
        assert response.pagination.has_more is True
        assert response.pagination.cursor is not None

    def test_pagination_total_count(self, integration_db):
        """UC-005-MAIN-MCP-14: pagination includes total_count across all pages."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=10))
            response = env.call_impl(req=req)

        assert response.pagination is not None
        assert response.pagination.total_count == REAL_TOTAL

    def test_cursor_navigates_to_next_page(self, integration_db):
        """UC-005-MAIN-MCP-14: using cursor from first page returns next page."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

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
        """UC-005-MAIN-MCP-14: last page has has_more=False and no cursor.

        Real catalog: 49 total. Use max_results=40: first page has 40 items with
        a cursor; second page has the remaining 9 items and no cursor.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            # First page (40 of 49 items)
            req1 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=40))
            page1 = env.call_impl(req=req1)
            assert len(page1.formats) == 40
            assert page1.pagination.has_more is True
            cursor = page1.pagination.cursor

            # Second page (remaining 9 items)
            req2 = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=40, cursor=cursor))
            page2 = env.call_impl(req=req2)

        assert len(page2.formats) == REAL_TOTAL - 40
        assert page2.pagination is not None
        assert page2.pagination.has_more is False
        assert page2.pagination.cursor is None

    def test_all_items_returned_across_pages(self, integration_db):
        """UC-005-MAIN-MCP-14: iterating all pages yields all items.

        Real catalog: 49 total. With max_results=10: pages of 10, 10, 10, 10, 9
        = 5 pages total.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

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

        assert len(all_ids) == REAL_TOTAL
        assert page_count == 5  # 10 + 10 + 10 + 10 + 9

    def test_exact_page_boundary(self, integration_db):
        """UC-005-MAIN-MCP-14: total items exactly divisible by max_results.

        Real catalog: 12 video formats. With max_results=6: exactly 2 full pages
        (6 + 6) with has_more=False on the second page.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            # First page of video formats (6 of 12)
            req1 = ListCreativeFormatsRequest(
                type=FormatCategory.video,
                pagination=PaginationRequest(max_results=6),
            )
            page1 = env.call_impl(req=req1)
            assert len(page1.formats) == 6
            assert page1.pagination.has_more is True

            # Second page (exactly fills; last 6 of 12)
            req2 = ListCreativeFormatsRequest(
                type=FormatCategory.video,
                pagination=PaginationRequest(max_results=6, cursor=page1.pagination.cursor),
            )
            page2 = env.call_impl(req=req2)
            assert len(page2.formats) == 6
            assert page2.pagination.has_more is False


# ---------------------------------------------------------------------------
# Default Pagination -- Covers: UC-005-MAIN-MCP-15
# ---------------------------------------------------------------------------


class TestPaginationDefault:
    """UC-005-MAIN-MCP-15: default pagination (max_results=50).

    Real catalog has 49 formats (< 50), so all formats are returned in a single
    page when no max_results is specified.  Tests that require more than 50 items
    use an explicit max_results smaller than the catalog size to verify the
    paging contract.

    Covers: UC-005-MAIN-MCP-15
    """

    def test_default_max_results_caps_at_50(self, integration_db):
        """UC-005-MAIN-MCP-15: explicit max_results=30 returns at most 30 formats.

        The real catalog has 49 formats so any max_results < 49 verifies that
        the cap is respected.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=30))
            response = env.call_impl(req=req)

        assert len(response.formats) == 30

    def test_default_pagination_has_more_when_limited(self, integration_db):
        """UC-005-MAIN-MCP-15: pagination cursor indicates more results when max_results < total."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            # Use max_results=20 — real catalog has 49, so 29 remain on next pages
            req = ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=20))
            response = env.call_impl(req=req)

        assert response.pagination is not None
        assert response.pagination.has_more is True
        assert response.pagination.cursor is not None
        assert response.pagination.total_count == REAL_TOTAL

    def test_default_pagination_under_limit(self, integration_db):
        """UC-005-MAIN-MCP-15: fewer than max_results formats returns all with has_more=False.

        Real catalog has 49 formats which is under the default max_results of 50,
        so no pagination cursor is returned.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            response = env.call_impl()

        assert len(response.formats) == REAL_TOTAL
        assert response.pagination is not None
        assert response.pagination.has_more is False
        assert response.pagination.cursor is None
        assert response.pagination.total_count == REAL_TOTAL

    def test_default_pagination_complete_traversal(self, integration_db):
        """UC-005-MAIN-MCP-15: traversal with explicit page size yields all formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            all_ids: set[str] = set()
            cursor = None

            while True:
                req = (
                    ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=20, cursor=cursor))
                    if cursor
                    else ListCreativeFormatsRequest(pagination=PaginationRequest(max_results=20))
                )
                response = env.call_impl(req=req)
                all_ids.update(f.format_id.id for f in response.formats)

                if not response.pagination.has_more:
                    break
                cursor = response.pagination.cursor

        assert len(all_ids) == REAL_TOTAL

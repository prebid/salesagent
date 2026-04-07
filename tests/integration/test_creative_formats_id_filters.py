"""Integration tests: UC-005-MAIN-MCP-06 format_ids + UC-005-MAIN-MCP-10 is_responsive filters.

Covers:
- UC-005-MAIN-MCP-06: Filter by format_ids
- UC-005-MAIN-MCP-10: Filter by is_responsive
"""

from __future__ import annotations

import pytest

from src.core.schemas import FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

# REST drops all filter kwargs (build_rest_body returns {}), so filter-specific
# tests use only IMPL/A2A/MCP. See CreativeFormatsEnv.build_rest_body.
FILTER_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP]

# Real format IDs from the creative agent catalog (49 total).
# Use these instead of synthetic ones — tests run against the real Docker container.
REAL_FORMAT_ID_1 = "display_300x250_generative"
REAL_FORMAT_ID_2 = "display_728x90_generative"
REAL_CATALOG_SIZE = 49


def _call(env: CreativeFormatsEnv, transport: Transport, **kwargs):
    """Call the appropriate transport method.

    For IMPL/A2A/REST: wraps kwargs into a ListCreativeFormatsRequest.
    For MCP: passes kwargs as individual params (MCP pops 'req').
    """
    if transport == Transport.MCP:
        return env.call_via(transport, **kwargs)
    req = ListCreativeFormatsRequest(**kwargs)
    return env.call_via(transport, req=req)


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-06: format_ids filter
# ---------------------------------------------------------------------------


class TestFormatIdsFilter:
    """UC-005-MAIN-MCP-06: format_ids filter returns only matching formats.

    Covers: UC-005-MAIN-MCP-06

    BR: format_ids filter returns only formats whose FormatId matches
    one of the requested values.
    Tests run against the real creative agent catalog (49 formats).
    """

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_single_format_id(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by one format_id returns only that format."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            target_id = FormatId(agent_url=AGENT_URL, id=REAL_FORMAT_ID_1)
            result = _call(env, transport, format_ids=[target_id])

        assert result.is_success
        assert len(result.payload.formats) == 1
        assert result.payload.formats[0].format_id.id == REAL_FORMAT_ID_1

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_multiple_format_ids(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by two format_ids returns both."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            ids = [
                FormatId(agent_url=AGENT_URL, id=REAL_FORMAT_ID_1),
                FormatId(agent_url=AGENT_URL, id=REAL_FORMAT_ID_2),
            ]
            result = _call(env, transport, format_ids=ids)

        assert result.is_success
        assert len(result.payload.formats) == 2
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert returned_ids == {REAL_FORMAT_ID_1, REAL_FORMAT_ID_2}

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_nonexistent_format_id(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by non-existent format_id returns empty."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            missing_id = FormatId(agent_url=AGENT_URL, id="nonexistent_format_xyz")
            result = _call(env, transport, format_ids=[missing_id])

        assert result.is_success
        assert result.payload.formats == []

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_format_ids_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: omitting format_ids returns all formats from the catalog."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            # No format_ids filter -- call with no kwargs
            result = _call(env, transport)

        assert result.is_success
        assert len(result.payload.formats) == REAL_CATALOG_SIZE


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-10: is_responsive filter
# ---------------------------------------------------------------------------


RESPONSIVE_FORMAT_COUNT = 2  # product_card_detailed, format_card_detailed
NON_RESPONSIVE_FORMAT_COUNT = REAL_CATALOG_SIZE - RESPONSIVE_FORMAT_COUNT  # 47


class TestIsResponsiveFilter:
    """UC-005-MAIN-MCP-10: is_responsive filter returns only matching formats.

    Covers: UC-005-MAIN-MCP-10

    BR: is_responsive=True returns formats with responsive dimensions;
    is_responsive=False returns formats without.
    Tests run against the real creative agent catalog (49 formats total).
    2 formats are responsive (product_card_detailed, format_card_detailed);
    the remaining 47 are non-responsive.
    """

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_true_returns_responsive_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=True returns only responsive formats.

        The real catalog has 2 responsive formats (product_card_detailed,
        format_card_detailed), so the result contains exactly 2 formats.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = _call(env, transport, is_responsive=True)

        assert result.is_success
        # Real catalog: 2 formats are responsive (product_card_detailed, format_card_detailed)
        assert len(result.payload.formats) == RESPONSIVE_FORMAT_COUNT
        responsive_ids = {f.format_id.id for f in result.payload.formats}
        assert "product_card_detailed" in responsive_ids
        assert "format_card_detailed" in responsive_ids

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_false_returns_fixed_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=False returns only non-responsive formats.

        The real catalog has 49 total formats, 2 of which are responsive.
        is_responsive=False returns the 47 non-responsive formats.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = _call(env, transport, is_responsive=False)

        assert result.is_success
        assert len(result.payload.formats) == NON_RESPONSIVE_FORMAT_COUNT

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_is_responsive_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: omitting is_responsive returns all formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = _call(env, transport)

        assert result.is_success
        assert len(result.payload.formats) == REAL_CATALOG_SIZE

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_no_renders_treated_as_not_responsive(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: format without renders is not responsive.

        Formats with empty or missing renders (like display_300x250_generative)
        are treated as non-responsive and included in is_responsive=False results.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = _call(env, transport, is_responsive=False)

        assert result.is_success
        returned_ids = {f.format_id.id for f in result.payload.formats}
        # display_300x250_generative has empty renders — must be in the non-responsive set
        assert REAL_FORMAT_ID_1 in returned_ids

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_true_excludes_non_responsive_formats(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=True excludes formats without responsive renders.

        Formats like display_300x250_generative (no renders) must NOT appear
        in is_responsive=True results — only formats with responsive renders qualify.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = _call(env, transport, is_responsive=True)

        assert result.is_success
        returned_ids = {f.format_id.id for f in result.payload.formats}
        # display_300x250_generative has no renders — must be excluded from responsive results
        assert REAL_FORMAT_ID_1 not in returned_ids

"""Integration tests: UC-005-MAIN-MCP-06 format_ids + UC-005-MAIN-MCP-10 is_responsive filters.

Covers:
- UC-005-MAIN-MCP-06: Filter by format_ids
- UC-005-MAIN-MCP-10: Filter by is_responsive
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.format import Dimensions, Renders, Responsive
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

# REST drops all filter kwargs (build_rest_body returns {}), so filter-specific
# tests use only IMPL/A2A/MCP. See CreativeFormatsEnv.build_rest_body.
FILTER_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP]


def _fmt(
    fmt_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    **kwargs,
) -> Format:
    """Shorthand for creating a Format object."""
    return Format(
        format_id=FormatId(agent_url=AGENT_URL, id=fmt_id),
        type=type,
        name=name,
        is_standard=True,
        **kwargs,
    )


def _responsive_fmt(
    fmt_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    **kwargs,
) -> Format:
    """Create a responsive format (dimensions.responsive.width=True)."""
    return _fmt(
        fmt_id,
        name,
        type=type,
        renders=[
            Renders(
                role="primary",
                dimensions=Dimensions(
                    min_width=300,
                    max_width=970,
                    responsive=Responsive(width=True, height=False),
                ),
            )
        ],
        **kwargs,
    )


def _fixed_fmt(
    fmt_id: str,
    name: str,
    width: int = 300,
    height: int = 250,
    type: FormatCategory = FormatCategory.display,
    **kwargs,
) -> Format:
    """Create a non-responsive format with fixed dimensions."""
    return _fmt(
        fmt_id,
        name,
        type=type,
        renders=[
            Renders(
                role="primary",
                dimensions=Dimensions(width=width, height=height),
            )
        ],
        **kwargs,
    )


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
    """

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_single_format_id(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by one format_id returns only that format."""
        formats = [
            _fmt("display_300", "Medium Rectangle"),
            _fmt("display_728", "Leaderboard"),
            _fmt("video_15s", "Pre-roll 15s", type=FormatCategory.video),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            target_id = FormatId(agent_url=AGENT_URL, id="display_300")
            result = _call(env, transport, format_ids=[target_id])

        assert result.is_success
        assert len(result.payload.formats) == 1
        assert result.payload.formats[0].format_id.id == "display_300"

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_multiple_format_ids(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by two format_ids returns both."""
        formats = [
            _fmt("fmt_a", "Format A"),
            _fmt("fmt_b", "Format B"),
            _fmt("fmt_c", "Format C"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            ids = [
                FormatId(agent_url=AGENT_URL, id="fmt_a"),
                FormatId(agent_url=AGENT_URL, id="fmt_c"),
            ]
            result = _call(env, transport, format_ids=ids)

        assert result.is_success
        assert len(result.payload.formats) == 2
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert returned_ids == {"fmt_a", "fmt_c"}

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_filter_by_nonexistent_format_id(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: filter by non-existent format_id returns empty."""
        formats = [
            _fmt("display_300", "Medium Rectangle"),
            _fmt("display_728", "Leaderboard"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            missing_id = FormatId(agent_url=AGENT_URL, id="does_not_exist")
            result = _call(env, transport, format_ids=[missing_id])

        assert result.is_success
        assert result.payload.formats == []

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_format_ids_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-06: omitting format_ids returns all formats."""
        formats = [
            _fmt("fmt_1", "Format One"),
            _fmt("fmt_2", "Format Two"),
            _fmt("fmt_3", "Format Three"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            # No format_ids filter -- call with no kwargs
            result = _call(env, transport)

        assert result.is_success
        assert len(result.payload.formats) == 3


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-10: is_responsive filter
# ---------------------------------------------------------------------------


class TestIsResponsiveFilter:
    """UC-005-MAIN-MCP-10: is_responsive filter returns only matching formats.

    Covers: UC-005-MAIN-MCP-10

    BR: is_responsive=True returns formats with responsive dimensions;
    is_responsive=False returns formats without.
    """

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_true_returns_responsive_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=True returns only responsive formats."""
        formats = [
            _responsive_fmt("responsive_banner", "Responsive Banner"),
            _responsive_fmt("responsive_video", "Responsive Video", type=FormatCategory.video),
            _fixed_fmt("fixed_300", "Fixed 300x250"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = _call(env, transport, is_responsive=True)

        assert result.is_success
        assert len(result.payload.formats) == 2
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert returned_ids == {"responsive_banner", "responsive_video"}

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_false_returns_fixed_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=False returns only non-responsive formats."""
        formats = [
            _responsive_fmt("responsive_banner", "Responsive Banner"),
            _fixed_fmt("fixed_300", "Fixed 300x250"),
            _fixed_fmt("fixed_728", "Fixed Leaderboard", width=728, height=90),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = _call(env, transport, is_responsive=False)

        assert result.is_success
        assert len(result.payload.formats) == 2
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert returned_ids == {"fixed_300", "fixed_728"}

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_is_responsive_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: omitting is_responsive returns all formats."""
        formats = [
            _responsive_fmt("responsive_one", "Responsive One"),
            _fixed_fmt("fixed_one", "Fixed One"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = _call(env, transport)

        assert result.is_success
        assert len(result.payload.formats) == 2

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_no_renders_treated_as_not_responsive(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: format without renders is not responsive."""
        formats = [
            _fmt("no_renders", "No Renders Format"),  # no renders at all
            _responsive_fmt("responsive_one", "Responsive One"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = _call(env, transport, is_responsive=False)

        assert result.is_success
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert "no_renders" in returned_ids

    @pytest.mark.parametrize("transport", FILTER_TRANSPORTS)
    def test_is_responsive_true_with_no_responsive_formats_returns_empty(self, integration_db, transport):
        """UC-005-MAIN-MCP-10: is_responsive=True with only fixed formats returns empty."""
        formats = [
            _fixed_fmt("fixed_300", "Fixed 300x250"),
            _fixed_fmt("fixed_728", "Fixed Leaderboard", width=728, height=90),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = _call(env, transport, is_responsive=True)

        assert result.is_success
        assert result.payload.formats == []

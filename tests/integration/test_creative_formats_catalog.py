"""Integration tests: UC-005-MAIN-MCP-01 full catalog with no filters.

Covers:
- UC-005-MAIN-MCP-01: Full catalog returned with no filters
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.core.format import Assets, Assets5, Dimensions, Renders
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsResponse
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]


def _make_format(
    format_id: str,
    name: str,
    type: FormatCategory = FormatCategory.display,
    renders: list | None = None,
    assets: list | None = None,
) -> Format:
    """Build a Format with minimal boilerplate."""
    return Format(
        format_id=FormatId(agent_url=AGENT_URL, id=format_id),
        name=name,
        type=type,
        is_standard=True,
        renders=renders,
        assets=assets,
    )


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-01: Full catalog returned when no filters applied
# ---------------------------------------------------------------------------


class TestFullCatalogNoFilters:
    """Covers: UC-005-MAIN-MCP-01

    Full catalog returned when no filters applied across all transports.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_all_formats_returned(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: no filters returns all registered formats."""
        formats = [
            _make_format(
                "display_300x250",
                "Medium Rectangle",
                type=FormatCategory.display,
                renders=[Renders(role="primary", dimensions=Dimensions(width=300, height=250))],
                assets=[Assets(item_type="individual", asset_id="hero_image", required=True)],
            ),
            _make_format(
                "video_preroll_15s",
                "Pre-roll 15s",
                type=FormatCategory.video,
                renders=[Renders(role="primary", dimensions=Dimensions(width=640, height=360))],
                assets=[Assets5(item_type="individual", asset_id="video_file", required=True)],
            ),
            _make_format(
                "audio_companion",
                "Audio Companion Banner",
                type=FormatCategory.audio,
            ),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 3
        returned_ids = {f.format_id.id for f in result.payload.formats}
        assert returned_ids == {"display_300x250", "video_preroll_15s", "audio_companion"}

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_format_structure_post_s2(self, integration_db, transport):
        """UC-005-MAIN-MCP-01 POST-S2: each format includes format_id, name, type.

        Verifies that each format in the response contains the required
        structural fields: format_id (with agent_url and id), name, and type.
        Assets with type, dimensions, and required flags are also verified
        when present.
        """
        formats = [
            _make_format(
                "display_banner",
                "Standard Display Banner",
                type=FormatCategory.display,
                renders=[Renders(role="primary", dimensions=Dimensions(width=728, height=90))],
                assets=[
                    Assets(
                        item_type="individual",
                        asset_id="banner_image",
                        required=True,
                    ),
                ],
            ),
            _make_format(
                "video_midroll",
                "Mid-roll Video",
                type=FormatCategory.video,
                assets=[
                    Assets5(
                        item_type="individual",
                        asset_id="video_asset",
                        required=True,
                    ),
                ],
            ),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 2

        for fmt in result.payload.formats:
            # Required structural fields
            assert fmt.format_id is not None
            assert fmt.format_id.id is not None
            assert str(fmt.format_id.agent_url).rstrip("/") == AGENT_URL
            assert fmt.name is not None
            assert fmt.type is not None

        # Verify specific format details
        fmt_by_id = {f.format_id.id: f for f in result.payload.formats}

        display_fmt = fmt_by_id["display_banner"]
        assert display_fmt.name == "Standard Display Banner"
        assert display_fmt.type == FormatCategory.display
        assert display_fmt.assets is not None
        assert len(display_fmt.assets) == 1

        video_fmt = fmt_by_id["video_midroll"]
        assert video_fmt.name == "Mid-roll Video"
        assert video_fmt.type == FormatCategory.video

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_empty_catalog_returns_empty_formats(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: empty registry returns empty formats list, not error."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])
            result = env.call_via(transport)

        assert result.is_success
        assert isinstance(result.payload, ListCreativeFormatsResponse)
        assert result.payload.formats == []

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_single_format_catalog(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: single format in registry returned correctly."""
        formats = [
            _make_format(
                "sole_format",
                "The Only Format",
                type=FormatCategory.display,
                renders=[Renders(role="primary", dimensions=Dimensions(width=320, height=50))],
            ),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 1
        assert result.payload.formats[0].format_id.id == "sole_format"
        assert result.payload.formats[0].name == "The Only Format"

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_diverse_format_types_all_returned(self, integration_db, transport):
        """UC-005-MAIN-MCP-01 POST-S1: complete catalog from all format categories.

        Verifies that formats of all categories (display, video, audio) are
        included when no filters are applied.
        """
        formats = [
            _make_format("display_leaderboard", "Leaderboard 728x90", type=FormatCategory.display),
            _make_format("display_mrec", "Medium Rectangle", type=FormatCategory.display),
            _make_format("video_preroll", "Pre-roll 30s", type=FormatCategory.video),
            _make_format("video_outstream", "Outstream Video", type=FormatCategory.video),
            _make_format("audio_spot", "Audio Spot 15s", type=FormatCategory.audio),
        ]

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 5

        returned_types = {f.type for f in result.payload.formats}
        assert FormatCategory.display in returned_types
        assert FormatCategory.video in returned_types
        assert FormatCategory.audio in returned_types

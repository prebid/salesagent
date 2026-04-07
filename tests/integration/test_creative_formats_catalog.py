"""Integration tests: UC-005-MAIN-MCP-01 full catalog with no filters.

Covers:
- UC-005-MAIN-MCP-01: Full catalog returned with no filters

These tests run against the real creative agent catalog served by a Docker
container. The real catalog contains 49 formats: 28 display, 12 video,
4 dooh, 3 audio, 2 native.
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import ListCreativeFormatsResponse
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

AGENT_URL = "https://creative.adcontextprotocol.org"

# Real catalog stats (served by Docker creative agent container)
REAL_CATALOG_TOTAL = 49
REAL_CATALOG_DISPLAY_COUNT = 28
REAL_CATALOG_VIDEO_COUNT = 12
REAL_CATALOG_DOOH_COUNT = 4
REAL_CATALOG_AUDIO_COUNT = 3
REAL_CATALOG_NATIVE_COUNT = 2

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-01: Full catalog returned when no filters applied
# ---------------------------------------------------------------------------


class TestFullCatalogNoFilters:
    """Covers: UC-005-MAIN-MCP-01

    Full catalog returned when no filters applied across all transports.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_all_formats_returned(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: no filters returns all formats from the real catalog."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == REAL_CATALOG_TOTAL

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_format_structure_post_s2(self, integration_db, transport):
        """UC-005-MAIN-MCP-01 POST-S2: each format includes format_id, name, type.

        Verifies that every format in the real catalog response contains the
        required structural fields: format_id (with agent_url and id), name,
        and type.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) > 0

        for fmt in result.payload.formats:
            # Required structural fields present on every catalog entry
            assert fmt.format_id is not None
            assert fmt.format_id.id is not None
            assert fmt.format_id.agent_url is not None
            assert fmt.name is not None
            assert fmt.type is not None

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_response_is_list_creative_formats_response(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: response is a well-formed ListCreativeFormatsResponse."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            result = env.call_via(transport)

        assert result.is_success
        assert isinstance(result.payload, ListCreativeFormatsResponse)
        assert isinstance(result.payload.formats, list)
        assert len(result.payload.formats) > 0

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_catalog_contains_display_formats(self, integration_db, transport):
        """UC-005-MAIN-MCP-01: real catalog includes the expected number of display formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            result = env.call_via(transport)

        assert result.is_success
        display_formats = [f for f in result.payload.formats if f.type == FormatCategory.display]
        assert len(display_formats) == REAL_CATALOG_DISPLAY_COUNT
        assert all(f.type == FormatCategory.display for f in display_formats)

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_diverse_format_types_all_returned(self, integration_db, transport):
        """UC-005-MAIN-MCP-01 POST-S1: complete catalog from all format categories.

        Verifies that formats of all categories (display, video, audio, dooh,
        native) are present in the real catalog when no filters are applied.
        """
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == REAL_CATALOG_TOTAL

        returned_types = {f.type for f in result.payload.formats}
        assert FormatCategory.display in returned_types
        assert FormatCategory.video in returned_types
        assert FormatCategory.audio in returned_types
        assert FormatCategory.dooh in returned_types
        assert FormatCategory.native in returned_types

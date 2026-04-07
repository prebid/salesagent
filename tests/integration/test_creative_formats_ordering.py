"""Integration tests: UC-005-MAIN-MCP-04 sorting + UC-005-MAIN-MCP-05 type filter.

Covers:
- UC-005-MAIN-MCP-04: Results sorted by format type then name
- UC-005-MAIN-MCP-05: Filter by format category (type)
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

# IMPL and A2A accept req= kwarg for filtering; MCP takes individual params.
# REST build_rest_body discards filter kwargs, so REST only works for unfiltered tests.
REQ_TRANSPORTS = [Transport.IMPL, Transport.A2A]

# Real catalog stats (49 total formats served by the creative agent container):
#   28 display, 12 video, 4 dooh, 3 audio, 2 native
REAL_CATALOG_TOTAL = 49
REAL_DISPLAY_COUNT = 28
REAL_VIDEO_COUNT = 12
REAL_AUDIO_COUNT = 3


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-04: Results sorted by format type then name
# ---------------------------------------------------------------------------


class TestSortingByTypeThenName:
    """UC-005-MAIN-MCP-04: Results sorted by format type then name.

    Covers: UC-005-MAIN-MCP-04

    BR: Formats are sorted first by type (alphabetical on enum value:
    audio < display < video), then by name within each type.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_sorted_by_type_then_name(self, integration_db, transport):
        """UC-005-MAIN-MCP-04: results sorted by type then name across all transports."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        formats = result.payload.formats
        assert len(formats) > 0

        # Verify type-then-name ordering: adjacent pair comparison
        for i in range(len(formats) - 1):
            a = formats[i]
            b = formats[i + 1]
            type_a = a.type.value
            type_b = b.type.value
            # type must be non-decreasing
            assert type_a <= type_b, f"Type order violated at index {i}: {type_a!r} > {type_b!r}"
            # within same type, name must be non-decreasing
            if type_a == type_b:
                assert a.name <= b.name, (
                    f"Name order violated within type {type_a!r} at index {i}: {a.name!r} > {b.name!r}"
                )

    def test_sorting_deterministic_across_calls(self, integration_db):
        """UC-005-MAIN-MCP-04: ordering is deterministic across repeated calls."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result_1 = env.call_impl()
            result_2 = env.call_impl()

        ids_1 = [f.format_id.id for f in result_1.formats]
        ids_2 = [f.format_id.id for f in result_2.formats]
        assert ids_1 == ids_2

    def test_sorting_single_type(self, integration_db):
        """UC-005-MAIN-MCP-04: formats of one type sorted alphabetically by name."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="display")
            result = env.call_impl(req=req)

        names = [f.name for f in result.formats]
        assert len(names) > 0
        assert names == sorted(names), f"Display formats not in alphabetical order: {names}"

    def test_sorting_preserves_all_formats(self, integration_db):
        """UC-005-MAIN-MCP-04: sorting does not lose or duplicate formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_impl()

        assert len(result.formats) == REAL_CATALOG_TOTAL
        # No duplicates
        actual_ids = [f.format_id.id for f in result.formats]
        assert len(actual_ids) == len(set(actual_ids))


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-05: Filter by format category (type)
# ---------------------------------------------------------------------------


class TestTypeFilter:
    """UC-005-MAIN-MCP-05: Filter by format category (type).

    Covers: UC-005-MAIN-MCP-05

    BR: When type filter is provided, only formats of that type are returned.
    """

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_video_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=video returns only video formats (IMPL/A2A)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="video")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == REAL_VIDEO_COUNT
        assert all(f.type == FormatCategory.video for f in result.payload.formats)

    def test_filter_video_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-05: type=video returns only video formats (MCP)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(Transport.MCP, type=FormatCategory.video)

        assert result.is_success
        assert len(result.payload.formats) == REAL_VIDEO_COUNT
        assert all(f.type == FormatCategory.video for f in result.payload.formats)

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_display_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=display returns only display formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="display")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == REAL_DISPLAY_COUNT
        assert all(f.type == FormatCategory.display for f in result.payload.formats)

    def test_filter_display_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-05: type=display returns only display formats (MCP)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(Transport.MCP, type=FormatCategory.display)

        assert result.is_success
        assert len(result.payload.formats) == REAL_DISPLAY_COUNT
        assert all(f.type == FormatCategory.display for f in result.payload.formats)

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_audio_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=audio returns only audio formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="audio")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == REAL_AUDIO_COUNT
        assert all(f.type == FormatCategory.audio for f in result.payload.formats)

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: no type filter returns all formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == REAL_CATALOG_TOTAL

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_excludes_other_types(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=video excludes display and audio."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="video")
            result = env.call_via(transport, req=req)

        assert result.is_success
        for fmt in result.payload.formats:
            assert fmt.type != FormatCategory.display
            assert fmt.type != FormatCategory.audio

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_results_still_sorted(self, integration_db, transport):
        """UC-005-MAIN-MCP-05 + MCP-04: filtered results maintain sort order."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")

            req = ListCreativeFormatsRequest(type="display")
            result = env.call_via(transport, req=req)

        assert result.is_success
        names = [f.name for f in result.payload.formats]
        assert names == sorted(names)

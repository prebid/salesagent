"""Integration tests: UC-005-MAIN-MCP-04 sorting + UC-005-MAIN-MCP-05 type filter.

Covers:
- UC-005-MAIN-MCP-04: Results sorted by format type then name
- UC-005-MAIN-MCP-05: Filter by format category (type)
"""

from __future__ import annotations

import pytest
from adcp.types.generated_poc.enums.format_category import FormatCategory

from src.core.schemas import Format, FormatId, ListCreativeFormatsRequest
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv
from tests.harness.transport import Transport

AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]

# IMPL and A2A accept req= kwarg for filtering; MCP takes individual params.
# REST build_rest_body discards filter kwargs, so REST only works for unfiltered tests.
REQ_TRANSPORTS = [Transport.IMPL, Transport.A2A]


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


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-04: Results sorted by format type then name
# ---------------------------------------------------------------------------


class TestSortingByTypeThenName:
    """UC-005-MAIN-MCP-04: Results sorted by format type then name.

    Covers: UC-005-MAIN-MCP-04

    BR: Formats are sorted first by type (alphabetical on enum value:
    audio < display < video), then by name within each type.
    """

    MIXED_FORMATS = [
        _fmt("z_audio", "Z Audio Spot", type=FormatCategory.audio),
        _fmt("a_display", "A Display Banner", type=FormatCategory.display),
        _fmt("m_video", "M Video Pre-roll", type=FormatCategory.video),
        _fmt("b_display", "B Display Skyscraper", type=FormatCategory.display),
        _fmt("a_audio", "A Audio Intro", type=FormatCategory.audio),
    ]

    # Expected order: audio(A, Z) → display(A, B) → video(M)
    EXPECTED_ORDER = [
        "a_audio",
        "z_audio",
        "a_display",
        "b_display",
        "m_video",
    ]

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_sorted_by_type_then_name(self, integration_db, transport):
        """UC-005-MAIN-MCP-04: results sorted by type then name across all transports."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.MIXED_FORMATS)

            result = env.call_via(transport)

        assert result.is_success
        actual_ids = [f.format_id.id for f in result.payload.formats]
        assert actual_ids == self.EXPECTED_ORDER

    def test_sorting_deterministic_across_calls(self, integration_db):
        """UC-005-MAIN-MCP-04: ordering is deterministic across repeated calls."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.MIXED_FORMATS)

            result_1 = env.call_impl()
            result_2 = env.call_impl()

        ids_1 = [f.format_id.id for f in result_1.formats]
        ids_2 = [f.format_id.id for f in result_2.formats]
        assert ids_1 == ids_2 == self.EXPECTED_ORDER

    def test_sorting_single_type(self, integration_db):
        """UC-005-MAIN-MCP-04: formats of one type sorted alphabetically by name."""
        formats = [
            _fmt("z_banner", "Zebra Banner"),
            _fmt("a_banner", "Alpha Banner"),
            _fmt("m_banner", "Medium Banner"),
        ]
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(formats)

            result = env.call_impl()

        actual_names = [f.name for f in result.formats]
        assert actual_names == ["Alpha Banner", "Medium Banner", "Zebra Banner"]

    def test_sorting_preserves_all_formats(self, integration_db):
        """UC-005-MAIN-MCP-04: sorting does not lose or duplicate formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.MIXED_FORMATS)

            result = env.call_impl()

        assert len(result.formats) == len(self.MIXED_FORMATS)
        actual_ids = {f.format_id.id for f in result.formats}
        expected_ids = {f.format_id.id for f in self.MIXED_FORMATS}
        assert actual_ids == expected_ids


# ---------------------------------------------------------------------------
# UC-005-MAIN-MCP-05: Filter by format category (type)
# ---------------------------------------------------------------------------


class TestTypeFilter:
    """UC-005-MAIN-MCP-05: Filter by format category (type).

    Covers: UC-005-MAIN-MCP-05

    BR: When type filter is provided, only formats of that type are returned.
    """

    ALL_FORMATS = [
        _fmt("display_banner", "Display Banner", type=FormatCategory.display),
        _fmt("display_sky", "Display Skyscraper", type=FormatCategory.display),
        _fmt("video_pre", "Video Pre-roll", type=FormatCategory.video),
        _fmt("video_mid", "Video Mid-roll", type=FormatCategory.video),
        _fmt("audio_spot", "Audio Spot", type=FormatCategory.audio),
    ]

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_video_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=video returns only video formats (IMPL/A2A)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            req = ListCreativeFormatsRequest(type="video")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == 2
        types = {f.type for f in result.payload.formats}
        assert types == {FormatCategory.video}

    def test_filter_video_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-05: type=video returns only video formats (MCP)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            result = env.call_via(Transport.MCP, type=FormatCategory.video)

        assert result.is_success
        assert len(result.payload.formats) == 2
        types = {f.type for f in result.payload.formats}
        assert types == {FormatCategory.video}

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_display_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=display returns only display formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            req = ListCreativeFormatsRequest(type="display")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == 2
        types = {f.type for f in result.payload.formats}
        assert types == {FormatCategory.display}

    def test_filter_display_via_mcp(self, integration_db):
        """UC-005-MAIN-MCP-05: type=display returns only display formats (MCP)."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            result = env.call_via(Transport.MCP, type=FormatCategory.display)

        assert result.is_success
        assert len(result.payload.formats) == 2
        types = {f.type for f in result.payload.formats}
        assert types == {FormatCategory.display}

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_audio_only(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=audio returns only audio formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            req = ListCreativeFormatsRequest(type="audio")
            result = env.call_via(transport, req=req)

        assert result.is_success
        assert len(result.payload.formats) == 1
        assert result.payload.formats[0].type == FormatCategory.audio

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS)
    def test_no_filter_returns_all(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: no type filter returns all formats."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

            result = env.call_via(transport)

        assert result.is_success
        assert len(result.payload.formats) == 5

    @pytest.mark.parametrize("transport", REQ_TRANSPORTS)
    def test_filter_excludes_other_types(self, integration_db, transport):
        """UC-005-MAIN-MCP-05: type=video excludes display and audio."""
        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats(self.ALL_FORMATS)

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
            env.set_registry_formats(self.ALL_FORMATS)

            req = ListCreativeFormatsRequest(type="display")
            result = env.call_via(transport, req=req)

        assert result.is_success
        names = [f.name for f in result.payload.formats]
        assert names == sorted(names)

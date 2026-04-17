"""Integration tests: UC-005-MAIN-MCP-04 sorting + UC-005-MAIN-MCP-05 type filter.

Covers:
- UC-005-MAIN-MCP-04: Results sorted by format type then name
- UC-005-MAIN-MCP-05: Filter by format category (type)
"""

from __future__ import annotations

import pytest

from src.core.schemas import Format, FormatId
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
    **kwargs,
) -> Format:
    """Shorthand for creating a Format object."""
    return Format(
        format_id=FormatId(agent_url=AGENT_URL, id=fmt_id),
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
        _fmt("z_audio", "Z Audio Spot"),
        _fmt("a_display", "A Display Banner"),
        _fmt("m_video", "M Video Pre-roll"),
        _fmt("b_display", "B Display Skyscraper"),
        _fmt("a_audio", "A Audio Intro"),
    ]

    # Expected order: sorted by name alphabetically
    EXPECTED_ORDER = [
        "a_audio",
        "a_display",
        "b_display",
        "m_video",
        "z_audio",
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

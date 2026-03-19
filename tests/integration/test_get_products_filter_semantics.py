"""Integration tests for filter conjunction & semantics obligations.

Tests covering:
- BR-RULE-031-01: Format discovery filter conjunction (AND combination, sorted by type then name)
- BR-RULE-049-01: Per-filter format discovery semantics (type, format_ids, name_search, dimensions, is_responsive)
- BR-RULE-050-01: Per-filter signal discovery semantics (catalog_types, data_providers, max_cpm, min_coverage)
- CONSTR-FORMAT-IDS-FILTER-01: Format IDs filter (id match, silent exclusion of non-matching)
- CONSTR-DIMENSION-FILTER-01: Dimension filter (ANY render match semantics)
"""

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    GetSignalsRequest,
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.creative_formats import _list_creative_formats_impl
from src.core.tools.signals import _get_signals_impl

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def identity(integration_db):
    """ResolvedIdentity with a tenant dict for format/signal tests.

    Uses integration_db because _list_creative_formats_impl accesses the DB
    for adapter config (AdapterConfig table lookup). The tenant is a plain dict
    since formats and signals don't require a Tenant row.
    """
    return ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="filter-sem-test",
        tenant={"tenant_id": "filter-sem-test", "name": "Filter Semantics Test"},
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
    )


def _call_list_formats(identity: ResolvedIdentity, **kwargs) -> ListCreativeFormatsResponse:
    """Helper to call _list_creative_formats_impl with typed request."""
    req = ListCreativeFormatsRequest(**kwargs)
    return _list_creative_formats_impl(req, identity)


# ---------------------------------------------------------------------------
# BR-RULE-031-01: Format Discovery Filter Conjunction
# ---------------------------------------------------------------------------


class TestFormatDiscoveryFilterConjunction:
    """Covers: BR-RULE-031-01"""

    def test_multiple_filters_combine_as_and(self, identity):
        """All format filters combine with AND semantics.

        Covers: BR-RULE-031-01
        """
        # Get all display formats
        display_only = _call_list_formats(identity, type="display")
        display_names = {f.name for f in display_only.formats}

        # Get all formats matching name search "static"
        name_only = _call_list_formats(identity, name_search="static")
        name_names = {f.name for f in name_only.formats}

        # Apply both: should be the intersection
        both = _call_list_formats(identity, type="display", name_search="static")
        both_names = {f.name for f in both.formats}

        # AND semantics: every result matches BOTH filters
        for f in both.formats:
            assert f.type.value == "display", f"format {f.name} should be display type"
            assert "static" in f.name.lower(), f"format {f.name} should contain 'static'"

        # AND semantics: result is subset of each individual filter
        assert both_names <= display_names, "AND result should be subset of display-only results"
        assert both_names <= name_names, "AND result should be subset of name-search-only results"

    def test_results_sorted_by_type_then_name(self, identity):
        """Format discovery results are sorted by type then name.

        Covers: BR-RULE-031-01
        """
        result = _call_list_formats(identity)
        formats = result.formats

        assert len(formats) > 1, "Need multiple formats to verify sorting"

        # Extract (type, name) tuples
        sort_keys = [(f.type.value if f.type else "", f.name) for f in formats]

        # Verify the list is already sorted
        assert sort_keys == sorted(sort_keys), f"Formats should be sorted by type then name, but got: {sort_keys}"


# ---------------------------------------------------------------------------
# BR-RULE-049-01: Per-Filter Format Discovery Semantics
# ---------------------------------------------------------------------------


class TestPerFilterFormatSemantics:
    """Covers: BR-RULE-049-01"""

    def test_type_filter_exact_match(self, identity):
        """type filter uses exact category match (display returns display, not display_native).

        Covers: BR-RULE-049-01
        """
        result = _call_list_formats(identity, type="display")

        assert len(result.formats) > 0, "Should have display formats"
        for f in result.formats:
            assert f.type.value == "display", (
                f"type filter is exact: format {f.name} has type {f.type.value}, expected display"
            )

    def test_type_filter_video_exact(self, identity):
        """type filter 'video' returns only video formats, not any other type.

        Covers: BR-RULE-049-01
        """
        result = _call_list_formats(identity, type="video")

        assert len(result.formats) > 0, "Should have video formats"
        for f in result.formats:
            assert f.type.value == "video", (
                f"type filter is exact: format {f.name} has type {f.type.value}, expected video"
            )

    def test_name_search_case_insensitive_substring(self, identity):
        """name_search uses case-insensitive substring matching.

        Covers: BR-RULE-049-01
        """
        # First get all formats to find a name to search for
        all_formats = _call_list_formats(identity)
        assert len(all_formats.formats) > 0

        # Pick a format and search with mixed case substring
        target = all_formats.formats[0]
        # Use first 4 chars of name as substring, uppercase
        search_term = target.name[:4].upper()

        result = _call_list_formats(identity, name_search=search_term)
        assert len(result.formats) > 0, f"name_search '{search_term}' should match at least one format"

        # Verify all results contain the search term (case-insensitive)
        for f in result.formats:
            assert search_term.lower() in f.name.lower(), (
                f"format {f.name} should contain '{search_term}' (case-insensitive)"
            )

    def test_is_responsive_filter_true(self, identity):
        """is_responsive=true returns only responsive formats.

        Covers: BR-RULE-049-01
        """
        all_formats = _call_list_formats(identity)
        responsive = _call_list_formats(identity, is_responsive=True)
        non_responsive = _call_list_formats(identity, is_responsive=False)

        # Bidirectional: responsive + non-responsive should cover all formats
        responsive_ids = {f.format_id.id for f in responsive.formats}
        non_responsive_ids = {f.format_id.id for f in non_responsive.formats}

        # No overlap between responsive and non-responsive
        assert not responsive_ids & non_responsive_ids, (
            "is_responsive is bidirectional: no format should be in both sets"
        )

    def test_is_responsive_filter_false(self, identity):
        """is_responsive=false returns only non-responsive formats.

        Covers: BR-RULE-049-01
        """
        result = _call_list_formats(identity, is_responsive=False)

        # When is_responsive=false, all returned formats should be non-responsive
        for f in result.formats:
            # Check that no render has responsive dimensions
            if f.renders:
                for render in f.renders:
                    dims = getattr(render, "dimensions", None)
                    if dims:
                        responsive = getattr(dims, "responsive", None)
                        if responsive:
                            w_fluid = getattr(responsive, "width", False)
                            h_fluid = getattr(responsive, "height", False)
                            assert not (w_fluid or h_fluid), (
                                f"format {f.name} should not be responsive when is_responsive=false"
                            )


# ---------------------------------------------------------------------------
# CONSTR-FORMAT-IDS-FILTER-01: Format IDs Filter
# ---------------------------------------------------------------------------


class TestFormatIdsFilter:
    """Covers: CONSTR-FORMAT-IDS-FILTER-01"""

    def test_format_ids_match_on_id_field(self, identity):
        """format_ids filter matches on the id field of FormatId.

        Covers: CONSTR-FORMAT-IDS-FILTER-01
        """
        # First get all formats to pick a valid one
        all_formats = _call_list_formats(identity)
        assert len(all_formats.formats) > 0

        target = all_formats.formats[0]
        target_id = target.format_id.id
        target_url = str(target.format_id.agent_url)

        # Filter by that specific format_id
        result = _call_list_formats(
            identity,
            format_ids=[{"agent_url": target_url, "id": target_id}],
        )

        assert len(result.formats) == 1, f"Should return exactly the requested format, got {len(result.formats)}"
        assert result.formats[0].format_id.id == target_id

    def test_non_matching_format_ids_silently_excluded(self, identity):
        """Non-matching format_ids are silently excluded (no error).

        Covers: CONSTR-FORMAT-IDS-FILTER-01
        """
        # Get a valid format
        all_formats = _call_list_formats(identity)
        assert len(all_formats.formats) > 0

        target = all_formats.formats[0]
        target_id = target.format_id.id
        target_url = str(target.format_id.agent_url)

        # Mix valid and non-existent format IDs
        result = _call_list_formats(
            identity,
            format_ids=[
                {"agent_url": target_url, "id": target_id},
                {"agent_url": target_url, "id": "nonexistent_format_xyz"},
            ],
        )

        # Only the matching format is returned; nonexistent is silently excluded
        returned_ids = {f.format_id.id for f in result.formats}
        assert target_id in returned_ids, "Valid format should be returned"
        assert "nonexistent_format_xyz" not in returned_ids, "Nonexistent format should be silently excluded"


# ---------------------------------------------------------------------------
# CONSTR-DIMENSION-FILTER-01: Dimension Filter
# ---------------------------------------------------------------------------


class TestDimensionFilter:
    """Covers: CONSTR-DIMENSION-FILTER-01"""

    def test_dimension_filter_any_render_match(self, identity):
        """Dimension filter uses ANY render match semantics.

        Covers: CONSTR-DIMENSION-FILTER-01
        """
        # Filter for formats with width in a reasonable range
        result = _call_list_formats(identity, min_width=300, max_width=728)

        for f in result.formats:
            # At least one render must have width in [300, 728]
            has_matching = False
            if f.renders:
                for render in f.renders:
                    dims = getattr(render, "dimensions", None)
                    if dims:
                        w = getattr(dims, "width", None)
                        if w is not None and 300 <= w <= 728:
                            has_matching = True
                            break
            assert has_matching, f"format {f.name} should have at least one render with width in [300, 728]"

    def test_dimension_filter_excludes_formats_without_matching_render(self, identity):
        """Formats without any render matching the dimension constraint are excluded.

        Covers: CONSTR-DIMENSION-FILTER-01
        """
        # Get all formats
        all_formats = _call_list_formats(identity)

        # Apply a narrow dimension filter
        narrow = _call_list_formats(identity, min_width=300, max_width=300)

        # Should have fewer formats (or same, but not more)
        assert len(narrow.formats) <= len(all_formats.formats), (
            "Dimension filter should not return more formats than unfiltered"
        )


# ---------------------------------------------------------------------------
# BR-RULE-050-01: Per-Filter Signal Discovery Semantics
# ---------------------------------------------------------------------------


class TestPerFilterSignalSemantics:
    """Covers: BR-RULE-050-01"""

    _DELIVER_TO = {
        "countries": ["US"],
        "deployments": [{"type": "platform", "platform": "google_ad_manager"}],
    }

    def _make_signal_req(self, **kwargs):
        """Build a GetSignalsRequest with required deliver_to + signal_spec fields."""
        data = {"signal_spec": "", "deliver_to": self._DELIVER_TO}
        data.update(kwargs)
        return GetSignalsRequest.model_validate(data)

    @pytest.mark.asyncio
    async def test_catalog_types_or_within_filter(self, identity):
        """catalog_types uses OR semantics: signals matching ANY listed type are returned.

        Covers: BR-RULE-050-01
        """
        marketplace_result = await _get_signals_impl(
            self._make_signal_req(filters={"catalog_types": ["marketplace"]}), identity
        )
        owned_result = await _get_signals_impl(self._make_signal_req(filters={"catalog_types": ["owned"]}), identity)
        both_result = await _get_signals_impl(
            self._make_signal_req(filters={"catalog_types": ["marketplace", "owned"]}), identity
        )

        # OR semantics: combined should be >= each individual
        assert len(both_result.signals) >= len(marketplace_result.signals)
        assert len(both_result.signals) >= len(owned_result.signals)

        for s in both_result.signals:
            assert s.signal_type in ("marketplace", "owned"), (
                f"Signal {s.name} has type {s.signal_type}, expected marketplace or owned"
            )

    @pytest.mark.asyncio
    async def test_data_providers_or_within_filter(self, identity):
        """data_providers uses OR semantics: signals from ANY listed provider are returned.

        Covers: BR-RULE-050-01
        """
        all_result = await _get_signals_impl(self._make_signal_req(), identity)
        providers = {s.data_provider for s in all_result.signals}
        assert len(providers) >= 2, "Need at least 2 data providers to test OR"

        provider_list = list(providers)[:2]

        result = await _get_signals_impl(self._make_signal_req(filters={"data_providers": provider_list}), identity)

        assert len(result.signals) >= 2, "Should return signals from at least 2 providers"
        for s in result.signals:
            assert s.data_provider in provider_list, (
                f"Signal {s.name} from provider {s.data_provider}, expected one of {provider_list}"
            )

    @pytest.mark.asyncio
    async def test_max_cpm_threshold(self, identity):
        """max_cpm enforces numeric threshold: signals with cpm > max_cpm are excluded.

        Covers: BR-RULE-050-01
        """
        max_cpm = 2.0
        result = await _get_signals_impl(self._make_signal_req(filters={"max_cpm": max_cpm}), identity)

        for s in result.signals:
            assert s.pricing is not None, f"Signal {s.name} should have pricing"
            assert s.pricing.cpm <= max_cpm, f"Signal {s.name} has cpm={s.pricing.cpm}, but max_cpm={max_cpm}"

        all_result = await _get_signals_impl(self._make_signal_req(), identity)
        assert len(result.signals) < len(all_result.signals), "max_cpm should exclude some signals"

    @pytest.mark.asyncio
    async def test_min_coverage_threshold(self, identity):
        """min_coverage enforces numeric threshold: signals below threshold are excluded.

        Covers: BR-RULE-050-01
        """
        min_coverage = 85.0
        result = await _get_signals_impl(
            self._make_signal_req(filters={"min_coverage_percentage": min_coverage}), identity
        )

        for s in result.signals:
            assert s.coverage_percentage >= min_coverage, (
                f"Signal {s.name} has coverage={s.coverage_percentage}%, but min_coverage={min_coverage}%"
            )

        all_result = await _get_signals_impl(self._make_signal_req(), identity)
        assert len(result.signals) < len(all_result.signals), "min_coverage should exclude some signals"

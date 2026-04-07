"""Integration tests for filter conjunction & semantics obligations.

Tests covering:
- BR-RULE-031-01: Format discovery filter conjunction (AND combination, sorted by type then name)
- BR-RULE-049-01: Per-filter format discovery semantics (type, format_ids, name_search, dimensions, is_responsive)
- BR-RULE-050-01: Per-filter signal discovery semantics (catalog_types, data_providers, max_cpm, min_coverage)
- CONSTR-FORMAT-IDS-FILTER-01: Format IDs filter (id match, silent exclusion of non-matching)
- CONSTR-DIMENSION-FILTER-01: Dimension filter (ANY render match semantics)
"""

import pytest

from src.core.schemas import (
    GetSignalsRequest,
    ListCreativeFormatsRequest,
    ListCreativeFormatsResponse,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.signals import _get_signals_impl
from tests.harness.creative_formats import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _call_list_formats(env: CreativeFormatsEnv, **kwargs) -> ListCreativeFormatsResponse:
    """Helper to call _list_creative_formats_impl via harness with typed request."""
    req = ListCreativeFormatsRequest(**kwargs)
    return env.call_impl(req=req)


# ---------------------------------------------------------------------------
# BR-RULE-031-01: Format Discovery Filter Conjunction
# ---------------------------------------------------------------------------


class TestFormatDiscoveryFilterConjunction:
    """Covers: BR-RULE-031-01"""

    def test_multiple_filters_combine_as_and(self, integration_db):
        """All format filters combine with AND semantics.

        Covers: BR-RULE-031-01
        """
        with CreativeFormatsEnv() as env:
            # Get all display formats
            display_only = _call_list_formats(env, type="display")
            display_names = {f.name for f in display_only.formats}

            # Get all formats matching name search "HTML" (matches display HTML formats)
            name_only = _call_list_formats(env, name_search="HTML")
            name_names = {f.name for f in name_only.formats}

            # Apply both: should be the intersection
            both = _call_list_formats(env, type="display", name_search="HTML")
            both_names = {f.name for f in both.formats}

            # AND semantics: every result matches BOTH filters
            for f in both.formats:
                assert f.type.value == "display", f"format {f.name} should be display type"
                assert "html" in f.name.lower(), f"format {f.name} should contain 'HTML'"

            # AND semantics: result is subset of each individual filter
            assert both_names <= display_names, "AND result should be subset of display-only results"
            assert both_names <= name_names, "AND result should be subset of name-search-only results"

    def test_results_sorted_by_type_then_name(self, integration_db):
        """Format discovery results are sorted by type then name.

        Covers: BR-RULE-031-01
        """
        with CreativeFormatsEnv() as env:
            result = _call_list_formats(env)
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

    def test_type_filter_exact_match(self, integration_db):
        """type filter uses exact category match (display returns display, not display_native).

        Covers: BR-RULE-049-01
        """
        with CreativeFormatsEnv() as env:
            result = _call_list_formats(env, type="display")

            assert len(result.formats) > 0, "Should have display formats"
            for f in result.formats:
                assert f.type.value == "display", (
                    f"type filter is exact: format {f.name} has type {f.type.value}, expected display"
                )

    def test_type_filter_video_exact(self, integration_db):
        """type filter 'video' returns only video formats, not any other type.

        Covers: BR-RULE-049-01
        """
        with CreativeFormatsEnv() as env:
            result = _call_list_formats(env, type="video")

            assert len(result.formats) > 0, "Should have video formats"
            for f in result.formats:
                assert f.type.value == "video", (
                    f"type filter is exact: format {f.name} has type {f.type.value}, expected video"
                )

    def test_name_search_case_insensitive_substring(self, integration_db):
        """name_search uses case-insensitive substring matching.

        Covers: BR-RULE-049-01
        """
        with CreativeFormatsEnv() as env:
            # First get all formats to find a name to search for
            all_formats = _call_list_formats(env)
            assert len(all_formats.formats) > 0

            # Pick a format and search with mixed case substring
            target = all_formats.formats[0]
            # Use first 4 chars of name as substring, uppercase
            search_term = target.name[:4].upper()

            result = _call_list_formats(env, name_search=search_term)
            assert len(result.formats) > 0, f"name_search '{search_term}' should match at least one format"

            # Verify all results contain the search term (case-insensitive)
            for f in result.formats:
                assert search_term.lower() in f.name.lower(), (
                    f"format {f.name} should contain '{search_term}' (case-insensitive)"
                )

    def test_is_responsive_filter_true(self, integration_db):
        """is_responsive=true and is_responsive=false return disjoint sets.

        Covers: BR-RULE-049-01
        """
        with CreativeFormatsEnv() as env:
            responsive = _call_list_formats(env, is_responsive=True)
            non_responsive = _call_list_formats(env, is_responsive=False)

            # Bidirectional: the two filter results must not overlap
            responsive_ids = {f.format_id.id for f in responsive.formats}
            non_responsive_ids = {f.format_id.id for f in non_responsive.formats}

            # No overlap between responsive and non-responsive
            assert not responsive_ids & non_responsive_ids, (
                "is_responsive is bidirectional: no format should be in both sets"
            )

            # Every format returned by is_responsive=True must actually be responsive
            for f in responsive.formats:
                has_responsive_render = False
                if f.renders:
                    for render in f.renders:
                        dims = getattr(render, "dimensions", None)
                        if dims:
                            responsive_dims = getattr(dims, "responsive", None)
                            if responsive_dims and (
                                getattr(responsive_dims, "width", False) or getattr(responsive_dims, "height", False)
                            ):
                                has_responsive_render = True
                                break
                assert has_responsive_render, (
                    f"format {f.name} was returned by is_responsive=True but has no responsive render"
                )

    def test_is_responsive_filter_false(self, integration_db):
        """is_responsive=false returns only non-responsive formats.

        Covers: BR-RULE-049-01
        """
        with CreativeFormatsEnv() as env:
            result = _call_list_formats(env, is_responsive=False)

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

    def test_format_ids_match_on_id_field(self, integration_db):
        """format_ids filter matches on the id field of FormatId.

        Covers: CONSTR-FORMAT-IDS-FILTER-01
        """
        with CreativeFormatsEnv() as env:
            # First get all formats to pick a valid one
            all_formats = _call_list_formats(env)
            assert len(all_formats.formats) > 0

            target = all_formats.formats[0]
            target_id = target.format_id.id
            target_url = str(target.format_id.agent_url)

            # Filter by that specific format_id
            result = _call_list_formats(
                env,
                format_ids=[{"agent_url": target_url, "id": target_id}],
            )

            assert len(result.formats) == 1, f"Should return exactly the requested format, got {len(result.formats)}"
            assert result.formats[0].format_id.id == target_id

    def test_non_matching_format_ids_silently_excluded(self, integration_db):
        """Non-matching format_ids are silently excluded (no error).

        Covers: CONSTR-FORMAT-IDS-FILTER-01
        """
        with CreativeFormatsEnv() as env:
            # Get a valid format
            all_formats = _call_list_formats(env)
            assert len(all_formats.formats) > 0

            target = all_formats.formats[0]
            target_id = target.format_id.id
            target_url = str(target.format_id.agent_url)

            # Mix valid and non-existent format IDs
            result = _call_list_formats(
                env,
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

    def test_dimension_filter_any_render_match(self, integration_db):
        """Dimension filter uses ANY render match semantics.

        Covers: CONSTR-DIMENSION-FILTER-01
        """
        with CreativeFormatsEnv() as env:
            # Filter for formats with width in a broad range; every returned format must satisfy it
            result = _call_list_formats(env, min_width=1, max_width=9999)

            for f in result.formats:
                # At least one render must have width in [1, 9999]
                has_matching = False
                if f.renders:
                    for render in f.renders:
                        dims = getattr(render, "dimensions", None)
                        if dims:
                            w = getattr(dims, "width", None)
                            if w is not None and 1 <= w <= 9999:
                                has_matching = True
                                break
                assert has_matching, f"format {f.name} was returned by dimension filter but has no matching render"

    def test_dimension_filter_excludes_formats_without_matching_render(self, integration_db):
        """Formats without any render matching the dimension constraint are excluded.

        Covers: CONSTR-DIMENSION-FILTER-01
        """
        with CreativeFormatsEnv() as env:
            # Get all formats (no dimension filter)
            all_formats = _call_list_formats(env)

            # Apply a dimension filter — formats without render dimension data are excluded
            filtered = _call_list_formats(env, min_width=1)

            # Dimension filter should return no more formats than unfiltered
            assert len(filtered.formats) <= len(all_formats.formats), (
                "Dimension filter should not return more formats than unfiltered"
            )

            # Every format returned must have at least one render with a width value
            for f in filtered.formats:
                has_width = False
                if f.renders:
                    for render in f.renders:
                        dims = getattr(render, "dimensions", None)
                        if dims and getattr(dims, "width", None) is not None:
                            has_width = True
                            break
                assert has_width, f"format {f.name} was returned by min_width filter but has no render with a width"


# ---------------------------------------------------------------------------
# BR-RULE-050-01: Per-Filter Signal Discovery Semantics
# ---------------------------------------------------------------------------


class TestPerFilterSignalSemantics:
    """Covers: BR-RULE-050-01"""

    _DELIVER_TO = {
        "countries": ["US"],
        "deployments": [{"type": "platform", "platform": "google_ad_manager"}],
    }

    def _make_identity(self):
        from src.core.resolved_identity import ResolvedIdentity

        return ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="filter-sem-test",
            tenant={"tenant_id": "filter-sem-test", "name": "Filter Semantics Test"},
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        )

    def _make_signal_req(self, **kwargs):
        """Build a GetSignalsRequest with required deliver_to + signal_spec fields."""
        data = {"signal_spec": "", "deliver_to": self._DELIVER_TO}
        data.update(kwargs)
        return GetSignalsRequest.model_validate(data)

    @pytest.mark.asyncio
    async def test_catalog_types_or_within_filter(self, integration_db):
        """catalog_types uses OR semantics: signals matching ANY listed type are returned.

        Covers: BR-RULE-050-01
        """
        identity = self._make_identity()
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
    async def test_data_providers_or_within_filter(self, integration_db):
        """data_providers uses OR semantics: signals from ANY listed provider are returned.

        Covers: BR-RULE-050-01
        """
        identity = self._make_identity()
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
    async def test_max_cpm_threshold(self, integration_db):
        """max_cpm enforces numeric threshold: signals with cpm > max_cpm are excluded.

        Covers: BR-RULE-050-01
        """
        identity = self._make_identity()
        max_cpm = 2.0
        result = await _get_signals_impl(self._make_signal_req(filters={"max_cpm": max_cpm}), identity)

        for s in result.signals:
            assert s.pricing is not None, f"Signal {s.name} should have pricing"
            assert s.pricing.cpm <= max_cpm, f"Signal {s.name} has cpm={s.pricing.cpm}, but max_cpm={max_cpm}"

        all_result = await _get_signals_impl(self._make_signal_req(), identity)
        assert len(result.signals) < len(all_result.signals), "max_cpm should exclude some signals"

    @pytest.mark.asyncio
    async def test_min_coverage_threshold(self, integration_db):
        """min_coverage enforces numeric threshold: signals below threshold are excluded.

        Covers: BR-RULE-050-01
        """
        identity = self._make_identity()
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

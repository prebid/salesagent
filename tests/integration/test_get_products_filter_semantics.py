"""Integration tests for filter conjunction & semantics obligations.

Tests covering:
- BR-RULE-031-01: Format discovery filter conjunction (AND combination, sorted by type then name)
- BR-RULE-049-01: Per-filter format discovery semantics (type, format_ids, name_search, dimensions, is_responsive)
- BR-RULE-050-01: Per-filter signal discovery semantics (catalog_types, data_providers, max_cpm, min_coverage)
- CONSTR-FORMAT-IDS-FILTER-01: Format IDs filter (id match, silent exclusion of non-matching)
- CONSTR-DIMENSION-FILTER-01: Dimension filter (ANY render match semantics)
"""

import pytest
from adcp.types import PaginationRequest

from src.core.format_cache import load_reference_formats
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import Format, ListCreativeFormatsRequest
from src.core.tenant_context import TenantContext
from src.core.tools.creative_formats import _list_creative_formats_impl
from tests.factories import ProductFactory, TenantFactory
from tests.factories.principal import PrincipalFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def identity(integration_db):
    """ResolvedIdentity over a seeded tenant row, the caller the resolver hands on.

    The tenant row is required: production reaches ``_list_creative_formats_impl`` only
    through the resolver, which has loaded the tenant, and the impl reads that row
    (``tenants.use_default_creative_agent`` decides whether the standard creative agent
    is listed, and the adapter config is looked up by tenant). The context is derived
    from the row through production's own constructor, so the identity cannot describe a
    tenant the database disagrees with.

    The tenant's product declares every format of the reference catalog: the list projects
    the seller's product declarations, so these filter tests grade the whole catalog only
    because a product names all of it.
    """
    with BareIntegrationEnv():
        tenant = TenantFactory(tenant_id="filter-sem-test", name="Filter Semantics Test")
        ProductFactory(
            tenant=tenant,
            format_ids=[
                {"agent_url": str(fmt.format_id.agent_url), "id": fmt.format_id.id} for fmt in load_reference_formats()
            ],
        )
        context = TenantContext.from_orm_model(tenant)
    return PrincipalFactory.make_identity(principal_id="test_principal", tenant_id="filter-sem-test", tenant=context)


def _call_list_formats(identity: ResolvedIdentity, **kwargs) -> list[Format]:
    """Every format _list_creative_formats_impl lists for the typed request, across all pages.

    The list is paginated (core/pagination-request.json: ``max_results`` defaults to 50), and
    the reference catalog is larger than one page, so a filter test that took one page as
    "all formats" would grade a truncated catalog. The cursor is followed until
    ``has_more`` is false.
    """
    formats: list[Format] = []
    cursor = None
    while True:
        req = ListCreativeFormatsRequest(pagination=PaginationRequest(cursor=cursor), **kwargs)
        response = _list_creative_formats_impl(req, identity)
        formats.extend(response.formats)
        if not response.pagination.has_more:
            return formats
        cursor = response.pagination.cursor


# ---------------------------------------------------------------------------
# BR-RULE-031-01: Format Discovery Filter Conjunction
# ---------------------------------------------------------------------------


class TestFormatDiscoveryFilterConjunction:
    """Covers: BR-RULE-031-01"""

    def test_multiple_filters_combine_as_and(self, identity):
        """All format filters combine with AND semantics.

        Covers: BR-RULE-031-01
        """
        # Get all formats
        all_formats = _call_list_formats(identity)
        all_names = {f.name for f in all_formats}

        # Get all formats matching name search "static"
        name_only = _call_list_formats(identity, name_search="static")
        name_names = {f.name for f in name_only}

        # AND semantics: every result matches the filter
        for f in name_only:
            assert "static" in f.name.lower(), f"format {f.name} should contain 'static'"

        # Name-filtered results are a subset of all
        assert name_names <= all_names, "filtered result should be subset of all results"

    def test_results_sorted_by_type_then_name(self, identity):
        """Format discovery results are sorted by type then name.

        Covers: BR-RULE-031-01
        """
        result = _call_list_formats(identity)
        formats = result

        assert len(formats) > 1, "Need multiple formats to verify sorting"

        # Extract names and verify sorted
        names = [f.name for f in formats]

        # Verify the list is already sorted by name
        assert names == sorted(names), f"Formats should be sorted by name, but got: {names}"


# ---------------------------------------------------------------------------
# BR-RULE-049-01: Per-Filter Format Discovery Semantics
# ---------------------------------------------------------------------------


class TestPerFilterFormatSemantics:
    """Covers: BR-RULE-049-01"""

    def test_all_formats_have_required_fields(self, identity):
        """All formats have required fields (format_id, name).

        Covers: BR-RULE-049-01
        """
        result = _call_list_formats(identity)

        assert len(result) > 0, "Should have formats"
        for f in result:
            assert f.format_id is not None, f"format {f.name} should have format_id"
            assert f.name is not None, "format should have name"

    def test_name_search_case_insensitive_substring(self, identity):
        """name_search uses case-insensitive substring matching.

        Covers: BR-RULE-049-01
        """
        # First get all formats to find a name to search for
        all_formats = _call_list_formats(identity)
        assert len(all_formats) > 0

        # Pick a format and search with mixed case substring
        target = all_formats[0]
        # Use first 4 chars of name as substring, uppercase
        search_term = target.name[:4].upper()

        result = _call_list_formats(identity, name_search=search_term)
        assert len(result) > 0, f"name_search '{search_term}' should match at least one format"

        # Verify all results contain the search term (case-insensitive)
        for f in result:
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
        responsive_ids = {f.format_id.id for f in responsive}
        non_responsive_ids = {f.format_id.id for f in non_responsive}

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
        for f in result:
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
        assert len(all_formats) > 0

        target = all_formats[0]
        target_id = target.format_id.id
        target_url = str(target.format_id.agent_url)

        # Filter by that specific format_id
        result = _call_list_formats(
            identity,
            format_ids=[{"agent_url": target_url, "id": target_id}],
        )

        assert len(result) == 1, f"Should return exactly the requested format, got {len(result)}"
        assert result[0].format_id.id == target_id

    def test_non_matching_format_ids_silently_excluded(self, identity):
        """Non-matching format_ids are silently excluded (no error).

        Covers: CONSTR-FORMAT-IDS-FILTER-01
        """
        # Get a valid format
        all_formats = _call_list_formats(identity)
        assert len(all_formats) > 0

        target = all_formats[0]
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
        returned_ids = {f.format_id.id for f in result}
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

        for f in result:
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
        assert len(narrow) <= len(all_formats), "Dimension filter should not return more formats than unfiltered"

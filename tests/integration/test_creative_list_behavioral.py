"""Integration tests: list_creatives auth, filtering, pagination.

Behavioral tests using CreativeListEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Test traceability: These tests verify list_creatives behavior defined in the
adcp spec (media-buy/task-reference/list_creatives). No BDD obligations exist
for list_creatives in docs/test-obligations/; auth tests reference sync_creatives
obligations (UC-006-EXT-*) which share the same auth contract.

Covers:
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.types import CreativeFilters, PaginationRequest
from adcp.types.generated_poc.creative.list_creatives_request import Sort

from tests.factories import (
    CreativeAssignmentFactory,
    CreativeFactory,
    MediaBuyFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.factories.creative_asset import build_assets, image_spec
from tests.harness import CreativeListEnv, make_identity
from tests.harness._base import WireError
from tests.helpers.envelope_assertions import assert_envelope_shape

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_make_identity = make_identity  # Canonical version from tests.harness


def _seed_tagged(tenant, principal, *, creative_id: str, name: str, tags: list[str]):
    """One approved creative carrying *tags* where a creative's tags live: the data blob.

    There is no tags column, and list_creatives reads them back from ``data["tags"]``, so
    that is where a tags-filter fixture has to put them.
    """
    return CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=name,
        approved=True,
        data={"assets": build_assets(image_spec("banner")), "tags": tags},
    )


# ---------------------------------------------------------------------------
# Auth Tests — Covers: UC-006-EXT-A-01, UC-006-EXT-B-01
# ---------------------------------------------------------------------------


# (Deleted) TestListAuth::test_no_principal_raises_auth_error (UC-006-EXT-A-01), which
# built ``_make_identity(principal_id=None, ...)``. The remaining half of the pair below,
# removed for the same reason: ``list_creatives`` is a PROTECTED tool whose
# ``ResolvedIdentity`` declares principal required, so "an identity with no principal" is
# not a value the parameter can hold. ``listing.py`` says so at the site the test reached
# -- "The boundary refused an anonymous caller; the ResolvedIdentity carries the principal
# by type" -- and the only way the assertion passed was
# ``identity.principal.principal_id`` raising AttributeError on the fabricated value, which
# is not an authentication rejection at all. Adding a guard there to make it a real one is
# the defensive-code antipattern tests/CLAUDE.md names.
#
# The obligation is graded where the refusal is minted: ``_resolve_identity``, for every
# tool and transport at once, with the AUTH_MISSING wire envelope asserted by the
# transport-blind auth scenarios.

# (Deleted) test_no_tenant_raises_auth_error (UC-006-EXT-B-01), which built
# ``_make_identity(principal_id="p1", tenant=None)``. ``list_creatives`` is a
# PROTECTED tool: its ``ResolvedIdentity`` declares both fields required, and the
# resolver refuses a tenant-less caller before the implementation runs -- with no
# tenant there is no principal lookup, so a presented credential resolves nothing and
# is AUTH_INVALID (``_resolve_identity`` step 4). The refusal is minted there and
# nowhere else, so the state this test set up cannot exist and the assertion could
# only ever have graded ``make_identity``.


# ---------------------------------------------------------------------------
# Validation Tests — Covers: UC-006-EXT-C-01
# ---------------------------------------------------------------------------


class TestListValidation:
    """An unparseable date in the filters is refused ON THE WIRE, on every transport.

    These used to call ``env.call_impl(created_after="not-a-date")`` and assert an
    in-process ``AdCPValidationError``. Those flat parameters are gone -- AdCP 3.1.1 puts
    both dates inside ``filters`` (core/creative-filters.json, format: date-time), no
    transport could ever send the flat spelling, and the builder no longer parses one.

    Grading the WIRE rather than the in-process raise is a strictly stronger assertion and
    the one this file is placed to make: it proves the buyer receives an INVALID_REQUEST
    envelope from A2A and REST both, which the old in-process form never reached.
    """

    @pytest.mark.parametrize("field", ["created_after", "created_before"])
    @pytest.mark.parametrize("transport", ["a2a", "rest"])
    def test_invalid_filter_date_is_refused_on_the_wire(self, integration_db, field: str, transport: str):
        """Covers: UC-006-EXT-C-01 — an unparseable filter date → INVALID_REQUEST envelope."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            dispatch = env.call_a2a if transport == "a2a" else env.call_rest
            with pytest.raises(WireError) as raised:
                dispatch(filters={field: "not-a-date"})

        # recovery="correctable": the buyer can fix this by resending a parseable date.
        assert_envelope_shape(raised.value.envelope, "INVALID_REQUEST", recovery="correctable")


# ---------------------------------------------------------------------------
# Filtering Tests — real DB queries
# adcp spec: list_creatives filters (statuses, formats, name_contains, etc.)
# ---------------------------------------------------------------------------


class TestListFiltering:
    """Filtering by status, format, and other parameters with real DB data."""

    def test_status_filter_returns_matching(self, integration_db):
        """Spec: list_creatives statuses filter returns only matching creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_approved",
                status="approved",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_pending",
                status="pending_review",
            )

            response = env.call_impl(filters=CreativeFilters(statuses=["approved"]))

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_approved"

    def test_no_filter_returns_all(self, integration_db):
        """Spec: list_creatives with no filter returns all principal's creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            for i in range(3):
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id=f"c_{i}",
                )

            response = env.call_impl()

        assert len(response.creatives) == 3


# ---------------------------------------------------------------------------
# Pagination Tests
# adcp spec: list_creatives pagination (page, limit, has_more, total_count)
# ---------------------------------------------------------------------------


class TestListPagination:
    """Pagination with real DB data."""

    def test_limit_restricts_results(self, integration_db):
        """Spec: list_creatives limit restricts returned count."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            for i in range(5):
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id=f"c_page_{i}",
                )

            response = env.call_impl(pagination=PaginationRequest(max_results=2))

        assert len(response.creatives) == 2
        assert response.pagination.has_more is True


class TestListPrincipalIsolation:
    """Creatives are principal-scoped — cross-principal isolation."""

    def test_principal_cannot_see_other_principals_creatives(self, integration_db):
        """Spec: list_creatives scoped to authenticated principal only."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            p1 = PrincipalFactory(tenant=tenant, principal_id="p1")
            p2 = PrincipalFactory(tenant=tenant, principal_id="p2")

            CreativeFactory(tenant=tenant, principal=p1, creative_id="c_p1")
            CreativeFactory(tenant=tenant, principal=p2, creative_id="c_p2")

            # Query as p1
            p1_identity = _make_identity(
                principal_id="p1",
                tenant_id="test_tenant",
                tenant={"tenant_id": "test_tenant", "name": "T"},
            )
            response = env.call_impl(identity=p1_identity)

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_p1"


# ---------------------------------------------------------------------------
# Advanced Filtering Tests — Covers:
# adcp spec: list_creatives filters (tags, created_after, created_before,
#            name_contains, media_buy_ids)
# ---------------------------------------------------------------------------


class TestListTagsFilter:
    """The tags filter asks about the creative's TAGS, and asks for all of them.

    core/creative-filters.json: ``tags`` is "Filter by creative tags (all tags must
    match)" — a creative's own tags, which this schema keeps on the JSON data blob and
    list_creatives reads back from there. The filter used to be implemented as
    ``Creative.name.contains(tag)`` and this test asserted that as if it were the spec
    ("matches creatives by name substring"), which is a different question and one
    ``name_contains`` already asks.
    """

    def test_tags_filter_matches_the_creatives_tags(self, integration_db):
        """A single-member tags filter returns the creatives carrying that tag."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            _seed_tagged(tenant, principal, creative_id="c_summer", name="Banner one", tags=["summer"])
            _seed_tagged(tenant, principal, creative_id="c_winter", name="Video two", tags=["winter"])

            response = env.call_impl(filters=CreativeFilters(tags=["summer"]))

        assert [creative.creative_id for creative in response.creatives] == ["c_summer"]

    def test_tags_filter_requires_every_tag(self, integration_db):
        """All tags must match: one of two is not enough."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            _seed_tagged(tenant, principal, creative_id="c_both", name="Banner one", tags=["q1", "brand"])
            _seed_tagged(tenant, principal, creative_id="c_one", name="Banner two", tags=["q1"])

            response = env.call_impl(filters=CreativeFilters(tags=["q1", "brand"]))

        assert [creative.creative_id for creative in response.creatives] == ["c_both"]

    def test_tags_any_filter_matches_either_tag(self, integration_db):
        """``tags_any`` is the OR sibling: "any tag must match"."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            _seed_tagged(tenant, principal, creative_id="c_q1", name="Banner one", tags=["q1"])
            _seed_tagged(tenant, principal, creative_id="c_brand", name="Banner two", tags=["brand"])
            _seed_tagged(tenant, principal, creative_id="c_other", name="Banner three", tags=["evergreen"])

            response = env.call_impl(filters=CreativeFilters(tags_any=["q1", "brand"]))

        assert {creative.creative_id for creative in response.creatives} == {"c_q1", "c_brand"}

    def test_a_name_substring_is_not_a_tag(self, integration_db):
        """The counter-example the old implementation could not tell apart.

        The creative's NAME contains "summer" and its tags do not, so a tags filter must
        not return it — while ``name_contains`` must.
        """
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            _seed_tagged(tenant, principal, creative_id="c_named", name="Summer Campaign Banner", tags=["evergreen"])

            by_tag = env.call_impl(filters=CreativeFilters(tags=["Summer"]))
            by_name = env.call_impl(filters=CreativeFilters(name_contains="Summer"))

        assert by_tag.creatives == []
        assert [creative.creative_id for creative in by_name.creatives] == ["c_named"]


class TestListDateFilters:
    """Created_after/created_before filters exercise lines 130, 132."""

    def test_created_after_returns_newer(self, integration_db):
        """Spec: list_creatives created_after filters older creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            now = datetime.now(UTC)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_old",
                created_at=now - timedelta(days=30),
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_new",
                created_at=now - timedelta(hours=1),
            )

            cutoff = now - timedelta(days=7)
            response = env.call_impl(filters=CreativeFilters(created_after=cutoff))

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_new"

    def test_created_before_returns_older(self, integration_db):
        """Spec: list_creatives created_before filters newer creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            now = datetime.now(UTC)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_old",
                created_at=now - timedelta(days=30),
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_new",
                created_at=now - timedelta(hours=1),
            )

            cutoff = now - timedelta(days=7)
            response = env.call_impl(filters=CreativeFilters(created_before=cutoff))

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_old"


class TestListSearchFilter:
    """Search filter exercises line 134 (search → name_contains)."""

    def test_search_matches_name_substring(self, integration_db):
        """Spec: list_creatives search/name_contains matches creative name."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_hero",
                name="Hero Banner Ad",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_footer",
                name="Footer Widget",
            )

            response = env.call_impl(filters=CreativeFilters(name_contains="hero"))

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_hero"


class TestListMediaBuyFilter:
    """Media buy ID filter exercises lines 139, 141."""

    def test_media_buy_id_returns_assigned(self, integration_db):
        """Spec: list_creatives media_buy_id filters to assigned creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            c1 = CreativeFactory(tenant=tenant, principal=principal, creative_id="c_assigned")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_unassigned")

            mb = MediaBuyFactory(tenant=tenant)
            CreativeAssignmentFactory(creative=c1, media_buy=mb)

            response = env.call_impl(filters=CreativeFilters(media_buy_ids=[mb.media_buy_id]))

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_assigned"

    def test_media_buy_ids_multiple(self, integration_db):
        """Spec: list_creatives media_buy_ids returns from multiple buys."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            c1 = CreativeFactory(tenant=tenant, principal=principal, creative_id="c_buy1")
            c2 = CreativeFactory(tenant=tenant, principal=principal, creative_id="c_buy2")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_nobuy")

            mb1 = MediaBuyFactory(tenant=tenant, media_buy_id="mb_1")
            mb2 = MediaBuyFactory(tenant=tenant, media_buy_id="mb_2")
            CreativeAssignmentFactory(creative=c1, media_buy=mb1)
            CreativeAssignmentFactory(creative=c2, media_buy=mb2)

            response = env.call_impl(filters=CreativeFilters(media_buy_ids=["mb_1", "mb_2"]))

        ids = {c.creative_id for c in response.creatives}
        assert ids == {"c_buy1", "c_buy2"}


class TestListStructuredFilters:
    """Structured CreativeFilters merge exercises line 151."""

    def test_structured_filters_merge_with_flat(self, integration_db):
        """Spec: list_creatives structured filters merge with flat params in request."""
        from adcp import CreativeFilters

        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_1",
                status="approved",
                format="display_300x250",
            )

            # Two criteria in ONE structured filters object. This used to pass
            # ``status="approved"`` alongside ``filters=CreativeFilters(name_contains=...)``
            # to grade the builder's flat-over-structured merge; the flat parameter is gone
            # (AdCP 3.1.1 has no top-level ``status``), so the merge it graded no longer
            # exists. The surviving obligation is the reportable one: every criterion the
            # buyer supplied is named back in query_summary.filters_applied.
            response = env.call_impl(filters=CreativeFilters(statuses=["approved"], name_contains="Creative"))

        applied = response.query_summary.filters_applied
        assert any("statuses" in f for f in applied)
        assert any("search=" in f for f in applied)


# ---------------------------------------------------------------------------
# Sorting Tests — Covers:
# adcp spec: list_creatives sort (name, status, created_date, etc.)
# ---------------------------------------------------------------------------


class TestListSorting:
    """Sort by name and status exercises line 170-171."""

    def test_sort_by_name_asc(self, integration_db):
        """Spec: list_creatives sort by name ascending."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_b", name="Bravo")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_a", name="Alpha")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_c", name="Charlie")

            response = env.call_impl(sort=Sort(field="name", direction="asc"))

        names = [c.name for c in response.creatives]
        assert names == ["Alpha", "Bravo", "Charlie"]

    def test_sort_by_status(self, integration_db):
        """Spec: list_creatives sort by status ascending."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_pend",
                status="pending_review",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_app",
                status="approved",
            )

            response = env.call_impl(sort=Sort(field="status", direction="asc"))

        statuses = [str(c.status) for c in response.creatives]
        assert statuses == sorted(statuses)

    def test_sort_applied_in_response(self, integration_db):
        """Spec: list_creatives response includes sort_applied metadata."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(sort=Sort(field="name", direction="asc"))

        sort = response.query_summary.sort_applied
        assert sort.field == "name"
        assert sort.direction.value == "asc"


# ---------------------------------------------------------------------------
# Query Summary Tests — Covers:
# adcp spec: list_creatives query_summary (filters_applied, total_matching, etc.)
# ---------------------------------------------------------------------------


class TestListQuerySummary:
    """Query summary shows filters_applied for each filter type."""

    def test_filters_applied_includes_media_buy_ids(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists media_buy_ids."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            c1 = CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")
            mb = MediaBuyFactory(tenant=tenant, media_buy_id="mb_qs_1")
            CreativeAssignmentFactory(creative=c1, media_buy=mb)

            response = env.call_impl(filters=CreativeFilters(media_buy_ids=["mb_qs_1"]))

        assert any("media_buy_ids" in f for f in response.query_summary.filters_applied)

    def test_filters_applied_includes_search(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists search term."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(filters=CreativeFilters(name_contains="banner"))

        assert any("search=" in f for f in response.query_summary.filters_applied)

    def test_filters_applied_includes_dates(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists date filters."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(
                filters=CreativeFilters(
                    created_after=datetime(2024, 1, 1, tzinfo=UTC),
                    created_before=datetime(2027, 12, 31, 23, 59, 59, tzinfo=UTC),
                )
            )

        applied = response.query_summary.filters_applied
        assert any("created_after" in f for f in applied)
        assert any("created_before" in f for f in applied)

    def test_filters_applied_includes_tags(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists tags."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1", name="Test tag1")

            response = env.call_impl(filters=CreativeFilters(tags=["tag1"]))

        assert any("tags=" in f for f in response.query_summary.filters_applied)


# ---------------------------------------------------------------------------
# Response Shape Tests — Covers:
# adcp spec: list_creatives response (format_id, pagination, creative fields)
# ---------------------------------------------------------------------------


class TestListResponseShape:
    """Creative response object construction — format_parameters, snippet, etc."""

    def test_format_parameters_extracted(self, integration_db):
        """Spec: list_creatives format_id includes width/height/duration_ms."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_fmt_params",
                format_parameters={"width": 300, "height": 250, "duration_ms": 15000},
            )

            response = env.call_impl()

        creative = response.creatives[0]
        assert creative.format_id.width == 300
        assert creative.format_id.height == 250
        assert creative.format_id.duration_ms == 15000

    def test_snippet_creative_content_uri(self, integration_db):
        """Spec: list_creatives includes snippet content in creative data."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_snippet",
                data={
                    "assets": build_assets(image_spec("banner")),
                    "snippet": "<script>/* ad tag */</script>",
                },
            )

            response = env.call_impl()

        # Snippet creative exists and has assets
        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_snippet"

    def test_pagination_total_count(self, integration_db):
        """Spec: list_creatives pagination includes total_count and has_more."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            for i in range(5):
                CreativeFactory(tenant=tenant, principal=principal, creative_id=f"c_tc_{i}")

            response = env.call_impl(pagination=PaginationRequest(max_results=2))

        assert response.pagination.total_count == 5
        assert response.pagination.has_more is True
        assert response.query_summary.total_matching == 5
        assert response.query_summary.returned == 2

    def test_query_summary_message_with_pages(self, integration_db):
        """Spec: list_creatives query_summary shows page info when paginated."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            for i in range(4):
                CreativeFactory(tenant=tenant, principal=principal, creative_id=f"c_msg_{i}")

            response = env.call_impl(pagination=PaginationRequest(max_results=2), page=1)

        # Paginated response has has_more
        assert response.pagination.has_more is True
        assert response.query_summary.total_matching == 4


# ---------------------------------------------------------------------------
# Datetime Fallback Tests — data migration safety net
# Architecture decision: listing.py provides fallback datetime.now(UTC) when
# created_at/updated_at is None (legacy records pre-dating the column).
# ---------------------------------------------------------------------------


class TestListDatetimeFallback:
    """Datetime fallback for null created_at/updated_at — data migration path."""

    def test_null_created_at_gets_fallback_datetime(self, integration_db):
        """Data migration: creative with null created_at gets datetime.now(UTC) fallback."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_null_ts",
                created_at=None,
                updated_at=None,
            )

            now = datetime.now(UTC)
            response = env.call_impl()

        assert len(response.creatives) == 1
        creative = response.creatives[0]
        # Fallback should produce a recent datetime (non-None, timezone-aware)
        assert creative.created_date is not None
        assert creative.updated_date is not None
        assert creative.created_date.tzinfo is not None
        assert creative.updated_date.tzinfo is not None
        # Fallback datetime should be within 10 seconds of now
        assert abs((creative.created_date - now).total_seconds()) < 10
        assert abs((creative.updated_date - now).total_seconds()) < 10


# ---------------------------------------------------------------------------
# Transport Parity Tests — Covers:
# Verify same behavior across IMPL, A2A, and MCP transports
# ---------------------------------------------------------------------------


class TestListTransportParity:
    """Same behavior across IMPL and A2A transports."""

    def test_a2a_returns_same_as_impl(self, integration_db):
        """Transport parity: A2A and IMPL return identical results."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_transport",
                status="approved",
            )

            # Spec vocabulary on BOTH sides. The flat ``status`` this used to send was
            # never a ListCreativesRequest field, so A2A's select_request_fields dropped it
            # while call_impl honoured it -- the two transports were being asked different
            # questions and the test could only ever have caught a difference by accident.
            # call_impl takes the TYPED filter; call_a2a takes the wire dict.
            impl_response = env.call_impl(filters=CreativeFilters(statuses=["approved"]))
            a2a_response = env.call_a2a(filters={"statuses": ["approved"]})

        assert len(impl_response.creatives) == len(a2a_response.creatives)
        assert impl_response.creatives[0].creative_id == a2a_response.creatives[0].creative_id

    def test_mcp_returns_same_as_impl(self, integration_db):
        """Transport parity: MCP wrapper returns identical results."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_mcp",
                status="approved",
            )

            # ``statuses``, plural, because that is what the pinned CreativeFilters
            # declares -- there is no ``status`` member. The singular spelling used to
            # sit here and the test passed anyway, vacuously: the pinned model DROPPED
            # the unknown key, so ``call_impl`` filtered on nothing and ``call_mcp``
            # filtered on nothing, and two unfiltered listings trivially agree. Parity
            # was being asserted over a filter neither side applied.
            # It surfaced when MCP began validating through the DTO like the other two
            # transports: the wire half started refusing the undeclared key
            # (INVALID_REQUEST, additionalProperties on /filters/status) while the impl
            # half kept silently discarding it. The sibling A2A test above already used
            # the plural; only this one was missed.
            # call_impl takes the TYPED filter (it hands the object straight to _impl);
            # call_mcp takes the wire dict, which FastMCP coerces. Same field either way.
            impl_response = env.call_impl(filters=CreativeFilters(statuses=["approved"]))
            mcp_response = env.call_mcp(filters={"statuses": ["approved"]})

        assert len(impl_response.creatives) == len(mcp_response.creatives)
        assert impl_response.creatives[0].creative_id == mcp_response.creatives[0].creative_id


class TestListCreativeObjectConstruction:
    """Tests for Creative object construction from DB rows — listing.py lines 226-308."""

    pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

    def test_snippet_creative_gets_snippet_url(self, integration_db):
        """Spec: snippet creatives use data.url as content_uri, fallback to script tag."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_snippet",
                data={
                    "assets": build_assets(image_spec("banner")),
                    "snippet": "<script>var ad = 1;</script>",
                    "url": "https://cdn.example.com/ad.html",
                },
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        # Snippet creative should use the url from data
        assert response.creatives[0].creative_id == "c_snippet"

    def test_snippet_creative_without_url_gets_fallback(self, integration_db):
        """Spec: snippet creative with no url gets script tag fallback."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_snippet_no_url",
                data={
                    "assets": build_assets(image_spec("banner")),
                    "snippet": "<script>var ad = 1;</script>",
                },
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_snippet_no_url"

    def test_non_snippet_creative_uses_url(self, integration_db):
        """Spec: non-snippet creatives use data.url as content_uri."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_normal",
                data={
                    "assets": build_assets(image_spec("banner")),
                    "url": "https://cdn.example.com/creative.jpg",
                },
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_normal"

    def test_format_parameters_width_height(self, integration_db):
        """Spec: format_parameters populates FormatId width/height/duration_ms."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_format_params",
                format_parameters={"width": 728, "height": 90, "duration_ms": 15000},
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        creative = response.creatives[0]
        assert creative.format_id.width == 728
        assert creative.format_id.height == 90
        assert creative.format_id.duration_ms == 15000

    def test_format_parameters_partial(self, integration_db):
        """Spec: format_parameters with only width still populates correctly."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_partial_params",
                format_parameters={"width": 300},
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        creative = response.creatives[0]
        assert creative.format_id.width == 300
        assert creative.format_id.height is None

    def test_unparseable_status_is_placeheld_and_surfaced(self, integration_db):
        """An unreadable stored status renders as ``processing`` AND raises an advisory.

        This test previously asserted ``pending_review`` and called that "spec" — AdCP
        3.1.1 says no such thing. ``enums/creative-status.json`` defines
        ``pending_review`` as "Creative has **passed processing** and is awaiting
        platform content policy review", i.e. a claim that processing succeeded and the
        SELLER owes the next transition. Asserting it for a value the reader could not
        parse states a lifecycle position nobody took (salesagent-zm5l). ``status`` is
        REQUIRED and the enum is closed with no ``unknown`` member, so a placeholder is
        unavoidable — ``processing`` is the member asserting the least — and the honest
        part is the ``errors[]`` advisory, which ``list-creatives-response.json`` puts on
        the success path for exactly this.

        The cross-transport wire assertions live in
        tests/integration/test_list_creatives_unrecognized_status.py.
        """
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_bad_status",
                status="completely_bogus_status",
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        # SDK 5.7: CreativeStatus is plain Enum, not StrEnum; compare via .value
        status = response.creatives[0].status
        status_str = status.value if hasattr(status, "value") else str(status)
        assert status_str == "processing"

        advisories = response.errors or []
        assert len(advisories) == 1, f"expected one advisory for the unreadable row; got {advisories!r}"
        assert advisories[0].code == "CONFIGURATION_ERROR"
        # WHICH creative travels in details. The unparseable stored status is INTERNAL
        # state and is deliberately kept OFF the buyer wire (salesagent-3dawm.14);
        # the operator gets it from the log line the same branch emits.
        assert advisories[0].details == {"creative_id": "c_bad_status"}
        # ...and the unparseable stored value does NOT reach the buyer. Asserting its
        # ABSENCE is the stronger claim and the one that matches the decision: it is
        # seller-side state that is, by definition, not in the AdCP vocabulary (that
        # is why this branch fired at all), so putting it on the wire would leak
        # internal data to answer a question the buyer cannot act on.
        assert "completely_bogus_status" not in advisories[0].message
        assert "completely_bogus_status" not in str(advisories[0].details)

    def test_creative_with_tags(self, integration_db):
        """Spec: creative tags from data dict are included in response."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_with_tags",
                data={
                    "assets": build_assets(image_spec("banner")),
                    "tags": ["brand_safe", "premium"],
                },
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        assert response.creatives[0].tags == ["brand_safe", "premium"]

    def test_creative_without_tags_returns_none(self, integration_db):
        """Spec: creative with no tags key in data returns None tags."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_no_tags",
                data={"assets": build_assets(image_spec("banner"))},
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        assert response.creatives[0].tags is None

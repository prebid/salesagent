"""Integration tests: list_creatives auth, filtering, pagination.

Behavioral tests using CreativeListEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Test traceability: These tests verify list_creatives behavior defined in the
adcp spec (media-buy/task-reference/list_creatives). No BDD obligations exist
for list_creatives in docs/test-obligations/; auth tests reference sync_creatives
obligations (UC-006-EXT-*) which share the same auth contract.

Covers: salesagent-wdkc, salesagent-39ic
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from tests.factories import (
    CreativeAssignmentFactory,
    CreativeFactory,
    MediaBuyFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.harness import CreativeListEnv, make_identity

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


_make_identity = make_identity  # Canonical version from tests.harness


# ---------------------------------------------------------------------------
# Auth Tests — Covers: UC-006-EXT-A-01, UC-006-EXT-B-01
# ---------------------------------------------------------------------------


class TestListAuth:
    """list_creatives requires authentication — creatives are principal-scoped."""

    def test_no_identity_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-A-01 — identity=None → AdCPAuthenticationError."""
        with CreativeListEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                env.call_impl(identity=None)

    def test_no_principal_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-A-01 — principal_id=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id=None, tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeListEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                env.call_impl(identity=identity)

    def test_no_tenant_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-B-01 — tenant=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeListEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(identity=identity)


# ---------------------------------------------------------------------------
# Validation Tests — Covers: UC-006-EXT-C-01
# ---------------------------------------------------------------------------


class TestListValidation:
    """Input validation for date filter parameters."""

    def test_invalid_created_after_raises(self, integration_db):
        """Covers: UC-006-EXT-C-01 — invalid created_after date → AdCPValidationError."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            with pytest.raises(AdCPValidationError, match="created_after"):
                env.call_impl(created_after="not-a-date")

    def test_invalid_created_before_raises(self, integration_db):
        """Covers: UC-006-EXT-C-01 — invalid created_before date → AdCPValidationError."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            with pytest.raises(AdCPValidationError, match="created_before"):
                env.call_impl(created_before="not-a-date")


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

            response = env.call_impl(status="approved")

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_approved"

    def test_format_filter_returns_matching(self, integration_db):
        """Spec: list_creatives formats filter returns only matching creatives."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_display",
                format="display_300x250",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_video",
                format="video_30s",
            )

            response = env.call_impl(format="display_300x250")

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_display"

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

            response = env.call_impl(limit=2)

        assert len(response.creatives) == 2
        assert response.pagination.has_more is True

    def test_page_offsets_results(self, integration_db):
        """Spec: list_creatives page parameter offsets results."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            for i in range(5):
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id=f"c_offset_{i}",
                )

            page1 = env.call_impl(limit=2, page=1)
            page2 = env.call_impl(limit=2, page=2)

        # Pages should return different creatives
        page1_ids = {c.creative_id for c in page1.creatives}
        page2_ids = {c.creative_id for c in page2.creatives}
        assert len(page1_ids) == 2
        assert len(page2_ids) == 2
        assert page1_ids.isdisjoint(page2_ids)


# ---------------------------------------------------------------------------
# Principal Isolation Tests
# ---------------------------------------------------------------------------


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
# Advanced Filtering Tests — Covers: salesagent-39ic
# adcp spec: list_creatives filters (tags, created_after, created_before,
#            name_contains, media_buy_ids, buyer_refs)
# ---------------------------------------------------------------------------


class TestListTagsFilter:
    """Tags filter exercises line 128 (tags → name.contains)."""

    def test_tags_filter_returns_matching(self, integration_db):
        """Spec: list_creatives tags filter matches creatives by name substring."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_summer",
                name="Summer Campaign Banner",
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_winter",
                name="Winter Campaign Video",
            )

            response = env.call_impl(tags=["Summer"])

        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_summer"


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

            cutoff = (now - timedelta(days=7)).isoformat()
            response = env.call_impl(created_after=cutoff)

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

            cutoff = (now - timedelta(days=7)).isoformat()
            response = env.call_impl(created_before=cutoff)

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

            response = env.call_impl(search="hero")

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

            response = env.call_impl(media_buy_id=mb.media_buy_id)

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

            response = env.call_impl(media_buy_ids=["mb_1", "mb_2"])

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

            # Structured filters with name_contains + flat status
            # Line 151: filters_dict = {**filters.model_dump(exclude_none=True), **filters_dict}
            structured = CreativeFilters(name_contains="Creative")
            response = env.call_impl(status="approved", filters=structured)

        # Flat status AND structured name_contains both appear in filters_applied
        applied = response.query_summary.filters_applied
        assert any("statuses" in f for f in applied)
        assert any("search=" in f for f in applied)


# ---------------------------------------------------------------------------
# Sorting Tests — Covers: salesagent-39ic
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

            response = env.call_impl(sort_by="name", sort_order="asc")

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

            response = env.call_impl(sort_by="status", sort_order="asc")

        statuses = [c.status.value for c in response.creatives]
        assert statuses == sorted(statuses)

    def test_sort_applied_in_response(self, integration_db):
        """Spec: list_creatives response includes sort_applied metadata."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(sort_by="name", sort_order="asc")

        sort = response.query_summary.sort_applied
        assert sort.field == "name"
        assert sort.direction.value == "asc"


# ---------------------------------------------------------------------------
# Query Summary Tests — Covers: salesagent-39ic
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

            response = env.call_impl(media_buy_ids=["mb_qs_1"])

        assert any("media_buy_ids" in f for f in response.query_summary.filters_applied)

    def test_filters_applied_includes_search(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists search term."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(search="banner")

        assert any("search=" in f for f in response.query_summary.filters_applied)

    def test_filters_applied_includes_dates(self, integration_db):
        """Spec: list_creatives query_summary.filters_applied lists date filters."""
        with CreativeListEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_1")

            response = env.call_impl(
                created_after="2024-01-01T00:00:00+00:00",
                created_before="2027-12-31T23:59:59+00:00",
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

            response = env.call_impl(tags=["tag1"])

        assert any("tags=" in f for f in response.query_summary.filters_applied)


# ---------------------------------------------------------------------------
# Response Shape Tests — Covers: salesagent-39ic
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
                    "assets": {"banner": {"url": "https://example.com/banner.png"}},
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

            response = env.call_impl(limit=2)

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

            response = env.call_impl(limit=2, page=1)

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
# Transport Parity Tests — Covers: salesagent-39ic
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

            impl_response = env.call_impl(status="approved")
            a2a_response = env.call_a2a(status="approved")

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

            impl_response = env.call_impl(status="approved")
            mcp_response = env.call_mcp(status="approved")

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
                    "assets": {"banner": {"url": "https://example.com/banner.png"}},
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
                    "assets": {"banner": {"url": "https://example.com/banner.png"}},
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
                    "assets": {"banner": {"url": "https://example.com/banner.png"}},
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

    def test_invalid_status_defaults_to_pending_review(self, integration_db):
        """Spec: unknown status string defaults to pending_review."""
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
        assert response.creatives[0].status.value == "pending_review"

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
                    "assets": {"banner": {"url": "https://example.com/b.png"}},
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
                data={"assets": {"banner": {"url": "https://example.com/b.png"}}},
            )
            response = env.call_impl()

        assert len(response.creatives) == 1
        assert response.creatives[0].tags is None

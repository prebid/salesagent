"""Integration tests: list_creatives auth, filtering, pagination.

Behavioral tests using CreativeListEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Covers: salesagent-wdkc
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory
from tests.harness.creative_list import CreativeListEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_identity(principal_id=None, tenant_id=None, tenant=None, **kwargs):
    """Build a ResolvedIdentity with explicit control over all fields."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id or "test_tenant",
        tenant=tenant,
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        **kwargs,
    )


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
# ---------------------------------------------------------------------------


class TestListFiltering:
    """Filtering by status, format, and other parameters with real DB data."""

    def test_status_filter_returns_matching(self, integration_db):
        """Covers: UC-006-LIST-FILTER-01 — status filter returns only matching creatives."""
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
        """Covers: UC-006-LIST-FILTER-02 — format filter returns only matching creatives."""
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
        """Covers: UC-006-LIST-FILTER-03 — no filter returns all principal's creatives."""
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
# ---------------------------------------------------------------------------


class TestListPagination:
    """Pagination with real DB data."""

    def test_limit_restricts_results(self, integration_db):
        """Covers: UC-006-LIST-PAGINATION-01 — limit restricts returned count."""
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
        """Covers: UC-006-LIST-PAGINATION-02 — page parameter offsets results."""
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
        """Covers: UC-006-LIST-ISO-01 — principal A cannot see principal B's creatives."""
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

"""Integration tests: CreativeRepository and CreativeAssignmentRepository.

Tests repository methods against real PostgreSQL using factory_boy.
Replaces mock-session unit tests from test_creative_repository.py.

Covers: salesagent-1m85
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.creative import (
    CreativeAssignmentRepository,
    CreativeListResult,
    CreativeRepository,
)
from tests.factories import (
    CreativeAssignmentFactory,
    CreativeFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


# ---------------------------------------------------------------------------
# CreativeRepository
# ---------------------------------------------------------------------------


class TestCreativeRepoInit:
    """Repository construction."""

    def test_tenant_id_stored(self, integration_db):
        """Internal: REPO-INIT-01 — tenant_id stored at construction."""
        with _RepoEnv() as env:
            session = env.get_session()
            repo = CreativeRepository(session, "t1")
            assert repo.tenant_id == "t1"


class TestCreativeRepoGetById:
    """get_by_id — scoped by tenant, principal, and creative_id."""

    def test_returns_matching_creative(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01 — returns creative when found."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c1",
            )
            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.get_by_id("c1", "p1")

        assert result is not None
        assert result.creative_id == "c1"

    def test_returns_none_when_not_found(self, integration_db):
        """Internal: REPO-GET-02 — returns None when no matching creative."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.get_by_id("nonexistent", "p1")

        assert result is None

    def test_cross_tenant_isolation(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01 — tenant A's creative invisible to tenant B repo."""
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="t1")
            t2 = TenantFactory(tenant_id="t2")
            p1 = PrincipalFactory(tenant=t1, principal_id="p1")
            PrincipalFactory(tenant=t2, principal_id="p1")
            CreativeFactory(tenant=t1, principal=p1, creative_id="c_t1")

            session = env.get_session()
            repo_t2 = CreativeRepository(session, "t2")
            result = repo_t2.get_by_id("c_t1", "p1")

        assert result is None


class TestCreativeRepoGetByPrincipal:
    """get_by_principal — paginated results with total count."""

    def test_returns_creative_list_result(self, integration_db):
        """Internal: REPO-LIST-01 — returns CreativeListResult with correct count."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            for i in range(5):
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id=f"c_{i}",
                )

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.get_by_principal("p1")

        assert isinstance(result, CreativeListResult)
        assert result.total_count == 5
        assert len(result.creatives) == 5

    def test_zero_results(self, integration_db):
        """Internal: REPO-LIST-02 — empty result for principal with no creatives."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.get_by_principal("nonexistent")

        assert result.total_count == 0
        assert result.creatives == []

    def test_pagination(self, integration_db):
        """Internal: REPO-LIST-03 — offset/limit pagination works correctly."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            for i in range(5):
                CreativeFactory(
                    tenant=tenant,
                    principal=principal,
                    creative_id=f"c_page_{i}",
                )

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            page1 = repo.get_by_principal("p1", limit=2, offset=0)
            page2 = repo.get_by_principal("p1", limit=2, offset=2)

        assert len(page1.creatives) == 2
        assert len(page2.creatives) == 2
        assert page1.total_count == 5
        # Pages should return different creatives
        p1_ids = {c.creative_id for c in page1.creatives}
        p2_ids = {c.creative_id for c in page2.creatives}
        assert p1_ids.isdisjoint(p2_ids)

    def test_status_filter(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01 — status filter narrows results."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_approved", status="approved")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c_pending", status="pending_review")

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.get_by_principal("p1", status="approved")

        assert result.total_count == 1
        assert result.creatives[0].creative_id == "c_approved"


class TestCreativeRepoListByPrincipal:
    """list_by_principal — all creatives without pagination."""

    def test_returns_list(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-01 — returns all creatives for principal."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            for i in range(3):
                CreativeFactory(tenant=tenant, principal=principal, creative_id=f"c_{i}")

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.list_by_principal("p1")

        assert len(result) == 3


class TestCreativeRepoCreate:
    """create() — persists a new creative."""

    def test_creates_and_persists(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03 — creates creative in DB with correct fields."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.create(
                creative_id="c_new",
                name="Test Banner",
                agent_url="https://agent.example.com",
                format="display_300x250",
                principal_id="p1",
                data={"assets": {"banner": {"url": "https://example.com/banner.png"}}},
            )

        assert result.creative_id == "c_new"
        assert result.tenant_id == "test_tenant"
        assert result.principal_id == "p1"
        assert result.name == "Test Banner"

    def test_generates_id_when_not_provided(self, integration_db):
        """Internal: REPO-CREATE-02 — auto-generates UUID creative_id."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            result = repo.create(
                name="Auto ID",
                agent_url="https://agent.example.com",
                format="display_300x250",
                principal_id="p1",
            )

        assert result.creative_id is not None
        assert len(result.creative_id) > 0


class TestCreativeRepoUpdateData:
    """update_data() — sets data field."""

    def test_updates_data_field(self, integration_db):
        """Covers: UC-006-MAIN-MCP-03 — data field updated and persisted."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c_upd")

            session = env.get_session()
            repo = CreativeRepository(session, "test_tenant")
            db_creative = repo.get_by_id("c_upd", "p1")
            new_data = {"assets": {"banner": {"url": "https://new.example.com/banner.png"}}}
            repo.update_data(db_creative, new_data)
            repo.flush()

            # Re-query to verify persistence
            refreshed = repo.get_by_id("c_upd", "p1")

        assert refreshed.data == new_data


# ---------------------------------------------------------------------------
# CreativeAssignmentRepository
# ---------------------------------------------------------------------------


class TestAssignmentRepoInit:
    """Repository construction."""

    def test_tenant_id_stored(self, integration_db):
        """Internal: ASSIGN-INIT-01 — tenant_id stored."""
        with _RepoEnv() as env:
            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "t1")
            assert repo.tenant_id == "t1"


class TestAssignmentRepoGetByCreative:
    """get_by_creative — assignments for a creative."""

    def test_returns_matching_assignments(self, integration_db):
        """Covers: UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-01 — returns assignments for creative."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            CreativeAssignmentFactory(
                tenant_id=tenant.tenant_id,
                creative_id=creative.creative_id,
                principal_id=principal.principal_id,
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
            )

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_by_creative("c1")

        assert len(result) == 1


class TestAssignmentRepoGetByPackage:
    """get_by_package — assignments for a package."""

    def test_returns_matching_assignments(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04 — returns assignments for package."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            CreativeAssignmentFactory(
                tenant_id=tenant.tenant_id,
                creative_id=creative.creative_id,
                principal_id=principal.principal_id,
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
            )

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_by_package(pkg.package_id)

        assert len(result) == 1


class TestAssignmentRepoGetExisting:
    """get_existing — lookup by composite key."""

    def test_returns_existing_assignment(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04 — returns assignment when found."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            assignment = CreativeAssignmentFactory(
                tenant_id=tenant.tenant_id,
                creative_id=creative.creative_id,
                principal_id=principal.principal_id,
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
            )

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_existing(media_buy.media_buy_id, pkg.package_id, "c1")

        assert result is not None
        assert result.assignment_id == assignment.assignment_id

    def test_returns_none_when_not_found(self, integration_db):
        """Internal: ASSIGN-EXISTING-02 — returns None when not found."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_existing("mb_none", "pkg_none", "c_none")

        assert result is None


class TestAssignmentRepoCreate:
    """create() — persists a new assignment."""

    def test_creates_assignment(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01 — creates assignment with correct fields."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.create(
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
                creative_id="c1",
                principal_id="p1",
                weight=75,
            )
            session.flush()

        assert result.tenant_id == "test_tenant"
        assert result.creative_id == "c1"
        assert result.weight == 75

    def test_default_weight_100(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04 — default weight is 100."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.create(
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
                creative_id="c1",
                principal_id="p1",
            )
            session.flush()

        assert result.weight == 100


class TestAssignmentRepoDelete:
    """delete() — removes assignment by ID."""

    def test_deletes_existing(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-01 — returns True when found and deleted."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            creative = CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)
            assignment = CreativeAssignmentFactory(
                tenant_id=tenant.tenant_id,
                creative_id=creative.creative_id,
                principal_id=principal.principal_id,
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
            )
            aid = assignment.assignment_id

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.delete(aid)

        assert result is True

    def test_returns_false_when_not_found(self, integration_db):
        """Internal: ASSIGN-DELETE-02 — returns False when not found."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.delete("nonexistent")

        assert result is False

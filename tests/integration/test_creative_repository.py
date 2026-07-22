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
    ProductFactory,
    TenantFactory,
)
from tests.factories.creative_asset import build_assets, image_spec
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
                data={"assets": build_assets(image_spec("banner"))},
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
            new_data = {"assets": build_assets(image_spec("banner"))}
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


class TestAssignmentRepoCreativeReadiness:
    """creative_readiness — the shared finalize-vs-hold gate (#1544).

    One tenant-scoped home for the readiness query previously open-coded (with
    drift) in the workflow approve route, the operations approve route, and the
    activation scheduler.
    """

    @staticmethod
    def _buy_with_assignment(tenant, principal, *, creative_status: str, creative_id: str = "c1"):
        creative = CreativeFactory(tenant=tenant, principal=principal, creative_id=creative_id, status=creative_status)
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
        pkg = MediaPackageFactory(media_buy=media_buy)
        CreativeAssignmentFactory(
            tenant_id=tenant.tenant_id,
            creative_id=creative.creative_id,
            principal_id=principal.principal_id,
            media_buy_id=media_buy.media_buy_id,
            package_id=pkg.package_id,
        )
        return media_buy, pkg

    def test_ready_when_all_assigned_creatives_approved(self, integration_db):
        """All assigned creatives approved → ready_for_finalize."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            media_buy, _ = self._buy_with_assignment(tenant, principal, creative_status="approved")

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "test_tenant").creative_readiness(media_buy.media_buy_id)

        assert readiness.has_assignments is True
        assert readiness.unapproved_creative_ids == []
        assert readiness.ready_for_finalize is True
        assert readiness.all_assigned_approved is True

    def test_unapproved_assigned_creative_blocks_finalize(self, integration_db):
        """An assigned-but-unapproved creative blocks BOTH policies."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            media_buy, _ = self._buy_with_assignment(tenant, principal, creative_status="pending")

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "test_tenant").creative_readiness(media_buy.media_buy_id)

        assert readiness.has_assignments is True
        assert readiness.unapproved_creative_ids == ["c1"]
        assert readiness.ready_for_finalize is False
        assert readiness.all_assigned_approved is False

    def test_zero_assignments_policy_split(self, integration_db):
        """Zero assignments: the approve gate HOLDS, the activation policy is ready.

        Pins the one-home policy split: ready_for_finalize is False (hold at
        pending_creatives — seller-approved, no creatives assigned yet) while
        all_assigned_approved is vacuously True (scheduler activation). #1544.
        """
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "test_tenant").creative_readiness(media_buy.media_buy_id)

        assert readiness.has_assignments is False
        assert readiness.unapproved_creative_ids == []
        assert readiness.ready_for_finalize is False
        assert readiness.all_assigned_approved is True

    def test_cross_tenant_assignment_invisible(self, integration_db):
        """Another tenant's assignment row against the SAME media_buy_id is ignored.

        Dropping the tenant filter from the assignments query flips
        has_assignments True and (with no in-tenant creatives) ready_for_finalize
        True — this test is the red oracle for that mutation. #1544.
        """
        with _RepoEnv() as env:
            tenant_a = TenantFactory(tenant_id="tenant_a")
            principal_a = PrincipalFactory(tenant=tenant_a, principal_id="pa")
            media_buy = MediaBuyFactory(tenant=tenant_a, principal=principal_a)
            pkg = MediaPackageFactory(media_buy=media_buy)

            tenant_b = TenantFactory(tenant_id="tenant_b")
            principal_b = PrincipalFactory(tenant=tenant_b, principal_id="pb")
            creative_b = CreativeFactory(tenant=tenant_b, principal=principal_b, creative_id="cb", status="approved")
            CreativeAssignmentFactory(
                tenant_id=tenant_b.tenant_id,
                creative_id=creative_b.creative_id,
                principal_id=principal_b.principal_id,
                media_buy_id=media_buy.media_buy_id,
                package_id=pkg.package_id,
            )

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "tenant_a").creative_readiness(media_buy.media_buy_id)

        assert readiness.has_assignments is False
        assert readiness.ready_for_finalize is False

    def test_cross_tenant_creative_status_invisible(self, integration_db):
        """Another tenant's same-id creative cannot block THIS tenant's readiness.

        creative_id is only unique per (tenant, principal): tenant_b's PENDING
        "c1" must not leak into tenant_a's status check. Dropping the tenant
        filter from the creatives query flips ready_for_finalize False — this
        test is the red oracle for that mutation. #1544.
        """
        with _RepoEnv() as env:
            tenant_a = TenantFactory(tenant_id="tenant_a")
            principal_a = PrincipalFactory(tenant=tenant_a, principal_id="pa")
            media_buy, _ = self._buy_with_assignment(tenant_a, principal_a, creative_status="approved")

            tenant_b = TenantFactory(tenant_id="tenant_b")
            principal_b = PrincipalFactory(tenant=tenant_b, principal_id="pb")
            CreativeFactory(tenant=tenant_b, principal=principal_b, creative_id="c1", status="pending")

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "tenant_a").creative_readiness(media_buy.media_buy_id)

        assert readiness.unapproved_creative_ids == []
        assert readiness.ready_for_finalize is True

    def test_cross_principal_creative_status_invisible(self, integration_db):
        """A same-tenant, other-principal creative with a colliding id cannot satisfy readiness.

        The creatives PK is composite (creative_id, tenant_id, principal_id): the
        status lookup matches the assignment's OWN principal, so principal-b's
        APPROVED "c1" must not stand in for principal-a's PENDING "c1". Dropping
        the principal filter (tenant-only matching) could flip this ready — this
        test is the red oracle for that mutation. #1544.
        """
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal_a = PrincipalFactory(tenant=tenant, principal_id="pa")
            media_buy, _ = self._buy_with_assignment(tenant, principal_a, creative_status="pending")

            principal_b = PrincipalFactory(tenant=tenant, principal_id="pb")
            CreativeFactory(tenant=tenant, principal=principal_b, creative_id="c1", status="approved")

            session = env.get_session()
            readiness = CreativeAssignmentRepository(session, "test_tenant").creative_readiness(media_buy.media_buy_id)

        assert readiness.unapproved_creative_ids == ["c1"]
        assert readiness.ready_for_finalize is False


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
            result = repo.get_existing(media_buy.media_buy_id, pkg.package_id, "c1", "p1")

        assert result is not None
        assert result.assignment_id == assignment.assignment_id

    def test_returns_none_when_not_found(self, integration_db):
        """Internal: ASSIGN-EXISTING-02 — returns None when not found."""
        with _RepoEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_existing("mb_none", "pkg_none", "c_none", "p_none")

        assert result is None


class TestAssignmentRepoGetCreativeById:
    """get_creative_by_id — full composite-PK creative lookup (tenant + principal + creative_id).

    Refactor guard for salesagent-qkk4: these pin the behavior that must survive
    delegation to CreativeRepository.get_by_id (they PASS pre-refactor).
    """

    def test_own_principal_creative_found(self, integration_db):
        """Internal: ASSIGN-XLOOKUP-01 — returns the creative when the caller's principal owns it."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            CreativeFactory(tenant=tenant, principal=principal, creative_id="c1")

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_creative_by_id("c1", "p1")

        assert result is not None
        assert result.creative_id == "c1"
        assert result.principal_id == "p1"

    def test_cross_principal_returns_none(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01 — another principal's creative resolves to None."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            owner = PrincipalFactory(tenant=tenant, principal_id="p_owner")
            PrincipalFactory(tenant=tenant, principal_id="p_other")
            CreativeFactory(tenant=tenant, principal=owner, creative_id="c_owned")

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_creative_by_id("c_owned", "p_other")

        assert result is None


class TestAssignmentRepoGetProductById:
    """get_product_by_id — tenant-scoped product lookup.

    Refactor guard for salesagent-qkk4: these pin the behavior that must survive
    delegation to ProductRepository.get_by_id (they PASS pre-refactor).
    """

    def test_own_tenant_product_found(self, integration_db):
        """Internal: ASSIGN-XLOOKUP-02 — returns the product when it belongs to the repo's tenant."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            ProductFactory(tenant=tenant, product_id="prod_own")

            session = env.get_session()
            repo = CreativeAssignmentRepository(session, "test_tenant")
            result = repo.get_product_by_id("prod_own")

        assert result is not None
        assert result.product_id == "prod_own"
        assert result.tenant_id == "test_tenant"

    def test_other_tenant_returns_none(self, integration_db):
        """Internal: ASSIGN-XLOOKUP-03 — another tenant's product is invisible to this repo."""
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="t1")
            TenantFactory(tenant_id="t2")
            ProductFactory(tenant=t1, product_id="prod_t1")

            session = env.get_session()
            repo_t2 = CreativeAssignmentRepository(session, "t2")
            result = repo_t2.get_product_by_id("prod_t1")

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

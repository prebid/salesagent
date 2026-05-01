"""Integration tests for MediaBuyRepository and MediaBuyUoW.

Tests the repository pattern with real PostgreSQL to verify:
- Tenant isolation (core invariant: every query is tenant-scoped by construction)
- CRUD operations return correct results
- UoW commit/rollback semantics
- MediaBuy.packages relationship loading

beads: salesagent-t735
"""

from datetime import UTC

import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, Tenant
from tests.integration.conftest import cleanup_tenant, make_media_buy, make_package

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_a(integration_db):
    """Create tenant A."""
    tenant_id = "repo_test_tenant_a"
    with get_db_session() as session:
        tenant = Tenant(tenant_id=tenant_id, name="Tenant A", subdomain="tenant-a", is_active=True, ad_server="mock")
        session.add(tenant)
        session.commit()
    yield tenant_id
    cleanup_tenant(tenant_id)


@pytest.fixture
def tenant_b(integration_db):
    """Create tenant B (for cross-tenant isolation tests)."""
    tenant_id = "repo_test_tenant_b"
    with get_db_session() as session:
        tenant = Tenant(tenant_id=tenant_id, name="Tenant B", subdomain="tenant-b", is_active=True, ad_server="mock")
        session.add(tenant)
        session.commit()
    yield tenant_id
    cleanup_tenant(tenant_id)


@pytest.fixture
def principal_a(tenant_a):
    """Create a principal in tenant A."""
    principal_id = "principal_a"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_a,
            principal_id=principal_id,
            name="Advertiser A",
            access_token="token_a",
            platform_mappings={"mock": {"advertiser_id": "adv_a"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


@pytest.fixture
def principal_b(tenant_b):
    """Create a principal in tenant B."""
    principal_id = "principal_b"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_b,
            principal_id=principal_id,
            name="Advertiser B",
            access_token="token_b",
            platform_mappings={"mock": {"advertiser_id": "adv_b"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


@pytest.fixture
def seed_data(tenant_a, tenant_b, principal_a, principal_b):
    """Seed two tenants with media buys and packages for isolation testing.

    Tenant A: mb_a1 (draft, 2 packages), mb_a2 (active)
    Tenant B: mb_b1 (draft, 1 package)
    """
    with get_db_session() as session:
        # Tenant A media buys
        mb_a1 = make_media_buy(tenant_a, principal_a, "mb_a1", status="draft")
        mb_a2 = make_media_buy(tenant_a, principal_a, "mb_a2", status="active")
        session.add_all([mb_a1, mb_a2])
        session.flush()

        # Tenant A packages (for mb_a1)
        pkg_a1_1 = make_package("mb_a1", "pkg_a1_1")
        pkg_a1_2 = make_package("mb_a1", "pkg_a1_2")
        session.add_all([pkg_a1_1, pkg_a1_2])

        # Tenant B media buys
        mb_b1 = make_media_buy(tenant_b, principal_b, "mb_b1", status="draft")
        session.add(mb_b1)
        session.flush()

        # Tenant B packages
        pkg_b1_1 = make_package("mb_b1", "pkg_b1_1")
        session.add(pkg_b1_1)

        session.commit()

    yield {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "principal_a": principal_a,
        "principal_b": principal_b,
    }


# ---------------------------------------------------------------------------
# Repository import and construction
# ---------------------------------------------------------------------------


class TestRepositoryImport:
    """Repository module exists and is importable."""

    def test_repository_importable(self):
        """MediaBuyRepository is importable from the repositories package."""
        from src.core.database.repositories import MediaBuyRepository  # noqa: F401

    def test_uow_importable(self):
        """MediaBuyUoW is importable from the repositories package."""
        from src.core.database.repositories import MediaBuyUoW  # noqa: F401


# ---------------------------------------------------------------------------
# Read operations — tenant isolation (Core Invariant)
# ---------------------------------------------------------------------------


class TestGetById:
    """get_by_id returns only media buys belonging to the repository's tenant."""

    def test_returns_own_tenant_media_buy(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            result = repo.get_by_id("mb_a1")
            assert result is not None
            assert result.media_buy_id == "mb_a1"
            assert result.tenant_id == seed_data["tenant_a"]

    def test_does_not_return_other_tenant_media_buy(self, seed_data):
        """Tenant A's repository must NOT see tenant B's media buy."""
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            result = repo.get_by_id("mb_b1")
            assert result is None

    def test_returns_none_for_nonexistent(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            result = repo.get_by_id("nonexistent")
            assert result is None


class TestGetByPrincipal:
    """get_by_principal returns only the specified principal's media buys."""

    def test_returns_all_for_principal(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            results = repo.get_by_principal(seed_data["principal_a"])
            assert len(results) == 2
            ids = {mb.media_buy_id for mb in results}
            assert ids == {"mb_a1", "mb_a2"}

    def test_status_filter(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            results = repo.get_by_principal(seed_data["principal_a"], statuses=["active"])
            assert len(results) == 1
            assert results[0].media_buy_id == "mb_a2"

    def test_media_buy_ids_filter(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            results = repo.get_by_principal(seed_data["principal_a"], media_buy_ids=["mb_a1"])
            assert len(results) == 1
            assert results[0].media_buy_id == "mb_a1"


# ---------------------------------------------------------------------------
# Package queries — tenant isolation through MediaBuy FK
# ---------------------------------------------------------------------------


class TestGetPackages:
    """get_packages returns packages for a media buy only if it belongs to the tenant."""

    def test_returns_packages_for_own_buy(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            packages = repo.get_packages("mb_a1")
            assert len(packages) == 2
            pkg_ids = {p.package_id for p in packages}
            assert pkg_ids == {"pkg_a1_1", "pkg_a1_2"}

    def test_returns_empty_for_other_tenant_buy(self, seed_data):
        """Tenant A repo asking for tenant B's media buy packages gets nothing."""
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            packages = repo.get_packages("mb_b1")
            assert packages == []


class TestGetPackage:
    """get_package returns a single package, tenant-scoped."""

    def test_returns_specific_package(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            pkg = repo.get_package("mb_a1", "pkg_a1_1")
            assert pkg is not None
            assert pkg.package_id == "pkg_a1_1"

    def test_returns_none_for_other_tenant(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            pkg = repo.get_package("mb_b1", "pkg_b1_1")
            assert pkg is None


class TestGetPackagesForIds:
    """get_packages_for_ids returns packages grouped by media_buy_id, tenant-scoped."""

    def test_groups_packages_by_media_buy(self, seed_data):
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            grouped = repo.get_packages_for_ids(["mb_a1", "mb_a2"])
            assert "mb_a1" in grouped
            assert len(grouped["mb_a1"]) == 2
            # mb_a2 has no packages, should be empty list or absent
            assert len(grouped.get("mb_a2", [])) == 0

    def test_excludes_other_tenant_packages(self, seed_data):
        """Even if we pass tenant B's media_buy_id, we get nothing."""
        from src.core.database.repositories import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, seed_data["tenant_a"])
            grouped = repo.get_packages_for_ids(["mb_b1"])
            assert len(grouped.get("mb_b1", [])) == 0


# ---------------------------------------------------------------------------
# MediaBuy.packages relationship
# ---------------------------------------------------------------------------


class TestMediaBuyPackagesRelationship:
    """MediaBuy.packages ORM relationship loads packages."""

    def test_relationship_loads_packages(self, seed_data):
        with get_db_session() as session:
            mb = session.get(MediaBuy, "mb_a1")
            assert mb is not None
            assert hasattr(mb, "packages")
            assert len(mb.packages) == 2

    def test_relationship_empty_when_no_packages(self, seed_data):
        with get_db_session() as session:
            mb = session.get(MediaBuy, "mb_a2")
            assert mb is not None
            assert mb.packages == []


# ---------------------------------------------------------------------------
# UoW — commit and rollback semantics
# ---------------------------------------------------------------------------


class TestMediaBuyUoW:
    """MediaBuyUoW provides single-session boundary with commit/rollback."""

    def test_uow_provides_repository(self, tenant_a, principal_a, seed_data):
        from src.core.database.repositories import MediaBuyUoW

        with MediaBuyUoW(seed_data["tenant_a"]) as uow:
            result = uow.media_buys.get_by_id("mb_a1")
            assert result is not None

    def test_uow_commits_on_clean_exit(self, tenant_a, principal_a):
        from src.core.database.repositories import MediaBuyUoW

        # Create a media buy inside UoW via repository
        with MediaBuyUoW(tenant_a) as uow:
            mb = make_media_buy(tenant_a, principal_a, "mb_uow_test")
            uow.media_buys.create(mb)

        # Verify it persisted (outside the UoW)
        with get_db_session() as session:
            result = session.get(MediaBuy, "mb_uow_test")
            assert result is not None

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuy).where(MediaBuy.media_buy_id == "mb_uow_test"))
            session.commit()

    def test_uow_rolls_back_on_exception(self, tenant_a, principal_a):
        from src.core.database.repositories import MediaBuyUoW

        with pytest.raises(ValueError, match="intentional"):
            with MediaBuyUoW(tenant_a) as uow:
                mb = make_media_buy(tenant_a, principal_a, "mb_uow_rollback")
                uow.media_buys.create(mb)
                raise ValueError("intentional")

        # Verify it was NOT persisted
        with get_db_session() as session:
            result = session.get(MediaBuy, "mb_uow_rollback")
            assert result is None


# ---------------------------------------------------------------------------
# Idempotency key lookup (adcp 3.12)
# ---------------------------------------------------------------------------


class TestIdempotencyKeyLookup:
    """Repository correctly finds media buys by idempotency_key.

    Core invariant: duplicate idempotency_key within (tenant, principal) returns
    existing media buy, never creates a second row.
    """

    def test_find_by_idempotency_key_returns_existing(self, tenant_a, principal_a):
        """find_by_idempotency_key returns the matching media buy."""
        from src.core.database.repositories.media_buy import MediaBuyRepository

        idem_key = "test-uuid-1234567890abcdef"
        with get_db_session() as session:
            buy = make_media_buy(tenant_a, principal_a, "mb_idem_1", idempotency_key=idem_key)
            session.add(buy)
            session.commit()

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            found = repo.find_by_idempotency_key(idem_key, principal_a)
            assert found is not None
            assert found.media_buy_id == "mb_idem_1"

    def test_find_by_idempotency_key_returns_none_when_missing(self, tenant_a, principal_a):
        """find_by_idempotency_key returns None for unknown key."""
        from src.core.database.repositories.media_buy import MediaBuyRepository

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            found = repo.find_by_idempotency_key("nonexistent-key-1234", principal_a)
            assert found is None

    def test_idempotency_key_scoped_to_tenant(self, tenant_a, tenant_b, principal_a, principal_b):
        """Same idempotency_key in different tenants are independent."""
        from src.core.database.repositories.media_buy import MediaBuyRepository

        idem_key = "shared-uuid-1234567890ab"
        with get_db_session() as session:
            buy_a = make_media_buy(tenant_a, principal_a, "mb_idem_a", idempotency_key=idem_key)
            buy_b = make_media_buy(tenant_b, principal_b, "mb_idem_b", idempotency_key=idem_key)
            session.add(buy_a)
            session.add(buy_b)
            session.commit()

        with get_db_session() as session:
            repo_a = MediaBuyRepository(session, tenant_a)
            found_a = repo_a.find_by_idempotency_key(idem_key, principal_a)
            assert found_a is not None
            assert found_a.media_buy_id == "mb_idem_a"

            repo_b = MediaBuyRepository(session, tenant_b)
            found_b = repo_b.find_by_idempotency_key(idem_key, principal_b)
            assert found_b is not None
            assert found_b.media_buy_id == "mb_idem_b"

    def test_create_from_request_stores_idempotency_key(self, tenant_a, principal_a):
        """create_from_request persists idempotency_key to the database."""
        from unittest.mock import MagicMock

        from src.core.database.repositories.media_buy import MediaBuyRepository

        idem_key = "create-test-uuid-123456"
        mock_req = MagicMock()
        mock_req.model_dump.return_value = {"test": True}
        mock_req.po_number = None
        mock_req.idempotency_key = idem_key

        from datetime import datetime

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            buy = repo.create_from_request(
                media_buy_id="mb_idem_create",
                req=mock_req,
                principal_id=principal_a,
                advertiser_name="Test",
                budget=1000.0,
                currency="USD",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                end_time=datetime(2026, 12, 31, tzinfo=UTC),
            )
            session.commit()
            assert buy.idempotency_key == idem_key

        # Verify persisted
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            found = repo.find_by_idempotency_key(idem_key, principal_a)
            assert found is not None
            assert found.media_buy_id == "mb_idem_create"

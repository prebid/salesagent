"""Integration tests for MediaBuyRepository write methods.

Tests write operations against real PostgreSQL to verify:
- Roundtrip: write -> read back -> verify fields match
- Tenant isolation: writes scoped to repository's tenant
- Edge cases: duplicate creates, updates to nonexistent records, tenant mismatches

beads: salesagent-dyb6
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, MediaPackage, Principal, Tenant
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Fixtures — reuse patterns from test_media_buy_repository.py
# ---------------------------------------------------------------------------


def _cleanup_tenant(tenant_id: str) -> None:
    """Delete tenant and all dependent data (correct FK order)."""
    with get_db_session() as session:
        mb_ids = session.scalars(select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == tenant_id)).all()
        if mb_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(mb_ids)))
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == tenant_id))
        session.execute(delete(Principal).where(Principal.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
        session.commit()


@pytest.fixture
def tenant_a(integration_db):
    """Create tenant A for write tests."""
    tenant_id = "write_test_tenant_a"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id, name="Write Tenant A", subdomain="write-a", is_active=True, ad_server="mock"
        )
        session.add(tenant)
        session.commit()
    yield tenant_id
    _cleanup_tenant(tenant_id)


@pytest.fixture
def tenant_b(integration_db):
    """Create tenant B for cross-tenant isolation tests."""
    tenant_id = "write_test_tenant_b"
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id, name="Write Tenant B", subdomain="write-b", is_active=True, ad_server="mock"
        )
        session.add(tenant)
        session.commit()
    yield tenant_id
    _cleanup_tenant(tenant_id)


@pytest.fixture
def principal_a(tenant_a):
    """Create a principal in tenant A."""
    principal_id = "write_principal_a"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_a,
            principal_id=principal_id,
            name="Write Advertiser A",
            access_token="write_token_a",
            platform_mappings={"mock": {"advertiser_id": "adv_write_a"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


@pytest.fixture
def principal_b(tenant_b):
    """Create a principal in tenant B."""
    principal_id = "write_principal_b"
    with get_db_session() as session:
        principal = Principal(
            tenant_id=tenant_b,
            principal_id=principal_id,
            name="Write Advertiser B",
            access_token="write_token_b",
            platform_mappings={"mock": {"advertiser_id": "adv_write_b"}},
        )
        session.add(principal)
        session.commit()
    yield principal_id


def _make_media_buy(tenant_id: str, principal_id: str, media_buy_id: str, **kwargs) -> MediaBuy:
    """Helper to construct a MediaBuy ORM object with required fields."""
    defaults = {
        "order_name": f"Order {media_buy_id}",
        "advertiser_name": "Test Advertiser",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 12, 31),
        "status": "draft",
        "raw_request": {"test": True},
    }
    defaults.update(kwargs)
    return MediaBuy(
        media_buy_id=media_buy_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        **defaults,
    )


def _make_package(media_buy_id: str, package_id: str, **kwargs) -> MediaPackage:
    """Helper to construct a MediaPackage ORM object."""
    defaults = {
        "package_config": {"name": f"Package {package_id}", "test": True},
    }
    defaults.update(kwargs)
    return MediaPackage(
        media_buy_id=media_buy_id,
        package_id=package_id,
        **defaults,
    )


# ---------------------------------------------------------------------------
# MediaBuy.create — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestCreateMediaBuy:
    """Repository.create() persists a new media buy within the tenant."""

    def test_roundtrip_create_and_read_back(self, tenant_a, principal_a):
        """Create via repository, read back, verify all fields match."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_create_1", buyer_ref="ref_create_1")
            result = uow.media_buys.create(mb)
            assert result is mb

        # Read back in a fresh session
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_create_1")
            assert fetched is not None
            assert fetched.media_buy_id == "mb_create_1"
            assert fetched.tenant_id == tenant_a
            assert fetched.principal_id == principal_a
            assert fetched.buyer_ref == "ref_create_1"
            assert fetched.order_name == "Order mb_create_1"
            assert fetched.status == "draft"

    def test_tenant_mismatch_raises(self, tenant_a, tenant_b, principal_a):
        """Creating a media buy with wrong tenant_id raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_b, principal_a, "mb_wrong_tenant")
            with pytest.raises(ValueError, match="Tenant mismatch"):
                uow.media_buys.create(mb)

    def test_tenant_isolation_on_create(self, tenant_a, tenant_b, principal_a, principal_b):
        """Media buy created in tenant A is not visible to tenant B."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_isolated")
            uow.media_buys.create(mb)

        with get_db_session() as session:
            repo_b = MediaBuyRepository(session, tenant_b)
            assert repo_b.get_by_id("mb_isolated") is None

    def test_uow_rollback_on_exception(self, tenant_a, principal_a):
        """If exception is raised inside UoW, the create is rolled back."""
        with pytest.raises(RuntimeError, match="intentional"):
            with MediaBuyUoW(tenant_a) as uow:
                mb = _make_media_buy(tenant_a, principal_a, "mb_rollback_create")
                uow.media_buys.create(mb)
                raise RuntimeError("intentional")

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            assert repo.get_by_id("mb_rollback_create") is None


# ---------------------------------------------------------------------------
# MediaBuy.update_status — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    """Repository.update_status() changes status and optional approval fields."""

    def test_roundtrip_update_status(self, tenant_a, principal_a):
        """Update status and verify fields persisted."""
        # Seed a media buy
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_status_1")
            uow.media_buys.create(mb)

        # Update status
        now = datetime.now(UTC)
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status(
                "mb_status_1",
                "approved",
                approved_at=now,
                approved_by="admin@test.com",
            )
            assert result is not None
            assert result.status == "approved"

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_status_1")
            assert fetched is not None
            assert fetched.status == "approved"
            assert fetched.approved_by == "admin@test.com"
            assert fetched.approved_at is not None

    def test_update_status_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating status of nonexistent media buy returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status("nonexistent_mb", "active")
            assert result is None

    def test_update_status_other_tenant_returns_none(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update status of media buy in another tenant."""
        # Create in tenant A
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_cross_status")
            uow.media_buys.create(mb)

        # Try to update from tenant B
        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_status("mb_cross_status", "active")
            assert result is None

        # Verify original status unchanged
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_cross_status")
            assert fetched is not None
            assert fetched.status == "draft"

    def test_update_status_only_changes_status(self, tenant_a, principal_a):
        """update_status without approved_at/approved_by only changes status."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_status_only")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_status("mb_status_only", "active")
            assert result is not None
            assert result.status == "active"
            assert result.approved_at is None
            assert result.approved_by is None


# ---------------------------------------------------------------------------
# MediaBuy.update_fields — generic field update
# ---------------------------------------------------------------------------


class TestUpdateFields:
    """Repository.update_fields() updates arbitrary attributes."""

    def test_roundtrip_update_fields(self, tenant_a, principal_a):
        """Update multiple fields and verify persistence."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_fields_1")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_fields(
                "mb_fields_1",
                order_name="Updated Order Name",
                budget=Decimal("5000.00"),
                kpi_goal="maximize_reach",
            )
            assert result is not None
            assert result.order_name == "Updated Order Name"

        # Read back in fresh session
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_fields_1")
            assert fetched is not None
            assert fetched.order_name == "Updated Order Name"
            assert fetched.budget == Decimal("5000.00")
            assert fetched.kpi_goal == "maximize_reach"

    def test_update_fields_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating fields of nonexistent media buy returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_fields("nonexistent_mb", order_name="x")
            assert result is None

    def test_update_fields_invalid_attribute_raises(self, tenant_a, principal_a):
        """Updating a nonexistent attribute raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_invalid_field")
            uow.media_buys.create(mb)

        with pytest.raises(ValueError, match="has no attribute"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.update_fields("mb_invalid_field", nonexistent_field="value")

    def test_update_fields_tenant_isolation(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update fields of media buy in another tenant."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_fields_iso")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_fields("mb_fields_iso", order_name="hacked")
            assert result is None

        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_by_id("mb_fields_iso")
            assert fetched is not None
            assert fetched.order_name == "Order mb_fields_iso"


# ---------------------------------------------------------------------------
# MediaPackage.create_package — roundtrip and tenant isolation
# ---------------------------------------------------------------------------


class TestCreatePackage:
    """Repository.create_package() creates a package for a tenant-scoped media buy."""

    def test_roundtrip_create_package(self, tenant_a, principal_a):
        """Create a package and read it back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_pkg_create")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            pkg = uow.media_buys.create_package(
                media_buy_id="mb_pkg_create",
                package_id="pkg_1",
                package_config={"name": "Test Package", "product_id": "prod_1"},
                budget=Decimal("1000.00"),
                bid_price=Decimal("5.50"),
                pacing="even",
            )
            assert pkg.package_id == "pkg_1"
            assert pkg.budget == Decimal("1000.00")

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_pkg_create", "pkg_1")
            assert fetched is not None
            assert fetched.package_id == "pkg_1"
            assert fetched.package_config == {"name": "Test Package", "product_id": "prod_1"}
            assert fetched.budget == Decimal("1000.00")
            assert fetched.bid_price == Decimal("5.50")
            assert fetched.pacing == "even"

    def test_create_package_nonexistent_media_buy_raises(self, tenant_a, principal_a):
        """Creating a package for a nonexistent media buy raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_package(
                    media_buy_id="nonexistent_mb",
                    package_id="pkg_x",
                    package_config={"test": True},
                )

    def test_create_package_other_tenant_media_buy_raises(self, tenant_a, tenant_b, principal_a, principal_b):
        """Creating a package for another tenant's media buy raises ValueError."""
        # Create media buy in tenant B
        with MediaBuyUoW(tenant_b) as uow:
            mb = _make_media_buy(tenant_b, principal_b, "mb_other_tenant_pkg")
            uow.media_buys.create(mb)

        # Try to create package from tenant A
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_package(
                    media_buy_id="mb_other_tenant_pkg",
                    package_id="pkg_cross",
                    package_config={"test": True},
                )

    def test_create_package_with_no_optional_fields(self, tenant_a, principal_a):
        """Create a package with only required fields (no budget/bid_price/pacing)."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_pkg_minimal")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            pkg = uow.media_buys.create_package(
                media_buy_id="mb_pkg_minimal",
                package_id="pkg_min",
                package_config={"name": "Minimal"},
            )
            assert pkg.budget is None
            assert pkg.bid_price is None
            assert pkg.pacing is None


# ---------------------------------------------------------------------------
# MediaPackage.update_package_config
# ---------------------------------------------------------------------------


class TestUpdatePackageConfig:
    """Repository.update_package_config() replaces the JSON config."""

    def test_roundtrip_update_config(self, tenant_a, principal_a):
        """Update package_config and read back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_upd_cfg")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_upd_cfg",
                package_id="pkg_cfg",
                package_config={"version": 1},
            )

        new_config = {"version": 2, "extra_field": "new"}
        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_config("mb_upd_cfg", "pkg_cfg", new_config)
            assert result is not None
            assert result.package_config == new_config

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_upd_cfg", "pkg_cfg")
            assert fetched is not None
            assert fetched.package_config == new_config

    def test_update_config_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating config of nonexistent package returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_no_pkg_cfg")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_config("mb_no_pkg_cfg", "nonexistent", {})
            assert result is None

    def test_update_config_other_tenant_returns_none(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot update package config via another tenant's repository."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_cfg_iso")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_cfg_iso",
                package_id="pkg_cfg_iso",
                package_config={"original": True},
            )

        with MediaBuyUoW(tenant_b) as uow:
            result = uow.media_buys.update_package_config("mb_cfg_iso", "pkg_cfg_iso", {"hacked": True})
            assert result is None

        # Verify original unchanged
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_cfg_iso", "pkg_cfg_iso")
            assert fetched is not None
            assert fetched.package_config == {"original": True}


# ---------------------------------------------------------------------------
# MediaPackage.update_package_fields
# ---------------------------------------------------------------------------


class TestUpdatePackageFields:
    """Repository.update_package_fields() updates arbitrary package attributes."""

    def test_roundtrip_update_package_fields(self, tenant_a, principal_a):
        """Update package fields and verify persistence."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_pkg_fields")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_pkg_fields",
                package_id="pkg_fld",
                package_config={"test": True},
                budget=Decimal("500.00"),
            )

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_fields(
                "mb_pkg_fields",
                "pkg_fld",
                budget=Decimal("1500.00"),
                pacing="asap",
            )
            assert result is not None
            assert result.budget == Decimal("1500.00")
            assert result.pacing == "asap"

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_package("mb_pkg_fields", "pkg_fld")
            assert fetched is not None
            assert fetched.budget == Decimal("1500.00")
            assert fetched.pacing == "asap"

    def test_update_package_fields_nonexistent_returns_none(self, tenant_a, principal_a):
        """Updating fields of nonexistent package returns None."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_no_pkg_fld")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.update_package_fields("mb_no_pkg_fld", "nope", budget=Decimal("1.00"))
            assert result is None

    def test_update_package_fields_invalid_attribute_raises(self, tenant_a, principal_a):
        """Updating a nonexistent package attribute raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_pkg_bad_attr")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            uow.media_buys.create_package(
                media_buy_id="mb_pkg_bad_attr",
                package_id="pkg_bad",
                package_config={"test": True},
            )

        with pytest.raises(ValueError, match="has no attribute"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.update_package_fields("mb_pkg_bad_attr", "pkg_bad", fake_field="x")


# ---------------------------------------------------------------------------
# create_packages_bulk — batch creation
# ---------------------------------------------------------------------------


class TestCreatePackagesBulk:
    """Repository.create_packages_bulk() creates multiple packages atomically."""

    def test_roundtrip_bulk_create(self, tenant_a, principal_a):
        """Bulk create packages and read them all back."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_bulk")
            uow.media_buys.create(mb)

        packages = [
            _make_package("mb_bulk", "pkg_bulk_1", budget=Decimal("100.00")),
            _make_package("mb_bulk", "pkg_bulk_2", budget=Decimal("200.00")),
            _make_package("mb_bulk", "pkg_bulk_3"),
        ]

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.create_packages_bulk("mb_bulk", packages)
            assert len(result) == 3

        # Read back
        with get_db_session() as session:
            repo = MediaBuyRepository(session, tenant_a)
            fetched = repo.get_packages("mb_bulk")
            assert len(fetched) == 3
            pkg_ids = {p.package_id for p in fetched}
            assert pkg_ids == {"pkg_bulk_1", "pkg_bulk_2", "pkg_bulk_3"}

    def test_bulk_create_nonexistent_media_buy_raises(self, tenant_a, principal_a):
        """Bulk creating packages for nonexistent media buy raises ValueError."""
        packages = [_make_package("nonexistent_mb", "pkg_x")]
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("nonexistent_mb", packages)

    def test_bulk_create_media_buy_id_mismatch_raises(self, tenant_a, principal_a):
        """Bulk creating with mismatched media_buy_id raises ValueError."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_bulk_mismatch")
            uow.media_buys.create(mb)

        packages = [
            _make_package("mb_bulk_mismatch", "pkg_ok"),
            _make_package("wrong_mb_id", "pkg_bad"),
        ]

        with pytest.raises(ValueError, match="media_buy_id"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("mb_bulk_mismatch", packages)

    def test_bulk_create_empty_list(self, tenant_a, principal_a):
        """Bulk creating with empty list succeeds and returns empty."""
        with MediaBuyUoW(tenant_a) as uow:
            mb = _make_media_buy(tenant_a, principal_a, "mb_bulk_empty")
            uow.media_buys.create(mb)

        with MediaBuyUoW(tenant_a) as uow:
            result = uow.media_buys.create_packages_bulk("mb_bulk_empty", [])
            assert result == []

    def test_bulk_create_tenant_isolation(self, tenant_a, tenant_b, principal_a, principal_b):
        """Cannot bulk create packages via another tenant's repository."""
        with MediaBuyUoW(tenant_b) as uow:
            mb = _make_media_buy(tenant_b, principal_b, "mb_bulk_iso")
            uow.media_buys.create(mb)

        packages = [_make_package("mb_bulk_iso", "pkg_iso")]
        with pytest.raises(ValueError, match="not found"):
            with MediaBuyUoW(tenant_a) as uow:
                uow.media_buys.create_packages_bulk("mb_bulk_iso", packages)

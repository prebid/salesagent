"""Integration test for delivery_measurement migrations against a real PostgreSQL.

Tests the two-phase migration:
  1. 6aee724a2d1d — backfill NULL delivery_measurement with adapter-specific defaults
  2. aa005b733aed — add NOT NULL constraint with server_default

Verifies upgrade, data transformation, downgrade, and roundtrip on a real DB.
"""

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade

# Migration revisions under test
BACKFILL_REV = "6aee724a2d1d"
NOT_NULL_REV = "aa005b733aed"
PRE_BACKFILL_REV = "b4aa81561fea"  # revision before backfill


def _seed_test_data(engine):
    """Insert tenants with different adapter types and products with NULL delivery_measurement.

    Creates:
    - tenant_gam: GAM adapter → expects google_ad_manager provider
    - tenant_mock: mock adapter → expects mock provider
    - tenant_none: no adapter_config → expects publisher fallback
    - product with custom (non-NULL) delivery_measurement → must be preserved
    """
    with engine.connect() as conn:
        # Tenants
        for tid, name, sub in [
            ("tenant_gam", "GAM Tenant", "gam-test"),
            ("tenant_mock", "Mock Tenant", "mock-test"),
            ("tenant_none", "No Adapter Tenant", "noadapter-test"),
        ]:
            conn.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name, subdomain, created_at, updated_at) "
                    "VALUES (:tid, :name, :sub, NOW(), NOW())"
                ),
                {"tid": tid, "name": name, "sub": sub},
            )

        # Adapter configs (only for gam and mock tenants)
        conn.execute(
            text("INSERT INTO adapter_config (tenant_id, adapter_type) VALUES (:tid, :atype)"),
            {"tid": "tenant_gam", "atype": "google_ad_manager"},
        )
        conn.execute(
            text("INSERT INTO adapter_config (tenant_id, adapter_type) VALUES (:tid, :atype)"),
            {"tid": "tenant_mock", "atype": "mock"},
        )

        # Products with NULL delivery_measurement
        for pid, tid in [
            ("prod_gam", "tenant_gam"),
            ("prod_mock", "tenant_mock"),
            ("prod_none", "tenant_none"),
        ]:
            conn.execute(
                text(
                    "INSERT INTO products (tenant_id, product_id, name, description, "
                    "delivery_type, format_ids, targeting_template, property_tags) "
                    "VALUES (:tid, :pid, :name, 'test', 'guaranteed', "
                    "'[]'::jsonb, '{}'::jsonb, '[\"all_inventory\"]'::jsonb)"
                ),
                {"tid": tid, "pid": pid, "name": f"Product {pid}"},
            )

        # Product with CUSTOM delivery_measurement (must survive migration)
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, "
                "delivery_type, format_ids, targeting_template, property_tags, "
                "delivery_measurement) "
                "VALUES (:tid, :pid, :name, 'test', 'guaranteed', "
                "'[]'::jsonb, '{}'::jsonb, '[\"all_inventory\"]'::jsonb, "
                "CAST(:dm AS jsonb))"
            ),
            {
                "tid": "tenant_gam",
                "pid": "prod_custom",
                "name": "Custom Measurement Product",
                "dm": '{"provider": "moat", "notes": "Third-party verification"}',
            },
        )

        conn.commit()


def _get_delivery_measurement(engine, product_id):
    """Read delivery_measurement for a product."""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT delivery_measurement FROM products WHERE product_id = :pid"),
            {"pid": product_id},
        )
        row = result.fetchone()
        return row[0] if row else None


def _get_column_info(engine):
    """Get is_nullable and column_default for delivery_measurement."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'products' AND column_name = 'delivery_measurement'"
            ),
        )
        row = result.fetchone()
        return row if row else None


@pytest.mark.requires_db
class TestDeliveryMeasurementBackfillMigration:
    """Test migration 6aee724a2d1d: backfill NULL delivery_measurement."""

    def test_upgrade_backfills_gam_products(self, migration_db):
        """GAM tenant products get google_ad_manager provider."""
        engine, db_url = migration_db

        # Migrate to pre-backfill state and seed test data
        run_alembic_upgrade(db_url, PRE_BACKFILL_REV)
        _seed_test_data(engine)

        # Verify NULLs exist before backfill
        assert _get_delivery_measurement(engine, "prod_gam") is None

        # Run backfill migration
        run_alembic_upgrade(db_url, BACKFILL_REV)

        dm = _get_delivery_measurement(engine, "prod_gam")
        assert dm["provider"] == "google_ad_manager"
        assert "notes" in dm

    def test_upgrade_backfills_mock_products(self, migration_db):
        """Mock tenant products get mock provider."""
        engine, _ = migration_db
        dm = _get_delivery_measurement(engine, "prod_mock")
        assert dm["provider"] == "mock"
        assert "notes" in dm

    def test_upgrade_backfills_unknown_adapter_with_publisher(self, migration_db):
        """Products without adapter_config get publisher fallback."""
        engine, _ = migration_db
        dm = _get_delivery_measurement(engine, "prod_none")
        assert dm == {"provider": "publisher"}

    def test_upgrade_preserves_custom_delivery_measurement(self, migration_db):
        """Products with existing delivery_measurement are not overwritten."""
        engine, _ = migration_db
        dm = _get_delivery_measurement(engine, "prod_custom")
        assert dm["provider"] == "moat"
        assert dm["notes"] == "Third-party verification"

    def test_no_nulls_remain_after_upgrade(self, migration_db):
        """Every product must have a non-NULL delivery_measurement after backfill."""
        engine, _ = migration_db
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM products WHERE delivery_measurement IS NULL"))
            assert result.scalar() == 0

    def test_downgrade_clears_default_values(self, migration_db):
        """Downgrade sets matching defaults back to NULL."""
        engine, db_url = migration_db

        run_alembic_downgrade(db_url, PRE_BACKFILL_REV)

        # Backfilled products should be NULL again
        assert _get_delivery_measurement(engine, "prod_gam") is None
        assert _get_delivery_measurement(engine, "prod_mock") is None
        assert _get_delivery_measurement(engine, "prod_none") is None

    def test_downgrade_preserves_custom_delivery_measurement(self, migration_db):
        """Downgrade must not clear custom (non-default) values."""
        engine, _ = migration_db
        dm = _get_delivery_measurement(engine, "prod_custom")
        assert dm["provider"] == "moat"


@pytest.mark.requires_db
class TestDeliveryMeasurementNotNullMigration:
    """Test migration aa005b733aed: NOT NULL constraint + server_default."""

    def test_upgrade_adds_not_null_constraint(self, migration_db):
        """After upgrade, delivery_measurement column is NOT NULL."""
        engine, db_url = migration_db

        # Re-upgrade through both migrations
        run_alembic_upgrade(db_url, NOT_NULL_REV)

        info = _get_column_info(engine)
        assert info is not None
        assert info[0] == "NO", f"Expected NOT NULL (is_nullable='NO'), got: {info[0]}"

    def test_upgrade_adds_server_default(self, migration_db):
        """After upgrade, server_default is set to publisher JSON."""
        engine, _ = migration_db
        info = _get_column_info(engine)
        assert info is not None
        assert "publisher" in str(info[1]), f"Expected server_default with 'publisher', got: {info[1]}"

    def test_insert_without_delivery_measurement_uses_default(self, migration_db):
        """INSERT omitting delivery_measurement gets the server_default."""
        engine, _ = migration_db
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO products (tenant_id, product_id, name, description, "
                    "delivery_type, format_ids, targeting_template, property_tags) "
                    "VALUES (:tid, :pid, 'Default DM Test', 'test', 'guaranteed', "
                    "'[]'::jsonb, '{}'::jsonb, '[\"all_inventory\"]'::jsonb)"
                ),
                {"tid": "tenant_gam", "pid": "prod_default_dm"},
            )
            conn.commit()

        dm = _get_delivery_measurement(engine, "prod_default_dm")
        assert dm == {"provider": "publisher"}

    def test_update_to_null_rejected(self, migration_db):
        """UPDATE delivery_measurement to NULL must fail."""
        engine, _ = migration_db
        with engine.connect() as conn:
            with pytest.raises(Exception) as exc_info:
                conn.execute(text("UPDATE products SET delivery_measurement = NULL WHERE product_id = 'prod_gam'"))
            assert "not-null" in str(exc_info.value).lower() or "null" in str(exc_info.value).lower()

    def test_downgrade_removes_not_null_and_default(self, migration_db):
        """Downgrade reverts to nullable with no server_default."""
        engine, db_url = migration_db

        run_alembic_downgrade(db_url, BACKFILL_REV)

        info = _get_column_info(engine)
        assert info is not None
        assert info[0] == "YES", f"Expected nullable (is_nullable='YES'), got: {info[0]}"
        assert info[1] is None, f"Expected no server_default, got: {info[1]}"

    def test_roundtrip_upgrade_downgrade_upgrade(self, migration_db):
        """Full roundtrip: downgrade all the way, re-upgrade, verify data integrity."""
        engine, db_url = migration_db

        # Downgrade past backfill
        run_alembic_downgrade(db_url, PRE_BACKFILL_REV)

        # Custom value should survive all downgrades
        dm = _get_delivery_measurement(engine, "prod_custom")
        assert dm["provider"] == "moat"

        # Re-upgrade through both migrations
        run_alembic_upgrade(db_url, NOT_NULL_REV)

        # All products should have non-NULL values
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM products WHERE delivery_measurement IS NULL"))
            assert result.scalar() == 0

        # Custom value still intact
        dm = _get_delivery_measurement(engine, "prod_custom")
        assert dm["provider"] == "moat"

        # Column is NOT NULL with server_default
        info = _get_column_info(engine)
        assert info[0] == "NO"
        assert "publisher" in str(info[1])

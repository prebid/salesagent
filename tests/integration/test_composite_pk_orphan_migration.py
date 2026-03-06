"""Regression test for migration 1a88e4967119 — composite PK with orphan rows.

Bug salesagent-0iig: The migration sets orphan creative_reviews/creative_assignments
rows to principal_id='unknown', then creates a composite FK requiring
(creative_id, tenant_id, principal_id) to exist in creatives. Orphan rows have
creative_ids that don't exist in creatives — FK creation fails with referential
integrity violation.

Fix: DELETE orphan rows before FK creation (parent creative is gone, reviews are
meaningless).
"""

import os
import re
import uuid

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, text

MIGRATION_REV = "1a88e4967119"
PRE_MIGRATION_REV = "3a16c5fc27ce"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _parse_postgres_url():
    """Parse DATABASE_URL into connection components."""
    postgres_url = os.environ.get("DATABASE_URL", "")
    match = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", postgres_url)
    if not match:
        return None
    user, password, host, port_str, _ = match.groups()
    return user, password, host, int(port_str)


def _run_alembic(db_url, target_revision):
    """Run Alembic upgrade to a specific revision."""
    from alembic.config import Config

    from alembic import command

    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, target_revision)
    finally:
        if old_url:
            os.environ["DATABASE_URL"] = old_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]


@pytest.fixture(scope="module")
def migration_db():
    """Create an isolated PostgreSQL database for migration testing."""
    parsed = _parse_postgres_url()
    if not parsed:
        pytest.skip("Requires PostgreSQL DATABASE_URL")

    user, password, host, port = parsed
    db_name = f"test_orphan_mig_{uuid.uuid4().hex[:8]}"

    conn_params = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": "postgres",
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE "{db_name}"')
    cur.close()
    conn.close()

    db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    engine = create_engine(db_url, echo=False)

    yield engine, db_url

    engine.dispose()
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.close()
        conn.close()
    except Exception:
        pass


def _insert_orphan_data(engine):
    """Insert test data then create orphan rows by deleting the parent creative.

    Strategy: insert valid data with FK constraints satisfied, then delete the
    parent creative with CASCADE disabled so child rows survive as orphans.
    This simulates the real-world scenario where FK constraints were removed
    or data was manipulated outside normal application flow.
    """
    with engine.connect() as conn:
        # Tenant
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, created_at, updated_at) "
                "VALUES ('t1', 'Test Tenant', 't1-sub', NOW(), NOW())"
            )
        )
        # Principal (minimal columns for pre-migration schema)
        conn.execute(
            text(
                "INSERT INTO principals (principal_id, tenant_id, name, platform_mappings, access_token, created_at) "
                "VALUES ('p1', 't1', 'Test Principal', '{}', 'tok_test_orphan', NOW())"
            )
        )
        # Media buy (needed for creative_assignments FK)
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, "
                "advertiser_name, start_date, end_date, status, raw_request) "
                "VALUES ('mb1', 't1', 'p1', 'Test Order', 'Test Advertiser', "
                "CURRENT_DATE, CURRENT_DATE + 7, 'draft', '{}')"
            )
        )
        # Two creatives: one to keep, one to delete (creating orphans)
        for cid in ("c_valid", "c_deleted"):
            conn.execute(
                text(
                    "INSERT INTO creatives (creative_id, tenant_id, principal_id, name, agent_url, format, data, status) "
                    "VALUES (:cid, 't1', 'p1', 'Creative', 'https://example.com', 'display', '{}', 'approved')"
                ),
                {"cid": cid},
            )
        # Creative review for each creative
        conn.execute(
            text(
                "INSERT INTO creative_reviews (review_id, creative_id, tenant_id, review_type, "
                "final_decision, reviewed_at) "
                "VALUES ('r_valid', 'c_valid', 't1', 'ai', 'approved', NOW())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO creative_reviews (review_id, creative_id, tenant_id, review_type, "
                "final_decision, reviewed_at) "
                "VALUES ('r_orphan', 'c_deleted', 't1', 'ai', 'approved', NOW())"
            )
        )
        # Creative assignments for each creative
        conn.execute(
            text(
                "INSERT INTO creative_assignments (assignment_id, tenant_id, creative_id, "
                "media_buy_id, package_id, weight) "
                "VALUES ('a_valid', 't1', 'c_valid', 'mb1', 'pkg1', 100)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO creative_assignments (assignment_id, tenant_id, creative_id, "
                "media_buy_id, package_id, weight) "
                "VALUES ('a_orphan', 't1', 'c_deleted', 'mb1', 'pkg1', 100)"
            )
        )
        conn.commit()

    # Now drop FK constraints on child tables so we can delete the creative
    # without CASCADE removing the child rows — this creates genuine orphans
    with engine.connect() as conn:
        # Find and drop FK constraints from creative_reviews and creative_assignments
        # that reference creatives
        fk_rows = conn.execute(
            text("""
                SELECT tc.constraint_name, tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND ccu.table_name = 'creatives'
                    AND ccu.column_name = 'creative_id'
                    AND tc.table_name IN ('creative_reviews', 'creative_assignments')
            """)
        ).fetchall()
        for constraint_name, table_name in fk_rows:
            conn.execute(text(f'ALTER TABLE {table_name} DROP CONSTRAINT "{constraint_name}"'))
        # Delete the creative to create orphans
        conn.execute(text("DELETE FROM creatives WHERE creative_id = 'c_deleted'"))
        # Re-add the FK constraints (they existed before our migration)
        conn.execute(
            text(
                "ALTER TABLE creative_reviews ADD CONSTRAINT creative_reviews_creative_id_fkey "
                "FOREIGN KEY (creative_id) REFERENCES creatives(creative_id) ON DELETE CASCADE "
                "NOT VALID"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE creative_assignments ADD CONSTRAINT creative_assignments_creative_id_fkey "
                "FOREIGN KEY (creative_id) REFERENCES creatives(creative_id) ON DELETE CASCADE "
                "NOT VALID"
            )
        )
        conn.commit()


class TestCompositePKOrphanMigration:
    """Migration 1a88e4967119 must handle orphan rows that reference deleted creatives."""

    def test_upgrade_succeeds_with_orphan_reviews_and_assignments(self, migration_db):
        """Orphan rows in creative_reviews/creative_assignments must be deleted,
        not just set to principal_id='unknown', because the FK requires the
        (creative_id, tenant_id, principal_id) tuple to exist in creatives.
        """
        engine, db_url = migration_db

        # Step 1: Migrate to the revision BEFORE the composite PK migration
        _run_alembic(db_url, PRE_MIGRATION_REV)

        # Step 2: Insert test data including orphan rows
        _insert_orphan_data(engine)

        # Verify orphans exist before migration
        with engine.connect() as conn:
            orphan_reviews = conn.execute(
                text("SELECT count(*) FROM creative_reviews WHERE creative_id = 'c_deleted'")
            ).scalar()
            assert orphan_reviews == 1, "Orphan review should exist before migration"

            orphan_assignments = conn.execute(
                text("SELECT count(*) FROM creative_assignments WHERE creative_id = 'c_deleted'")
            ).scalar()
            assert orphan_assignments == 1, "Orphan assignment should exist before migration"

        # Step 3: Run the composite PK migration — this MUST succeed
        # Bug: without the fix, this raises IntegrityError because orphan rows
        # have principal_id='unknown' but (c_deleted, t1, unknown) doesn't exist in creatives
        _run_alembic(db_url, MIGRATION_REV)

        # Step 4: Verify orphan rows were deleted
        with engine.connect() as conn:
            orphan_reviews = conn.execute(
                text("SELECT count(*) FROM creative_reviews WHERE creative_id = 'c_deleted'")
            ).scalar()
            assert orphan_reviews == 0, "Orphan reviews should be deleted by migration"

            orphan_assignments = conn.execute(
                text("SELECT count(*) FROM creative_assignments WHERE creative_id = 'c_deleted'")
            ).scalar()
            assert orphan_assignments == 0, "Orphan assignments should be deleted by migration"

        # Step 5: Verify valid rows survived with correct principal_id
        with engine.connect() as conn:
            valid_review = conn.execute(
                text("SELECT principal_id FROM creative_reviews WHERE review_id = 'r_valid'")
            ).fetchone()
            assert valid_review is not None, "Valid review should survive migration"
            assert valid_review[0] == "p1", "Valid review should have principal_id from creatives"

            valid_assignment = conn.execute(
                text("SELECT principal_id FROM creative_assignments WHERE assignment_id = 'a_valid'")
            ).fetchone()
            assert valid_assignment is not None, "Valid assignment should survive migration"
            assert valid_assignment[0] == "p1", "Valid assignment should have principal_id from creatives"

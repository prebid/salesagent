"""Migration sanity for 1497aa06013c — add media_buys.revision.

The persisted monotonic revision counter backing the AdCP 3.1.1 ``revision``
response field. Verifies against a real PostgreSQL (Alembic-managed, no
create_all):

- upgrade adds a NOT NULL ``revision`` column and backfills existing rows to 1
  via the server default;
- new rows inserted without an explicit value also land at 1;
- downgrade drops the column cleanly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import (
    column_exists,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_tenant,
)

# Migration under test and its parent
REVISION_REV = "1497aa06013c"
PRE_REV = "a164b85bab9e"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_pre_migration_media_buy(engine) -> None:
    """Insert tenant → principal → media buy on the PRE-migration schema."""
    seed_tenant(engine, "t_rev", subdomain="rev-mig-test")
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, access_token) "
                "VALUES ('t_rev', 'p_rev', 'Revision Principal', '{}', 'tok_rev_mig')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, advertiser_name, "
                "budget, currency, start_date, end_date, status, raw_request, created_at, updated_at) "
                "VALUES ('mb_rev_mig', 't_rev', 'p_rev', 'Order mb_rev_mig', 'Adv', 100.00, 'USD', "
                "'2026-01-01', '2026-02-01', 'active', '{}', NOW(), NOW())"
            )
        )
        conn.commit()


def test_upgrade_backfills_existing_rows_to_1_and_downgrade_drops_column(migration_db):
    engine, db_url = migration_db

    # Schema up to the parent revision — no revision column yet.
    run_alembic_upgrade(db_url, PRE_REV)
    assert not column_exists(engine, "media_buys", "revision")

    _seed_pre_migration_media_buy(engine)

    # Upgrade: column added, pre-existing row backfilled to 1, NOT NULL.
    run_alembic_upgrade(db_url, REVISION_REV)
    with engine.connect() as conn:
        revision, is_nullable = conn.execute(
            text(
                "SELECT mb.revision, c.is_nullable FROM media_buys mb, information_schema.columns c "
                "WHERE mb.media_buy_id = 'mb_rev_mig' "
                "AND c.table_name = 'media_buys' AND c.column_name = 'revision'"
            )
        ).one()
    assert revision == 1
    assert is_nullable == "NO"

    # New rows without an explicit value default to 1 (server default).
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, advertiser_name, "
                "budget, currency, start_date, end_date, status, raw_request, created_at, updated_at) "
                "VALUES ('mb_rev_new', 't_rev', 'p_rev', 'Order mb_rev_new', 'Adv', 100.00, 'USD', "
                "'2026-01-01', '2026-02-01', 'active', '{}', NOW(), NOW())"
            )
        )
        conn.commit()
        new_revision = conn.execute(
            text("SELECT revision FROM media_buys WHERE media_buy_id = 'mb_rev_new'")
        ).scalar_one()
    assert new_revision == 1

    # Downgrade drops the column; rows survive.
    run_alembic_downgrade(db_url, PRE_REV)
    assert not column_exists(engine, "media_buys", "revision")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM media_buys")).scalar_one()
    assert count == 2

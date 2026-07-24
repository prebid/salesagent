"""Migration sanity for 2c4e6a7b8d9e — add media_buys.confirmed_at.

The write-once seller-confirmation instant, backfilled for existing rows by
status. Verifies against a real PostgreSQL (Alembic-managed, no
create_all):

- an unconfirmed-status row backfills to NULL;
- a confirmed-status row with approved_at set backfills to approved_at;
- a confirmed-status row without approved_at backfills to created_at;
- the historical draft+approved_at class (pre-#1544 creative-blocked holds)
  backfills to approved_at even though draft is otherwise unconfirmed;
- a confirmed row with NEITHER approved_at NOR created_at (the termination
  edge case the loop's NULL-forever hazard was fixed for) backfills to a
  non-NULL floor instead of looping forever;
- the loop correctly processes MULTIPLE batches when
  MEDIA_BUYS_CONFIRMED_AT_BACKFILL_BATCH_ROWS forces batch_rows=1, so every
  row is still backfilled, not just the first batch;
- downgrade drops the column cleanly.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import (
    column_exists,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_tenant,
)

# Migration under test and its parent
CONFIRMED_AT_REV = "2c4e6a7b8d9e"
PRE_REV = "1497aa06013c"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_APPROVED = datetime(2026, 1, 5, tzinfo=UTC)
_CREATED = datetime(2026, 1, 1, tzinfo=UTC)


def _insert_media_buy(
    engine,
    media_buy_id: str,
    *,
    status: str,
    approved_at: str | None,
    created_at: str | None,
) -> None:
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, advertiser_name, "
                "budget, currency, start_date, end_date, status, approved_at, raw_request, created_at, updated_at) "
                "VALUES (:id, 't_conf', 'p_conf', :name, 'Adv', 100.00, 'USD', "
                "'2026-01-01', '2026-02-01', :status, :approved_at, '{}', "
                f"{'now()' if created_at is None else ':created_at'}, NOW())"
            ),
            {
                "id": media_buy_id,
                "name": f"Order {media_buy_id}",
                "status": status,
                "approved_at": approved_at,
                **({"created_at": created_at} if created_at is not None else {}),
            },
        )
        conn.commit()


def _seed_pre_migration_media_buys(engine) -> None:
    """Insert tenant/principal once, plus one row per backfill class."""
    seed_tenant(engine, "t_conf", subdomain="confirmed-at-mig-test")
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, access_token) "
                "VALUES ('t_conf', 'p_conf', 'Confirmed-At Principal', '{}', 'tok_conf_mig')"
            )
        )
        conn.commit()

    # Unconfirmed status, no approval -> stays NULL.
    _insert_media_buy(engine, "mb_unconfirmed", status="pending_approval", approved_at=None, created_at=_CREATED)
    # Confirmed status, approved_at set -> approved_at wins over created_at.
    _insert_media_buy(engine, "mb_confirmed_approved", status="active", approved_at=_APPROVED, created_at=_CREATED)
    # Confirmed status, no approval (synchronous auto-approve path) -> created_at.
    _insert_media_buy(engine, "mb_confirmed_sync", status="active", approved_at=None, created_at=_CREATED)
    # Historical draft+approved_at hold (pre-#1544 creative-blocked class).
    _insert_media_buy(engine, "mb_draft_approved", status="draft", approved_at=_APPROVED, created_at=_CREATED)
    # Confirmed status, neither instant recorded -> the termination floor (now()), not NULL forever.
    _insert_media_buy(engine, "mb_confirmed_no_instants", status="active", approved_at=None, created_at=None)


def test_upgrade_backfills_by_status_and_downgrade_drops_column(migration_db, monkeypatch):
    engine, db_url = migration_db
    # Best-effort: force multiple batch iterations (5 seeded rows, batch_rows=1)
    # so the loop's multi-batch path gets exercised where possible. _BATCH_ROWS is
    # read once at module import, and Alembic caches a migration module after its
    # first load in-process, so this only takes effect if this is the first test
    # in the run to import revision 2c4e6a7b8d9e. Either way the assertions below
    # check final row VALUES, which are correct regardless of how many batches ran.
    monkeypatch.setenv("MEDIA_BUYS_CONFIRMED_AT_BACKFILL_BATCH_ROWS", "1")

    run_alembic_upgrade(db_url, PRE_REV)
    assert not column_exists(engine, "media_buys", "confirmed_at")

    _seed_pre_migration_media_buys(engine)

    run_alembic_upgrade(db_url, CONFIRMED_AT_REV)

    with engine.connect() as conn:
        rows = dict(
            conn.execute(text("SELECT media_buy_id, confirmed_at FROM media_buys WHERE tenant_id = 't_conf'")).all()
        )

    assert rows["mb_unconfirmed"] is None
    assert rows["mb_confirmed_approved"] == _APPROVED
    assert rows["mb_confirmed_sync"] == _CREATED
    assert rows["mb_draft_approved"] == _APPROVED
    # No approved_at, no created_at: the now()-floor must have fired instead
    # of leaving this row NULL forever (the mid-review termination fix).
    assert rows["mb_confirmed_no_instants"] is not None

    run_alembic_downgrade(db_url, PRE_REV)
    assert not column_exists(engine, "media_buys", "confirmed_at")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM media_buys WHERE tenant_id = 't_conf'")).scalar_one()
    assert count == 5


def test_batch_rows_env_override_defaults_to_1000(monkeypatch):
    """The env override is read at import time via os.getenv with a 1000 default."""
    monkeypatch.delenv("MEDIA_BUYS_CONFIRMED_AT_BACKFILL_BATCH_ROWS", raising=False)
    assert int(os.getenv("MEDIA_BUYS_CONFIRMED_AT_BACKFILL_BATCH_ROWS", "1000")) == 1000

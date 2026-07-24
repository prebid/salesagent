"""Persist the seller confirmation instant for media buys.

The value is write-once: approved_at may change or be cleared by later
workflow transitions, while AdCP's confirmed_at is the instant the seller
committed to the buy.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2c4e6a7b8d9e"
down_revision: str | Sequence[str] | None = "1497aa06013c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Rows updated per statement in the confirmed_at backfill (bounds the write-lock
# footprint of each batch on a large media_buys table).
_BATCH_ROWS = 1000


def upgrade() -> None:
    op.add_column("media_buys", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    # The hardcoded status set mirrors MEDIA_BUY_UNCONFIRMED_STATUSES
    # (src/core/database/models.py) as of this migration's authoring. Migrations
    # are frozen once committed, so this literal is deliberately NOT imported from
    # that constant — a later change to the runtime set does not (and must not)
    # rewrite already-applied history.
    #
    # The runtime set gained a sixth member (``finalizing``) AFTER this revision:
    # ``finalizing`` is introduced by 7f3a9c1d5e2b, which lists THIS revision as a
    # down_revision (it runs strictly later). So no row can be ``finalizing`` when
    # this backfill executes, and its absence from the literal below is correct —
    # were it reachable it would need excluding (it is an unconfirmed status).
    #
    # The extra ``OR (status = 'draft' AND approved_at IS NOT NULL)`` clause
    # backfills a historical class: before #1544, the admin approve route parked a
    # seller-approved-but-creative-blocked buy at ``draft`` (with ``approved_at``
    # stamped) instead of ``pending_creatives``. Those rows WERE seller-confirmed,
    # so their ``confirmed_at`` must be set from the approval instant even though
    # ``draft`` is otherwise an unconfirmed status. (Going forward the route holds
    # such buys at ``pending_creatives``, so this only reaches pre-fix rows.)
    #
    # Batched to bound the DURATION and WAL/memory burst of each individual
    # statement on a large media_buys table. It does NOT bound the held-lock
    # footprint: alembic/env.py runs all migrations inside ONE transaction
    # (no transaction_per_migration), and op.get_bind() returns that same
    # connection, so every batch's row locks accumulate until the migration
    # commits. Nor is it batched by primary key — the CTE takes an unordered
    # LIMIT over the not-yet-set rows.
    #
    # TERMINATION: the loop advances only if each claimed row leaves the
    # ``confirmed_at IS NULL`` predicate, so the SET must never evaluate to
    # NULL. ``media_buys.created_at`` is nullable in the DDL (initial_schema.py;
    # the ORM's nullable=False is ORM-only and does not constrain rows already
    # in the table), so COALESCE(approved_at, created_at) alone can yield NULL
    # and re-claim the same batch forever. now() is the terminating floor: a row
    # with neither instant recorded is confirmed as of this backfill.
    connection = op.get_bind()
    while True:
        result = connection.execute(
            sa.text(
                """
                WITH batch AS (
                    SELECT media_buy_id
                    FROM media_buys
                    WHERE confirmed_at IS NULL
                      AND (
                        status NOT IN ('draft', 'pending', 'pending_approval', 'rejected', 'failed')
                        OR (status = 'draft' AND approved_at IS NOT NULL)
                      )
                    LIMIT :batch_rows
                )
                UPDATE media_buys AS mb
                SET confirmed_at = COALESCE(mb.approved_at, mb.created_at, now())
                FROM batch
                WHERE mb.media_buy_id = batch.media_buy_id
                """
            ),
            {"batch_rows": _BATCH_ROWS},
        )
        if result.rowcount == 0:
            break


def downgrade() -> None:
    op.drop_column("media_buys", "confirmed_at")

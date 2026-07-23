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
    # Batched by primary key so a large media_buys table is never locked in a
    # single wide write: each statement claims at most _BATCH_ROWS not-yet-set
    # rows (``confirmed_at IS NULL`` makes every batch make progress and the loop
    # terminate), bounding the lock footprint per statement.
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
                SET confirmed_at = COALESCE(mb.approved_at, mb.created_at)
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

"""widen media_buys idempotency backstop to account scope

Revision ID: a164b85bab9e
Revises: 7a8c3e1170a5
Create Date: 2026-06-10 23:11:52.533121

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a164b85bab9e"
down_revision: str | Sequence[str] | None = "7a8c3e1170a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add account_id to the media_buys dup-booking backstop + a payload_hash column.

    AdCP idempotency scope is (agent, account, key). The backstop index was
    account-blind, so a race loser under account B could be resolved to account
    A's buy. Widening is data-safe: every row set unique under the 3-column
    index is unique under the 4-column one.

    media_buys is a hot, populated production table, so:

    - ``payload_hash`` is nullable with no default and no backfill (instant DDL;
      legacy rows carry NULL = no conflict signal). It stores the same canonical
      request hash the idempotency probe computes — ``raw_request`` is not
      canonicalizable (injected package_ids, alias-dependent field names), so
      the degraded fallback conflict-checks against this column instead.
    - The index swap runs CONCURRENTLY in an autocommit block, building the new
      index under a temporary name first so the dup-booking backstop never has
      a coverage gap, then dropping the old one and renaming.
    """
    op.add_column(
        "media_buys",
        sa.Column(
            "payload_hash",
            sa.String(length=64),
            nullable=True,
            comment=(
                "RFC 8785 JCS SHA-256 of the create request (excluded fields stripped); "
                "degraded-path IDEMPOTENCY_CONFLICT signal"
            ),
        ),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_media_buys_idempotency_key_acct "
            "ON media_buys (tenant_id, principal_id, account_id, idempotency_key) "
            "NULLS NOT DISTINCT WHERE idempotency_key IS NOT NULL"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_media_buys_idempotency_key")
        op.execute("ALTER INDEX idx_media_buys_idempotency_key_acct RENAME TO idx_media_buys_idempotency_key")


def downgrade() -> None:
    """Revert to the account-blind backstop and drop payload_hash.

    Narrowing the unique scope can fail with UniqueViolation if cross-account
    same-key buys were created under the widened schema — those rows are real
    bookings and are NOT deleted here. If the index build fails, an operator
    must resolve the key collisions (e.g. NULL the idempotency_key on the
    duplicates) before re-running the downgrade.
    """
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_media_buys_idempotency_key_noacct "
            "ON media_buys (tenant_id, principal_id, idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_media_buys_idempotency_key")
        op.execute("ALTER INDEX idx_media_buys_idempotency_key_noacct RENAME TO idx_media_buys_idempotency_key")
    op.drop_column("media_buys", "payload_hash")

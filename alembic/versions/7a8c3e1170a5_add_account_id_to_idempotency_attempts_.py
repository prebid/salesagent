"""add account_id to idempotency_attempts scope

Revision ID: 7a8c3e1170a5
Revises: 1d9b1402eacb
Create Date: 2026-06-09 13:04:43.157198

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a8c3e1170a5"
down_revision: str | Sequence[str] | None = "1d9b1402eacb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add account_id to the idempotency scope.

    AdCP idempotency scope is (agent, account, key); the table previously keyed
    only on (tenant, principal, tool, key), so two accounts under one principal
    reusing a key for different payloads would falsely collide. Add account_id
    (nullable) and rebuild the unique index with NULLS NOT DISTINCT so a NULL
    account (no sub-account) still enforces uniqueness on the rest of the tuple.
    """
    op.add_column(
        "idempotency_attempts",
        sa.Column(
            "account_id",
            sa.String(length=255),
            nullable=True,
            comment=(
                "Resolved account scope (AdCP idempotency scope is agent+account+key); "
                "NULL when the buy targets no sub-account"
            ),
        ),
    )
    op.drop_index("idx_idempotency_attempts_lookup", table_name="idempotency_attempts")
    op.create_index(
        "idx_idempotency_attempts_lookup",
        "idempotency_attempts",
        ["tenant_id", "principal_id", "account_id", "idempotency_key"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Revert account_id from the idempotency scope.

    The cache is cleared first: rows that differ only in account_id collide
    under the narrower 4-column unique index, so recreating it over existing
    feature data would fail with UniqueViolation and wedge the rollback. Safe
    to clear — this table is a TTL-bounded replay cache rebuilt on use, never
    the source of truth for bookings (that is media_buys).
    """
    op.drop_index("idx_idempotency_attempts_lookup", table_name="idempotency_attempts")
    op.execute("DELETE FROM idempotency_attempts")
    op.create_index(
        "idx_idempotency_attempts_lookup",
        "idempotency_attempts",
        ["tenant_id", "principal_id", "idempotency_key"],
        unique=True,
    )
    op.drop_column("idempotency_attempts", "account_id")

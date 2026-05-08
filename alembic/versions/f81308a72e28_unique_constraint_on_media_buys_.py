"""unique constraint on media_buys external_id per tenant

Promotes the existing ``idx_media_buys_external_id`` partial index from
non-unique to UNIQUE so two concurrent materializations of the same GAM
order get a clean DB-level rejection instead of silently producing
duplicate rows. Combined with the IntegrityError catch in
``materialize_projected_buy``, the race resolves cleanly: one inserter
wins, the loser re-fetches and continues.

Revision ID: f81308a72e28
Revises: d8e9f0a1b2c3
Create Date: 2026-05-07

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f81308a72e28"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_media_buys_external_id", table_name="media_buys")
    op.create_index(
        "idx_media_buys_external_id",
        "media_buys",
        ["tenant_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_media_buys_external_id", table_name="media_buys")
    op.create_index(
        "idx_media_buys_external_id",
        "media_buys",
        ["tenant_id", "external_id"],
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

"""add idempotency_key to media_buys

Revision ID: d40df2c92316
Revises: c612d0326eb0
Create Date: 2026-04-17 09:54:20.267314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd40df2c92316'
down_revision: Union[str, Sequence[str], None] = 'c612d0326eb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add idempotency_key column with partial unique index."""
    op.add_column(
        "media_buys",
        sa.Column("idempotency_key", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_media_buys_idempotency_key",
        "media_buys",
        ["tenant_id", "principal_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove idempotency_key column and index."""
    op.drop_index("idx_media_buys_idempotency_key", table_name="media_buys")
    op.drop_column("media_buys", "idempotency_key")

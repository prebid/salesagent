"""add beta4 product signal fields

Revision ID: d4e5f6a7b8c9
Revises: c67ff82b7514
Create Date: 2026-05-26 08:12:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c67ff82b7514"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("included_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "products", sa.Column("signal_targeting_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "products", sa.Column("signal_targeting_options", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("products", "signal_targeting_options")
    op.drop_column("products", "signal_targeting_rules")
    op.drop_column("products", "included_signals")

"""drop buyer_ref from media_buys

Revision ID: cf421634d3c8
Revises: d40df2c92316
Create Date: 2026-04-17 10:15:05.228315

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf421634d3c8'
down_revision: Union[str, Sequence[str], None] = 'd40df2c92316'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop buyer_ref column, unique constraint, and index from media_buys.

    BREAKING: buyer_ref is no longer used for dedup. idempotency_key replaces it.
    """
    op.drop_constraint("uq_media_buys_buyer_ref", "media_buys", type_="unique")
    op.drop_index("ix_media_buys_buyer_ref", table_name="media_buys")
    op.drop_column("media_buys", "buyer_ref")


def downgrade() -> None:
    """Re-add buyer_ref column, index, and unique constraint."""
    op.add_column(
        "media_buys",
        sa.Column("buyer_ref", sa.String(100), nullable=True),
    )
    op.create_index("ix_media_buys_buyer_ref", "media_buys", ["buyer_ref"])
    op.create_unique_constraint(
        "uq_media_buys_buyer_ref",
        "media_buys",
        ["tenant_id", "principal_id", "buyer_ref"],
    )

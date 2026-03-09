"""add is_paused column to media_buys

Revision ID: 5cf35536db6f
Revises: 886966ee9a9d
Create Date: 2026-03-05 14:45:23.064045

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cf35536db6f'
down_revision: Union[str, Sequence[str], None] = '886966ee9a9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_paused boolean column to media_buys table."""
    op.add_column(
        "media_buys",
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Remove is_paused column from media_buys table."""
    op.drop_column("media_buys", "is_paused")

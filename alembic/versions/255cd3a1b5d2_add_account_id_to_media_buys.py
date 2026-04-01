"""add account_id to media_buys

Revision ID: 255cd3a1b5d2
Revises: 51d4f9009db4
Create Date: 2026-03-19 00:22:10.883910

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '255cd3a1b5d2'
down_revision: Union[str, Sequence[str], None] = '51d4f9009db4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable account_id FK to media_buys table."""
    op.add_column("media_buys", sa.Column("account_id", sa.String(100), nullable=True))
    op.create_foreign_key(
        "fk_media_buys_account",
        "media_buys",
        "accounts",
        ["tenant_id", "account_id"],
        ["tenant_id", "account_id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_media_buys_account", "media_buys", ["account_id"])


def downgrade() -> None:
    """Remove account_id from media_buys."""
    op.drop_index("idx_media_buys_account", "media_buys")
    op.drop_constraint("fk_media_buys_account", "media_buys", type_="foreignkey")
    op.drop_column("media_buys", "account_id")

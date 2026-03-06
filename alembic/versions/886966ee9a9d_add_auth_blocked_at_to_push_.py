"""add auth_blocked_at to push_notification_configs

Revision ID: 886966ee9a9d
Revises: b4aa81561fea
Create Date: 2026-03-05 14:40:02.606419

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '886966ee9a9d'
down_revision: Union[str, Sequence[str], None] = 'b4aa81561fea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add auth_blocked_at column for persistent auth-failure blocking."""
    op.add_column(
        "push_notification_configs",
        sa.Column("auth_blocked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove auth_blocked_at column."""
    op.drop_column("push_notification_configs", "auth_blocked_at")

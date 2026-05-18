"""Merge migration heads

Revision ID: 044f0fee8ae9
Revises: 46d5d2ac70b0, b4e2bffdd4f8
Create Date: 2026-05-18 15:05:25.300925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '044f0fee8ae9'
down_revision: Union[str, Sequence[str], None] = ('46d5d2ac70b0', 'b4e2bffdd4f8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

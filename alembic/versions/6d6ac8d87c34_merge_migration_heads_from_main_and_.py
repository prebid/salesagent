"""Merge migration heads from main and pr79 branches

Revision ID: 6d6ac8d87c34
Revises: c115f6aa3687, f4f0feaaedff
Create Date: 2025-10-04 06:56:12.852633

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6d6ac8d87c34'
down_revision: Union[str, Sequence[str], None] = ('c115f6aa3687', 'f4f0feaaedff')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

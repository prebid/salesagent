"""Merge concurrent merge migrations

Revision ID: 393172c38f48
Revises: ba4adebb9c45, dc7ad64fff72
Create Date: 2026-05-08 09:16:53.562994

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '393172c38f48'
down_revision: Union[str, Sequence[str], None] = ('ba4adebb9c45', 'dc7ad64fff72')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

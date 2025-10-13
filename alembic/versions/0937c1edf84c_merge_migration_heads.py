"""Merge migration heads

Revision ID: 0937c1edf84c
Revises: 31ff6218695a, c3b75d304773
Create Date: 2025-10-12 23:13:46.604015

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0937c1edf84c'
down_revision: Union[str, Sequence[str], None] = ('31ff6218695a', 'c3b75d304773')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

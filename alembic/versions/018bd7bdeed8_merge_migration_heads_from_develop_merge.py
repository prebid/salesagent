"""Merge migration heads from develop merge

Revision ID: 018bd7bdeed8
Revises: 255cd3a1b5d2, 2e04733a751f
Create Date: 2026-03-21 09:19:58.940075

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '018bd7bdeed8'
down_revision: Union[str, Sequence[str], None] = ('255cd3a1b5d2', '2e04733a751f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""Merge migration heads after PR #186 + #193 land independently

Revision ID: ba4adebb9c45
Revises: 51a885014fac, f81308a72e28
Create Date: 2026-05-08 08:11:07.587757

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba4adebb9c45'
down_revision: Union[str, Sequence[str], None] = ('51a885014fac', 'f81308a72e28')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

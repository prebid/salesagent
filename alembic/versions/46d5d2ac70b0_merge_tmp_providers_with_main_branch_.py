"""merge tmp_providers with main branch migrations

Revision ID: 46d5d2ac70b0
Revises: 20260413120000, c612d0326eb0
Create Date: 2026-04-22 08:03:44.432656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46d5d2ac70b0'
down_revision: Union[str, Sequence[str], None] = ('20260413120000', 'c612d0326eb0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge migration heads

Revision ID: 067acc1c87dc
Revises: b4e2bffdd4f8, d12aae262177
Create Date: 2026-05-05 11:14:28.259936

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '067acc1c87dc'
down_revision: Union[str, Sequence[str], None] = ('b4e2bffdd4f8', 'd12aae262177')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge migration heads

Revision ID: c3d10c6688d1
Revises: 018bd7bdeed8, 13b5e73b6983
Create Date: 2026-04-01 20:19:32.451530

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d10c6688d1'
down_revision: Union[str, Sequence[str], None] = ('018bd7bdeed8', '13b5e73b6983')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

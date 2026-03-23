"""merge heads for account management

Revision ID: aa2e905fe772
Revises: 5cf35536db6f, aa005b733aed
Create Date: 2026-03-19 00:21:37.725679

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa2e905fe772'
down_revision: Union[str, Sequence[str], None] = ('5cf35536db6f', 'aa005b733aed')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge pricing and property migrations

Revision ID: 182e1c7dcd01
Revises: 5d949a78d36f, ab57bdcf4bd8
Create Date: 2025-10-13 12:02:44.864691

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '182e1c7dcd01'
down_revision: Union[str, Sequence[str], None] = ('5d949a78d36f', 'ab57bdcf4bd8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

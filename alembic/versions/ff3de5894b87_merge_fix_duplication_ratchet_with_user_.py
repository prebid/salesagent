"""merge fix-duplication-ratchet with user-role-rename

Revision ID: ff3de5894b87
Revises: e0f450f098de, ff860c4f32f6
Create Date: 2026-05-07 14:32:03.563682

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff3de5894b87'
down_revision: Union[str, Sequence[str], None] = ('e0f450f098de', 'ff860c4f32f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

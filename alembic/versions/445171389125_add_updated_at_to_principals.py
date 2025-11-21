"""add_updated_at_to_principals

Revision ID: 445171389125
Revises: 1ad9b025f95e
Create Date: 2025-11-21 21:30:55.873173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '445171389125'
down_revision: Union[str, Sequence[str], None] = '1ad9b025f95e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('principals', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('principals', 'updated_at')

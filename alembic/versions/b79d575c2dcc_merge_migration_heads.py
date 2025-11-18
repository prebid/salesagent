"""Merge migration heads

Revision ID: b79d575c2dcc
Revises: 4b8c3ffb6ae7, bef03cdc4629
Create Date: 2025-11-17 13:51:04.468473

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b79d575c2dcc"
down_revision: Union[str, Sequence[str], None] = ("4b8c3ffb6ae7", "bef03cdc4629")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

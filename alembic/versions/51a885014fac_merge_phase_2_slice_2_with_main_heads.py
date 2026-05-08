"""Merge phase 2 slice 2 with main heads

Revision ID: 51a885014fac
Revises: 74e4e6cb4d6c, d8e9f0a1b2c3
Create Date: 2026-05-08 06:47:19.865108

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "51a885014fac"
down_revision: Union[str, Sequence[str], None] = ("74e4e6cb4d6c", "d8e9f0a1b2c3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

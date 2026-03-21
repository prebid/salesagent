"""Merge v1.5.0 migration heads

Revision ID: 2e04733a751f
Revises: 5cf35536db6f, aa005b733aed
Create Date: 2026-03-17 08:57:29.928665

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2e04733a751f"
down_revision: Union[str, Sequence[str], None] = ("5cf35536db6f", "aa005b733aed")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

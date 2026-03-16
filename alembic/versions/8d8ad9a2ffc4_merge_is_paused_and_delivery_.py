"""merge is_paused and delivery_measurement_not_null branches

Revision ID: 8d8ad9a2ffc4
Revises: 5cf35536db6f, aa005b733aed
Create Date: 2026-03-16 11:20:04.106777

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8d8ad9a2ffc4"
down_revision: Union[str, Sequence[str], None] = ("5cf35536db6f", "aa005b733aed")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

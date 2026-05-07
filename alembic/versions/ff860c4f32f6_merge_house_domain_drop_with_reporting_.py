"""merge house_domain drop with reporting_capabilities

Revision ID: ff860c4f32f6
Revises: 102ce62707b9, o6p7q8r9s0t1
Create Date: 2026-05-07 13:30:03.645801

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ff860c4f32f6"
down_revision: Union[str, Sequence[str], None] = ("102ce62707b9", "o6p7q8r9s0t1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

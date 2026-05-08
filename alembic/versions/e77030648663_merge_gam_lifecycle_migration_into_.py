"""merge gam-lifecycle migration into branch

Revision ID: e77030648663
Revises: b0545900b6b1, c46693e8c3dc
Create Date: 2026-05-06 08:11:56.630820

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e77030648663"
down_revision: Union[str, Sequence[str], None] = ("b0545900b6b1", "c46693e8c3dc")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge signing slice 2 with pre-start-buys report

Revision ID: e9f8a7ce4e46
Revises: dde622632b0e, bcd40819d318
Create Date: 2026-05-07 07:11:02.612176

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9f8a7ce4e46"
down_revision: Union[str, Sequence[str], None] = ("dde622632b0e", "bcd40819d318")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

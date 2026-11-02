"""merge main media_buy status normalization with spec-gaps creative status repair

Revision ID: 19dd84c79382
Revises: 9b2d4f6c1a37, c7a2f10b93de
Create Date: 2026-08-28 20:47:43.045838

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "19dd84c79382"
down_revision: Union[str, Sequence[str], None] = ("9b2d4f6c1a37", "c7a2f10b93de")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

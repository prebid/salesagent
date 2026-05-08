"""merge signing + harness migration heads

Revision ID: c46693e8c3dc
Revises: j0k1l2m3n4o5, m3n4o5p6q7r8
Create Date: 2026-05-06 06:41:52.413941

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c46693e8c3dc"
down_revision: Union[str, Sequence[str], None] = ("j0k1l2m3n4o5", "m3n4o5p6q7r8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

"""merge heads

Revision ID: ed248e24dfc0
Revises: efd3fb6e1884, rename_formats_to_format_ids
Create Date: 2025-11-15 13:52:38.668922

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ed248e24dfc0"
down_revision: Union[str, Sequence[str], None] = ("efd3fb6e1884", "rename_formats_to_format_ids")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

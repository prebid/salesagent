"""Merge pricing migration with format discovery

Revision ID: 39c5ac5558c7
Revises: 182e1c7dcd01, 2a2aaed3b50d
Create Date: 2025-10-13 16:08:07.935531

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "39c5ac5558c7"
down_revision: str | Sequence[str] | None = ("182e1c7dcd01", "2a2aaed3b50d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

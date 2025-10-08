"""merge currency limits and naming templates branches

Revision ID: merge_heads_001
Revises: 9b54e0acc0e7, ebcb8dda247a
Create Date: 2025-10-08 07:50:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge_heads_001"
down_revision: str | Sequence[str] | None = ("9b54e0acc0e7", "ebcb8dda247a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge two migration branches - no schema changes needed."""
    pass


def downgrade() -> None:
    """Merge migration - no schema changes to revert."""
    pass

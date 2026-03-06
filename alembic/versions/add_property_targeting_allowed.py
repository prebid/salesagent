"""Merge heads: adcp 3.6.0 fields + creative composite PK

Merge point for the two concurrent migration branches.

Revision ID: b8e5f3a2c7d9
Revises: a7d4e2f1b3c5, 1a88e4967119
Create Date: 2026-02-27
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b8e5f3a2c7d9"
down_revision: Union[str, tuple[str, ...], None] = ("a7d4e2f1b3c5", "1a88e4967119")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge point — no DDL."""
    pass


def downgrade() -> None:
    """Merge point — no DDL."""
    pass

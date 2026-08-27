"""Merge signing and spec-gaps migration heads

Revision ID: 6371c0f43f54
Revises: b7c1d9f4a2e3, c7a2f10b93de
Create Date: 2026-08-06 10:01:01.699018

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6371c0f43f54"
down_revision: str | Sequence[str] | None = ("b7c1d9f4a2e3", "c7a2f10b93de")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

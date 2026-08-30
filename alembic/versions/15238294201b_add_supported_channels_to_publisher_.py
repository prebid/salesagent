"""add supported_channels to publisher_partners

Revision ID: 15238294201b
Revises: 823974a5553e
Create Date: 2026-08-04 07:35:11.605408

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "15238294201b"
down_revision: str | Sequence[str] | None = "823974a5553e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add supported_channels JSON column to publisher_partners."""
    op.add_column(
        "publisher_partners",
        sa.Column(
            "supported_channels",
            JSONType(),
            nullable=True,
            comment="Advertising channels supported by this publisher partnership",
        ),
    )


def downgrade() -> None:
    """Remove supported_channels from publisher_partners."""
    op.drop_column("publisher_partners", "supported_channels")

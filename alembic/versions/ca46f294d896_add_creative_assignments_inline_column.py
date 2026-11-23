"""add creative_assignments inline column

Adds ``creative_assignments.inline`` (Boolean, NOT NULL, default FALSE): whether the
assignment came in through a package's inline ``creatives`` on ``create_media_buy`` or
``update_media_buy``. update_media_buy.mdx's package field table gives the two package
arrays separate scopes: ``creative_assignments`` "Replace assigned library creatives" and
``creatives`` "Replace assigned inline creative bodies for this package". The inline
replacement reads this column to touch only the assignments it owns.

Default FALSE: an existing row's origin was never recorded, and a row read as a library
assignment is one the inline replacement leaves in place rather than deletes.

Revision ID: ca46f294d896
Revises: ddb20e636cee
Create Date: 2026-10-29 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ca46f294d896"
down_revision: str | Sequence[str] | None = "ddb20e636cee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add creative_assignments.inline (Boolean, NOT NULL, default FALSE)."""
    op.add_column(
        "creative_assignments",
        sa.Column("inline", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Drop creative_assignments.inline."""
    op.drop_column("creative_assignments", "inline")

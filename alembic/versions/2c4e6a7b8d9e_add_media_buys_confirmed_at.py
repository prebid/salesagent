"""Persist the seller confirmation instant for media buys.

The value is write-once: approved_at may change or be cleared by later
workflow transitions, while AdCP's confirmed_at is the instant the seller
committed to the buy.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2c4e6a7b8d9e"
down_revision: str | Sequence[str] | None = "1497aa06013c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_buys", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE media_buys
            SET confirmed_at = COALESCE(approved_at, created_at)
            WHERE status NOT IN ('draft', 'pending', 'pending_approval', 'rejected', 'failed')
            """
        )
    )


def downgrade() -> None:
    op.drop_column("media_buys", "confirmed_at")

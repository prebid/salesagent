"""add_media_buy_delivery_snapshot

Adds delivery snapshot columns to media_buys so the publisher dashboard can
show pacing without calling the adapter on every render. The snapshot is
written opportunistically after each get_media_buy_delivery call (Option A
in https://github.com/bokelley/salesagent/issues/22's sibling discussion).

A future scheduled poll job (Option B, follow-up issue) will keep the
snapshot fresh independent of buyer polling cadence.

Revision ID: m3n4o5p6q7r8
Revises: h7i8j9k0l1m2
Create Date: 2026-05-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: str | Sequence[str] | None = "h7i8j9k0l1m2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_buys",
        sa.Column("delivered_impressions", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "media_buys",
        sa.Column("delivered_amount", sa.DECIMAL(15, 2), nullable=True),
    )
    op.add_column(
        "media_buys",
        sa.Column("delivery_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_buys", "delivery_synced_at")
    op.drop_column("media_buys", "delivered_amount")
    op.drop_column("media_buys", "delivered_impressions")

"""Convert channel (String) to channels (JSONB array) on products table.

Revision ID: channel_to_channels
Revises: add_gam_network_currency
Create Date: 2025-12-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "channel_to_channels"
down_revision: str | None = "add_gam_network_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert channel column to channels (JSONB array)."""
    # Add new channels column
    op.add_column(
        "products",
        sa.Column(
            "channels",
            JSONB,
            nullable=True,
            comment="Advertising channels (e.g., ['display', 'video', 'native'])",
        ),
    )

    # Migrate existing data: convert single channel string to array
    op.execute(
        """
        UPDATE products
        SET channels = jsonb_build_array(channel)
        WHERE channel IS NOT NULL AND channel != ''
        """
    )

    # Drop old channel column
    op.drop_column("products", "channel")


def downgrade() -> None:
    """Convert channels back to channel (first element only)."""
    # Add back the old channel column
    op.add_column(
        "products",
        sa.Column(
            "channel",
            sa.String(50),
            nullable=True,
            comment="Advertising channel (e.g., display, video, audio, native)",
        ),
    )

    # Migrate data: take first channel from array
    op.execute(
        """
        UPDATE products
        SET channel = channels->>0
        WHERE channels IS NOT NULL AND jsonb_array_length(channels) > 0
        """
    )

    # Drop new channels column
    op.drop_column("products", "channels")

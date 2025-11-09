"""add_product_detail_fields

Revision ID: e40d739b89c6
Revises: d169f2e66919
Create Date: 2025-11-07 08:01:39.496342

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e40d739b89c6"
down_revision: str | Sequence[str] | None = "d169f2e66919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add delivery_measurement column (REQUIRED per AdCP spec)
    op.add_column("products", sa.Column("delivery_measurement", sa.dialects.postgresql.JSONB(), nullable=True))

    # Add optional product card columns
    op.add_column("products", sa.Column("product_card", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("product_card_detailed", sa.dialects.postgresql.JSONB(), nullable=True))

    # Add optional placements column (array of placement objects)
    op.add_column("products", sa.Column("placements", sa.dialects.postgresql.JSONB(), nullable=True))

    # Add optional reporting_capabilities column
    op.add_column("products", sa.Column("reporting_capabilities", sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove columns in reverse order
    op.drop_column("products", "reporting_capabilities")
    op.drop_column("products", "placements")
    op.drop_column("products", "product_card_detailed")
    op.drop_column("products", "product_card")
    op.drop_column("products", "delivery_measurement")

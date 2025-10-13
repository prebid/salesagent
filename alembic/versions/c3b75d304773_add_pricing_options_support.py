"""add_pricing_options_support

Add support for multiple pricing models per product (AdCP PR #88).

Creates pricing_options table and updates products table to support
the new pricing model structure with CPM, CPCV, CPP, CPC, CPV, and flat_rate options.

Revision ID: c3b75d304773
Revises: 9309ac2fa74f
Create Date: 2025-10-12 20:30:18.139519

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3b75d304773"
down_revision: str | Sequence[str] | None = "9309ac2fa74f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema for pricing options support (AdCP PR #88)."""
    # Create pricing_options table
    op.create_table(
        "pricing_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("pricing_model", sa.String(length=20), nullable=False),  # cpm, cpcv, cpp, cpc, cpv, flat_rate
        sa.Column("rate", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),  # ISO 4217 code
        sa.Column("is_fixed", sa.Boolean(), nullable=False),
        sa.Column("price_guidance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("min_spend_per_package", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"], ["products.tenant_id", "products.product_id"], ondelete="CASCADE"
        ),
    )
    op.create_index("idx_pricing_options_product", "pricing_options", ["tenant_id", "product_id"])

    # Make old pricing fields nullable for backward compatibility
    op.alter_column("products", "is_fixed_price", nullable=True, existing_type=sa.Boolean())

    # Add currency column to products table (for legacy pricing)
    op.add_column("products", sa.Column("currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    """Downgrade schema - remove pricing options support."""
    # Drop pricing_options table
    op.drop_index("idx_pricing_options_product", table_name="pricing_options")
    op.drop_table("pricing_options")

    # Restore old pricing fields as required
    op.alter_column("products", "is_fixed_price", nullable=False, existing_type=sa.Boolean())

    # Remove currency column from products
    op.drop_column("products", "currency")

"""add_dynamic_product_fields

Revision ID: c608bc822a13
Revises: a79158b4a1ae
Create Date: 2025-11-07 18:06:33.079580

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c608bc822a13"
down_revision: str | Sequence[str] | None = "a79158b4a1ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add dynamic product fields
    op.add_column("products", sa.Column("is_dynamic", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("products", sa.Column("is_dynamic_variant", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("products", sa.Column("parent_product_id", sa.String(100), nullable=True))
    op.add_column("products", sa.Column("signals_agent_ids", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("max_signals", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("products", sa.Column("activation_key", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("signal_metadata", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("products", sa.Column("last_synced_at", sa.DateTime(), nullable=True))
    op.add_column("products", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("products", sa.Column("variant_ttl_days", sa.Integer(), nullable=True))

    # Add foreign key constraint for parent_product_id
    op.create_foreign_key(
        "fk_products_parent_product",
        "products",
        "products",
        ["tenant_id", "parent_product_id"],
        ["tenant_id", "product_id"],
        ondelete="CASCADE",
    )

    # Add indexes for performance
    op.create_index(
        "idx_products_dynamic", "products", ["tenant_id", "is_dynamic"], postgresql_where=sa.text("is_dynamic = true")
    )
    op.create_index(
        "idx_products_variants",
        "products",
        ["tenant_id", "parent_product_id"],
        postgresql_where=sa.text("parent_product_id IS NOT NULL"),
    )
    op.create_index(
        "idx_products_archived", "products", ["archived_at"], postgresql_where=sa.text("archived_at IS NOT NULL")
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop indexes
    op.drop_index("idx_products_archived", "products")
    op.drop_index("idx_products_variants", "products")
    op.drop_index("idx_products_dynamic", "products")

    # Drop foreign key
    op.drop_constraint("fk_products_parent_product", "products", type_="foreignkey")

    # Drop columns
    op.drop_column("products", "variant_ttl_days")
    op.drop_column("products", "archived_at")
    op.drop_column("products", "last_synced_at")
    op.drop_column("products", "signal_metadata")
    op.drop_column("products", "activation_key")
    op.drop_column("products", "max_signals")
    op.drop_column("products", "signals_agent_ids")
    op.drop_column("products", "parent_product_id")
    op.drop_column("products", "is_dynamic_variant")
    op.drop_column("products", "is_dynamic")

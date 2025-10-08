"""Add currency_limits table for multi-currency support

Revision ID: 226b47580589
Revises: e2d9b45ea2bc
Create Date: 2025-10-08 04:42:07.795271

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "226b47580589"
down_revision: str | Sequence[str] | None = "e2d9b45ea2bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create currency_limits table
    op.create_table(
        "currency_limits",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("min_product_spend", sa.DECIMAL(precision=15, scale=2), nullable=True),
        sa.Column("max_daily_spend", sa.DECIMAL(precision=15, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "currency_code"),
        sa.UniqueConstraint("tenant_id", "currency_code", name="uq_currency_limit"),
    )
    op.create_index("idx_currency_limits_tenant", "currency_limits", ["tenant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop currency_limits table
    op.drop_index("idx_currency_limits_tenant", table_name="currency_limits")
    op.drop_table("currency_limits")

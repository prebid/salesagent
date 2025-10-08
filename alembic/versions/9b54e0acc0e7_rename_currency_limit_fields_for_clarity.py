"""Rename currency limit fields for clarity

Revision ID: 9b54e0acc0e7
Revises: 226b47580589
Create Date: 2025-10-08 06:07:05.189168

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b54e0acc0e7"
down_revision: str | Sequence[str] | None = "226b47580589"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rename columns for clarity (per-package, not per-product)
    op.alter_column("currency_limits", "min_product_spend", new_column_name="min_package_budget")
    op.alter_column("currency_limits", "max_daily_spend", new_column_name="max_daily_package_spend")


def downgrade() -> None:
    """Downgrade schema."""
    # Revert column names
    op.alter_column("currency_limits", "min_package_budget", new_column_name="min_product_spend")
    op.alter_column("currency_limits", "max_daily_package_spend", new_column_name="max_daily_spend")

"""add_naming_templates_to_adapter_config

Revision ID: ede76bc258af
Revises: a7acdcb7b3d3
Create Date: 2025-10-06 20:04:19.012817

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ede76bc258af"
down_revision: str | Sequence[str] | None = "a7acdcb7b3d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add naming template fields to adapter_config table."""
    # Add GAM naming template fields
    op.add_column("adapter_config", sa.Column("gam_order_name_template", sa.String(500), nullable=True))
    op.add_column("adapter_config", sa.Column("gam_line_item_name_template", sa.String(500), nullable=True))

    # Set sensible defaults for existing rows
    # Order name: "{campaign_name|promoted_offering} - {date_range}"
    # Line item name: "{order_name} - {product_name}" (includes campaign context for better reporting)
    op.execute(
        """
        UPDATE adapter_config
        SET gam_order_name_template = '{campaign_name|promoted_offering} - {date_range}',
            gam_line_item_name_template = '{order_name} - {product_name}'
        WHERE adapter_type = 'google_ad_manager'
    """
    )


def downgrade() -> None:
    """Remove naming template fields."""
    op.drop_column("adapter_config", "gam_line_item_name_template")
    op.drop_column("adapter_config", "gam_order_name_template")

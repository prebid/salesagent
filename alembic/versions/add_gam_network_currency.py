"""Add gam_network_currency, gam_secondary_currencies, and gam_network_timezone columns to adapter_config.

Revision ID: add_gam_network_currency
Revises: 4b11f64bbebe
Create Date: 2025-12-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "add_gam_network_currency"
down_revision: str | None = "4b11f64bbebe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add gam_network_currency, gam_secondary_currencies, and gam_network_timezone columns to adapter_config table."""
    op.add_column(
        "adapter_config",
        sa.Column(
            "gam_network_currency",
            sa.String(3),
            nullable=True,
            comment="Primary currency code from GAM network (ISO 4217). Auto-populated on connection test.",
        ),
    )
    op.add_column(
        "adapter_config",
        sa.Column(
            "gam_secondary_currencies",
            JSONB,
            nullable=True,
            comment="Secondary currency codes enabled in GAM network (ISO 4217 array). Auto-populated on connection test.",
        ),
    )
    op.add_column(
        "adapter_config",
        sa.Column(
            "gam_network_timezone",
            sa.String(100),
            nullable=True,
            comment="Timezone of the GAM network (e.g., 'America/New_York'). Auto-populated on connection test.",
        ),
    )


def downgrade() -> None:
    """Remove gam_network_currency, gam_secondary_currencies, and gam_network_timezone columns from adapter_config table."""
    op.drop_column("adapter_config", "gam_network_timezone")
    op.drop_column("adapter_config", "gam_secondary_currencies")
    op.drop_column("adapter_config", "gam_network_currency")

"""add_custom_targeting_keys_to_adapter_config

Revision ID: efd3fb6e1884
Revises: 240284b2f169
Create Date: 2025-11-15 11:37:38.917187

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "efd3fb6e1884"
down_revision: Union[str, Sequence[str], None] = "240284b2f169"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add custom_targeting_keys JSONB field to adapter_config table.

    This field stores the mapping of GAM custom targeting key names to their numeric IDs:
    {
        "axe_include_segment": "123456789",
        "axe_exclude_segment": "987654321",
        "custom_key_1": "111111111"
    }

    This enables the GAM adapter to resolve key names to IDs at runtime without
    making additional API calls to GAM for every media buy creation.
    """
    op.add_column(
        "adapter_config",
        sa.Column("custom_targeting_keys", sa.dialects.postgresql.JSONB, nullable=True, server_default="{}"),
    )


def downgrade() -> None:
    """Remove custom_targeting_keys field from adapter_config table."""
    op.drop_column("adapter_config", "custom_targeting_keys")

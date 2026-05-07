"""drop kevel adapter columns

Revision ID: e9a1c2d3f4b5
Revises: bcd40819d318
Create Date: 2026-05-07 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9a1c2d3f4b5"
down_revision: Union[str, Sequence[str], None] = "ee6fe59f5407"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop kevel adapter — Kevel was unused and untested.

    Sanitizes any tenants stuck on adapter_type='kevel' (route them to mock,
    matching the get_adapter() fallback for unknown adapter types) and strips
    'kevel' keys from principal.platform_mappings before dropping the columns.
    """
    bind = op.get_bind()

    # Reroute any adapter_config rows still pointing at kevel to mock.
    bind.execute(sa.text("UPDATE adapter_config SET adapter_type = 'mock' WHERE adapter_type = 'kevel'"))

    # Reroute any tenants whose ad_server is still kevel.
    bind.execute(sa.text("UPDATE tenants SET ad_server = 'mock' WHERE ad_server = 'kevel'"))

    # Strip 'kevel' key from principal.platform_mappings JSON to avoid a future
    # PlatformMappingModel validation error (the validator only allows
    # google_ad_manager and mock now). Use jsonb_strip + key removal.
    bind.execute(
        sa.text(
            "UPDATE principals "
            "SET platform_mappings = platform_mappings::jsonb - 'kevel' "
            "WHERE platform_mappings::jsonb ? 'kevel'"
        )
    )

    op.drop_column("adapter_config", "kevel_network_id")
    op.drop_column("adapter_config", "kevel_api_key")
    op.drop_column("adapter_config", "kevel_manual_approval_required")


def downgrade() -> None:
    """Restore kevel_* columns on adapter_config (data is not recoverable)."""
    op.add_column(
        "adapter_config",
        sa.Column("kevel_network_id", sa.String(50), nullable=True),
    )
    op.add_column(
        "adapter_config",
        sa.Column("kevel_api_key", sa.String(100), nullable=True),
    )
    op.add_column(
        "adapter_config",
        sa.Column(
            "kevel_manual_approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

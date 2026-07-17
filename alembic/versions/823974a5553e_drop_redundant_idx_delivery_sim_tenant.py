"""drop redundant idx_delivery_sim_tenant

``delivery_simulation_configs`` (a test-seeding table, ADCP_TESTING only) has
the composite primary key ``(tenant_id, media_buy_id)``; the PK index already
serves tenant_id-prefix scans, so the standalone ``idx_delivery_sim_tenant``
on ``[tenant_id]`` added by 64f0fff7d954 was pure write amplification
(PR #1430 review). ``media_buy_id`` deliberately carries no FK — the table
seeds simulations for buys that may not exist yet.

Revision ID: 823974a5553e
Revises: 64f0fff7d954
Create Date: 2026-07-10 15:09:19.976959

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "823974a5553e"
down_revision: str | Sequence[str] | None = "64f0fff7d954"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the index made redundant by the composite PK's leading column."""
    op.drop_index("idx_delivery_sim_tenant", table_name="delivery_simulation_configs")


def downgrade() -> None:
    """Recreate the redundant index exactly as 64f0fff7d954 built it."""
    op.create_index("idx_delivery_sim_tenant", "delivery_simulation_configs", ["tenant_id"])

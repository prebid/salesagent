"""Add delivery_simulation_configs table

Server-side delivery seeding for the Mock adapter (#1418). Holds a
per-(tenant, media_buy) snapshot of the wire payload the Mock adapter returns
from get_media_buy_delivery, so the live server can return deterministic
delivery numbers for e2e scenarios instead of an in-memory MagicMock.

Revision ID: 64f0fff7d954
Revises: 597485e1799a
Create Date: 2026-06-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "64f0fff7d954"
down_revision: str | Sequence[str] | None = "597485e1799a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "delivery_simulation_configs",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("media_buy_id", sa.String(length=100), nullable=False),
        sa.Column("response_payload", JSONType(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "media_buy_id"),
    )
    op.create_index("idx_delivery_sim_tenant", "delivery_simulation_configs", ["tenant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_delivery_sim_tenant", table_name="delivery_simulation_configs")
    op.drop_table("delivery_simulation_configs")

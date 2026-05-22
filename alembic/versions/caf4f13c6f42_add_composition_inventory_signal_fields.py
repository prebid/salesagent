"""add composition inventory and signal fields

Revision ID: caf4f13c6f42
Revises: b4e2bffdd4f8
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

revision: str = "caf4f13c6f42"
down_revision: str | Sequence[str] | None = "b4e2bffdd4f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inventory_profiles", sa.Column("constraints", JSONType, nullable=True))
    op.add_column("inventory_profiles", sa.Column("etag", sa.String(length=64), nullable=True))

    op.create_table(
        "tenant_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("signal_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("categories", JSONType, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tags", JSONType, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("range_min", sa.DECIMAL(20, 6), nullable=True),
        sa.Column("range_max", sa.DECIMAL(20, 6), nullable=True),
        sa.Column("adapter_config", JSONType, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("data_provider", sa.String(length=200), nullable=True),
        sa.Column("targeting_dimension", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "signal_id", name="uq_tenant_signal"),
    )
    op.create_index("idx_tenant_signals_tenant", "tenant_signals", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("idx_tenant_signals_tenant", table_name="tenant_signals")
    op.drop_table("tenant_signals")
    op.drop_column("inventory_profiles", "etag")
    op.drop_column("inventory_profiles", "constraints")

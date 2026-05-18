"""add inventory_review_state table

Job 1 (Discovery) coverage analytics for #485. Tracks the operator's
review decision for each synced inventory entity — adapter-agnostic.

Status state machine:
- ``pending`` — synced from the adapter, not yet decided. Default.
- ``in_bundle`` — referenced by ≥1 ``InventoryProfile``. Maintained at
  bundle save time by the inventory_profiles blueprint.
- ``explicitly_skipped`` — operator decided not to sell. Operator action.

Keyed by ``(tenant_id, adapter, entity_type, external_id)``. The
``entity_type`` slot leaves room for #486's signal candidates to reuse
the same table with ``entity_type='signal_candidate'`` rather than
duplicate the schema.

Revision ID: y7z8a9b0c1d2
Revises: x6y7z8a9b0c1
Create Date: 2026-05-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y7z8a9b0c1d2"
down_revision: str | Sequence[str] | None = "x6y7z8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_review_state",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(50),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # "gam" | "freewheel" | "springserve" — adapter that owns the entity.
        sa.Column("adapter", sa.String(50), nullable=False),
        # "ad_unit" | "placement" today. "signal_candidate" reserved for #486.
        sa.Column("entity_type", sa.String(50), nullable=False),
        # Adapter-native id (e.g. GAM ad_unit_id). Strings — adapters vary.
        sa.Column("external_id", sa.String(200), nullable=False),
        # "pending" | "in_bundle" | "explicitly_skipped"
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "adapter",
            "entity_type",
            "external_id",
            name="uq_inventory_review_state",
        ),
    )
    # Coverage dashboard does GROUP BY (tenant_id, entity_type, status).
    op.create_index(
        "idx_inventory_review_state_tenant_type_status",
        "inventory_review_state",
        ["tenant_id", "entity_type", "status"],
    )
    # Save-time hook upserts rows for a specific (tenant, adapter, entity_type).
    op.create_index(
        "idx_inventory_review_state_tenant_adapter_type",
        "inventory_review_state",
        ["tenant_id", "adapter", "entity_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_inventory_review_state_tenant_adapter_type", table_name="inventory_review_state")
    op.drop_index("idx_inventory_review_state_tenant_type_status", table_name="inventory_review_state")
    op.drop_table("inventory_review_state")

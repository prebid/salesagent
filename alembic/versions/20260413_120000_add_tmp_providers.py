"""Add tmp_providers table for Trusted Match Protocol provider registrations

Revision ID: 20260413120000
Revises: aa2e905fe772
Create Date: 2026-04-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260413120000"
down_revision: str | Sequence[str] | None = "018bd7bdeed8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create tmp_providers table
    op.create_table(
        "tmp_providers",
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("context_match", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("identity_match", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider_id"),
    )

    # Create indexes
    op.create_index("idx_tmp_providers_tenant", "tmp_providers", ["tenant_id"])
    op.create_index("idx_tmp_providers_active", "tmp_providers", ["is_active"])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop indexes
    op.drop_index("idx_tmp_providers_active", table_name="tmp_providers")
    op.drop_index("idx_tmp_providers_tenant", table_name="tmp_providers")

    # Drop table
    op.drop_table("tmp_providers")

"""Add tmp_providers table for Trusted Match Protocol provider registrations

Revision ID: 20260413120000
Revises: 018bd7bdeed8
Create Date: 2026-04-13 12:00:00.000000

Schema aligned with provider-registration.json (AdCP spec PR #2210):
  - status string (active/inactive/draining) instead of is_active boolean
  - countries (JSONB, conditional on identity_match)
  - uid_types (JSONB, conditional on identity_match)
  - properties (JSONB, optional property RIDs)
  - priority (integer, default 0)
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
    """Create tmp_providers table aligned with provider-registration.json schema."""
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
        sa.Column("countries", sa.JSON(), nullable=True),
        sa.Column("uid_types", sa.JSON(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider_id"),
    )

    # Create indexes
    op.create_index("idx_tmp_providers_tenant", "tmp_providers", ["tenant_id"])
    op.create_index("idx_tmp_providers_status", "tmp_providers", ["status"])


def downgrade() -> None:
    """Drop tmp_providers table."""
    op.drop_index("idx_tmp_providers_status", table_name="tmp_providers")
    op.drop_index("idx_tmp_providers_tenant", table_name="tmp_providers")
    op.drop_table("tmp_providers")

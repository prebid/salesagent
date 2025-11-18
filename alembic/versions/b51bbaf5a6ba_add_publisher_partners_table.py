"""add_publisher_partners_table

Revision ID: b51bbaf5a6ba
Revises: 3d2f7ff99896
Create Date: 2025-11-16 19:43:15.311945

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b51bbaf5a6ba"
down_revision: Union[str, Sequence[str], None] = "3d2f7ff99896"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "publisher_partners",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("publisher_domain", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
            comment="pending, success, error",
        ),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sync_status IN ('pending', 'success', 'error')", name="ck_sync_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "publisher_domain", name="uq_tenant_publisher"),
    )
    op.create_index("idx_publisher_partners_domain", "publisher_partners", ["publisher_domain"])
    op.create_index("idx_publisher_partners_tenant", "publisher_partners", ["tenant_id"])
    op.create_index("idx_publisher_partners_verified", "publisher_partners", ["is_verified"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_publisher_partners_verified", table_name="publisher_partners")
    op.drop_index("idx_publisher_partners_tenant", table_name="publisher_partners")
    op.drop_index("idx_publisher_partners_domain", table_name="publisher_partners")
    op.drop_table("publisher_partners")

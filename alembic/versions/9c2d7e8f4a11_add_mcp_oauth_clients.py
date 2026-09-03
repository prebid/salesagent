"""add mcp oauth clients

Revision ID: 9c2d7e8f4a11
Revises: 823974a5553e
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9c2d7e8f4a11"
down_revision: str | Sequence[str] | None = "823974a5553e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create OAuth client credentials for MCP client-credentials auth."""
    op.create_table(
        "oauth_clients",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.String(length=120), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column("client_secret_hash", sa.String(length=500), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "client_id"),
        sa.UniqueConstraint("client_id", name="uq_oauth_clients_client_id"),
    )
    op.create_index("idx_oauth_clients_client_id", "oauth_clients", ["client_id"])
    op.create_index("idx_oauth_clients_tenant_principal", "oauth_clients", ["tenant_id", "principal_id"])


def downgrade() -> None:
    """Drop OAuth client credentials for MCP client-credentials auth."""
    op.drop_index("idx_oauth_clients_tenant_principal", table_name="oauth_clients")
    op.drop_index("idx_oauth_clients_client_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
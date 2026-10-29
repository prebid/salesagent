"""add mcp oauth authorization codes

Revision ID: b7c9e4a2d6f1
Revises: 9c2d7e8f4a11
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7c9e4a2d6f1"
down_revision: str | Sequence[str] | None = "9c2d7e8f4a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add Authorization Code + PKCE storage for MCP OAuth."""
    op.add_column(
        "oauth_clients",
        sa.Column(
            "redirect_uris",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("oauth_clients", "redirect_uris", server_default=None)

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.String(length=120), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column("redirect_uri", sa.String(length=1000), nullable=False),
        sa.Column("code_challenge", sa.String(length=256), nullable=False),
        sa.Column("code_challenge_method", sa.String(length=20), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resource", sa.String(length=1000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["oauth_clients.tenant_id", "oauth_clients.client_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("code_hash"),
    )
    op.create_index(
        "idx_oauth_authorization_codes_client",
        "oauth_authorization_codes",
        ["tenant_id", "client_id"],
    )
    op.create_index(
        "idx_oauth_authorization_codes_expires_at",
        "oauth_authorization_codes",
        ["expires_at"],
    )


def downgrade() -> None:
    """Remove Authorization Code + PKCE storage for MCP OAuth."""
    op.drop_index("idx_oauth_authorization_codes_expires_at", table_name="oauth_authorization_codes")
    op.drop_index("idx_oauth_authorization_codes_client", table_name="oauth_authorization_codes")
    op.drop_table("oauth_authorization_codes")
    op.drop_column("oauth_clients", "redirect_uris")
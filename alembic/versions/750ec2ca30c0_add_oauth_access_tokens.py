"""add oauth access tokens

Stored opaque MCP OAuth access tokens, replacing the signed JWT.

An access token becomes a high-entropy random string the token endpoint returns
once; this table keeps ``sha256`` of it plus the facts that used to travel as JWT
claims — the client and principal the grant named, the scopes it carried, and the
RFC 8707 ``resource`` the token is bound to. The verifier already re-read every
one of those claims off ``oauth_clients`` and ``principals`` on each request, so
the claims were a cache of this table, not an authority. Verification becomes one
tenant-scoped hash lookup, and ``revoked_at`` makes revocation exact instead of
needing a deny-list to shorten a signature nothing can withdraw.

Deliberately the same shape as ``oauth_authorization_codes``
(``b7c9e4a2d6f1_add_mcp_oauth_authorization_codes``) one step later in the flow:
a 128-char hash primary key, the same two composite foreign keys so a token dies
with the client or principal it names, and the same two indexes — ``(tenant_id,
client_id)`` for the per-client reads and ``expires_at`` for the pruning job.

Revision ID: 750ec2ca30c0
Revises: d35284fd33cf
Create Date: 2026-10-28 13:41:30.684864

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "750ec2ca30c0"
down_revision: str | Sequence[str] | None = "d35284fd33cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add stored opaque access tokens for MCP OAuth."""
    op.create_table(
        "oauth_access_tokens",
        # The hash IS the identity: the plaintext is returned once and stored nowhere.
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.String(length=120), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column("scopes", JSONType, nullable=False),
        # The RFC 8707 resource binding the verifier compares against the request's
        # target — what the JWT's `aud` claim carried.
        sa.Column("resource", sa.String(length=1000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # NULL means live; a set value ends the token at the next request.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # CASCADE both ways: a token must not outlive the client or the principal it
        # names, or it is a bearer for an identity that no longer exists.
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
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "idx_oauth_access_tokens_client",
        "oauth_access_tokens",
        ["tenant_id", "client_id"],
    )
    op.create_index(
        "idx_oauth_access_tokens_expires_at",
        "oauth_access_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    """Remove stored opaque access tokens for MCP OAuth.

    Dropping the table invalidates every issued token, which is the honest
    consequence of reverting: the plaintexts were never stored, so there is
    nothing to preserve and nothing a backup table could restore. Clients
    re-authorize.
    """
    op.drop_index("idx_oauth_access_tokens_expires_at", table_name="oauth_access_tokens")
    op.drop_index("idx_oauth_access_tokens_client", table_name="oauth_access_tokens")
    op.drop_table("oauth_access_tokens")

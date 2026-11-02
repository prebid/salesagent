"""hash principal tokens; drop the tenant admin token

Revision ID: e4b7c2a91f05
Revises: d1a7c4b90e33
Create Date: 2026-09-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7c2a91f05"
down_revision: str | Sequence[str] | None = "d1a7c4b90e33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store sha256(token) and a display prefix instead of the token; drop tenants.admin_token.

    ``principals.access_token`` held the plaintext buyer credential, indexed and matched by
    equality, and the admin UI displayed it back. The resolver now hashes what a request
    presents and compares hashes (``src/core/credentials.py``), so the table needs the hash
    and nothing else. Existing rows are hashed IN PLACE from their plaintext, so every token
    already issued keeps working; the prefix is the first twelve characters, the same head
    the UI used to truncate to.

    ``tenants.admin_token`` was accepted as a Bearer credential by a resolver fallback that
    is deleted: a tenant is not a caller, and a per-tenant secret that authenticates as any
    principal is a credential without an owner. Every tenant already carries a real
    principal for its operator (the signup flow creates one), which is the shape that
    survives.
    """
    op.add_column("principals", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.add_column("principals", sa.Column("token_prefix", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE principals SET "
        "token_hash = encode(sha256(convert_to(access_token, 'UTF8')), 'hex'), "
        "token_prefix = left(access_token, 12)"
    )
    op.alter_column("principals", "token_hash", nullable=False)
    op.alter_column("principals", "token_prefix", nullable=False)
    op.create_unique_constraint("uq_principals_token_hash", "principals", ["token_hash"])
    op.create_index("idx_principals_token_hash", "principals", ["token_hash"])
    # No migration in the chain creates this index; a database built by the ORM's
    # create_all carries it and a database built by the chain does not.
    op.execute("DROP INDEX IF EXISTS idx_principals_token")
    op.drop_column("principals", "access_token")

    op.drop_column("tenants", "admin_token")


def downgrade() -> None:
    """Restore the columns. The plaintext tokens cannot be recovered from their hashes.

    ``access_token`` comes back filled with ``rehash:`` plus the hash, which is unique, is
    not a valid credential, and marks every row as needing a token re-issued. The tenant
    column comes back empty; nothing reads it.
    """
    op.add_column("tenants", sa.Column("admin_token", sa.String(length=100), nullable=True))

    op.add_column("principals", sa.Column("access_token", sa.String(length=255), nullable=True))
    op.execute("UPDATE principals SET access_token = 'rehash:' || token_hash")
    op.alter_column("principals", "access_token", nullable=False)
    op.create_unique_constraint("principals_access_token_key", "principals", ["access_token"])
    op.create_index("idx_principals_token", "principals", ["access_token"])
    op.drop_index("idx_principals_token_hash", table_name="principals")
    op.drop_constraint("uq_principals_token_hash", "principals", type_="unique")
    op.drop_column("principals", "token_prefix")
    op.drop_column("principals", "token_hash")

"""add_signing_keys_table

Per-tenant RFC 9421 signing key material (#1291 A2, salesagent-z6nr.8).

One row binds a unique ``kid`` to the public JWK we publish and to a
scheme-prefixed REFERENCE (``db:<kid>`` / ``env:NAME`` / ``file:/abs/path``) the
process resolves for the private half.

For the ``db:`` scheme — the only one this agent MINTS (salesagent-7x8t) —
``private_key_pem_encrypted`` holds that private half as the PKCS#8
``BEGIN ENCRYPTED PRIVATE KEY`` PEM the SDK returned, encrypted under the
deployment KEK. The application writes key material to no filesystem, so the row
is the only place it can live; provisioning refuses ``db:`` when no KEK is
configured, which is what keeps this column from ever holding plaintext. The
column is folded into THIS migration rather than added by a second one: A2 is
unmerged and no environment has applied it, so there is no deployed schema to
preserve and a two-step migration would exist only to record a decision git
already records.

The two CHECK constraints are DERIVED from ``src.core.signing.algorithms`` and
rendered by the same helper the ORM model uses, so the migration DDL and the ORM
DDL cannot disagree — migration ``e381618812f1`` exists because a hand-written
CHECK froze while a spec enum grew (#1521).

Revision ID: e7a2c40b91d5
Revises: d3f7a5c81e46
Create Date: 2026-07-27 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType
from src.core.signing.algorithms import MINTABLE_PURPOSES, SIGNING_ALG_VALUES, sql_value_list

# revision identifiers, used by Alembic.
revision: str = "e7a2c40b91d5"
down_revision: str | Sequence[str] | None = "d3f7a5c81e46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "signing_keys",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("kid", sa.String(255), nullable=False),
        sa.Column("alg", sa.String(50), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("public_jwk", JSONType, nullable=False),
        sa.Column("private_key_ref", sa.Text, nullable=False),
        # NULL for env:/file: rows, whose material this process did not write.
        sa.Column("private_key_pem_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # NULL not_after means open-ended (+infinity) — the current key always is.
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        # security.mdx: a kid MUST NOT collide with any other entry in the JWKS,
        # and one JWKS is published per tenant.
        sa.UniqueConstraint("tenant_id", "kid", name="uq_signing_keys_tenant_kid"),
        sa.CheckConstraint(f"alg IN ({sql_value_list(SIGNING_ALG_VALUES)})", name="ck_signing_keys_alg"),
        sa.CheckConstraint(f"purpose IN ({sql_value_list(MINTABLE_PURPOSES)})", name="ck_signing_keys_purpose"),
    )

    op.create_index(
        "idx_signing_keys_tenant_purpose_active",
        "signing_keys",
        ["tenant_id", "purpose", "not_after"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_signing_keys_tenant_purpose_active", "signing_keys")
    op.drop_table("signing_keys")

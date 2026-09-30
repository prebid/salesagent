"""add_signing_keys_table

Per-tenant RFC 9421 signing key material (#1291 A2, salesagent-z6nr.8).

One row binds a unique ``kid`` to BOTH halves of the key: the public JWK we
publish, and the private half as the PKCS#8 ``BEGIN ENCRYPTED PRIVATE KEY`` PEM
the SDK returned, encrypted under the deployment KEK.

``private_key_pem_encrypted`` is NOT NULL because the database is the only place
a private half ever lives. There is no locator column and no storage scheme: a
row that exists has material, so a published key nothing can sign with is
unrepresentable rather than merely unusual. Provisioning refuses when no KEK is
configured, which is what keeps this column from ever holding plaintext.

The table is defined in ONE revision rather than created here and amended later.
This revision has never reached ``main``, so no environment has applied it and
there is no deployed schema to preserve; a follow-up revision would exist only to
record a decision git already records — the two-migrations-for-a-zero-migration-
problem CLAUDE.md § Database migrations forbids.

The two CHECK constraints are DERIVED from ``src.core.signing.algorithms`` and
rendered by the same helper the ORM model uses, so the migration DDL and the ORM
DDL cannot disagree — migration ``e381618812f1`` exists because a hand-written
CHECK froze while a spec enum grew (#1521).

This revision was authored against ``d3f7a5c81e46`` and RE-PARENTED onto
``c41f8a6b2d90`` (inbound request signature verification) while merging the
signing epic onto #1721. ``d3f7a5c81e46`` is an ancestor of ``c41f8a6b2d90``, so
the re-parent only lengthens this revision's lineage — it drops nothing and adds
no merge point. It is not a post-commit edit of a shipped migration: this file
has never reached ``main``, so no database has ever recorded ``e7a2c40b91d5``
following ``d3f7a5c81e46``. The alternative — a merge migration — needs two
GENUINE heads, which after the merge there are not.

Revision ID: e7a2c40b91d5
Revises: c41f8a6b2d90
Create Date: 2026-07-27 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType
from src.core.signing.algorithms import MINTABLE_PURPOSES, SIGNING_ALG_VALUES, sql_value_list

# revision identifiers, used by Alembic.
revision: str = "e7a2c40b91d5"
down_revision: str | Sequence[str] | None = "c41f8a6b2d90"
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
        # NOT NULL: the row IS where the private half lives, so a row without one
        # would be a published key nothing can sign with.
        sa.Column("private_key_pem_encrypted", sa.LargeBinary, nullable=False),
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

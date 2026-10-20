"""Onboarded request-signing keyids, and the signed-requests test counterparty's record

1. ``principal_signing_keyids`` maps a keyid to the counterparty principal whose
   ``agent_url`` is its brand.json ``agents[]`` entry (security.mdx @ v3.1.1 L1088-1094).
   ``keyid`` uses ``collation="C"`` as ``adcp_replay`` does.
2. ``principals.signed_requests_grading`` holds the signed-requests test kit's relaxations
   on the kit's test counterparty.

Revision ID: b8d3e5f1a207
Revises: 7f31c0ab94d2
Create Date: 2026-10-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d3e5f1a207"
down_revision: str | Sequence[str] | None = "7f31c0ab94d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "principals",
        sa.Column(
            "signed_requests_grading",
            postgresql.JSONB(),
            nullable=True,
            comment="signed_requests test counterparty: the kit's JWKS, replay cap and TTL; NULL = an ordinary counterparty",
        ),
    )
    op.create_table(
        "principal_signing_keyids",
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("keyid", sa.Text(collation="C"), nullable=False),
        sa.Column("principal_id", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "keyid"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
            name="fk_principal_signing_keyids_principal",
        ),
    )


def downgrade() -> None:
    op.drop_table("principal_signing_keyids")
    op.drop_column("principals", "signed_requests_grading")

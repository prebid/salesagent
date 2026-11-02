"""Add the inbound RFC 9421 verifier's two storage needs.

#1291 B1/A4, re-homed onto #1721's request boundary.

1. ``adcp_replay`` — the deployment-wide nonce store the SDK's verifier checklist calls
   at steps 9a/11. Translated from the DDL the SDK ships at
   ``adcp/signing/pg/replay_store.sql``, table name and index names included, so that
   file stays a valid reference and a future swap to the SDK's own ``PgReplayStore``
   needs no migration.
2. ``principals.agent_url`` — the counterparty's own AdCP agent URL, recorded at
   onboarding. The verifier reads it in both directions: forward to resolve the
   counterparty's JWKS, and backward to establish the principal a verified signature
   belongs to when the caller presented no bearer. The reverse read is why the column is
   UNIQUE per tenant rather than merely indexed.

Revision ID: c41f8a6b2d90
Revises: 390461e816ea
"""

import sqlalchemy as sa
from alembic import op

revision = "c41f8a6b2d90"
down_revision = "390461e816ea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adcp_replay",
        # ``collation="C"`` is security, not style: under some locales "Key-A" and "key-a"
        # compare equal, which would let an attacker collapse distinct kids or nonces into
        # one slot and replay against it. "C" is byte-for-byte.
        sa.Column("keyid", sa.Text(collation="C"), nullable=False),
        sa.Column("nonce", sa.Text(collation="C"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("keyid", "nonce"),
    )
    # The sweep's index (the SDK's name).
    op.create_index("adcp_replay_expires_idx", "adcp_replay", ["expires_at"])
    # ``at_capacity``'s predicate is ``keyid = ? AND expires_at > now()``. A partial index
    # on ``now()`` is impossible — it is not IMMUTABLE — so the composite carries the whole
    # predicate and the count is an index-only scan instead of a heap walk per keyid.
    op.create_index("adcp_replay_keyid_expires_idx", "adcp_replay", ["keyid", "expires_at"])

    op.add_column("principals", sa.Column("agent_url", sa.String(length=500), nullable=True))
    op.create_unique_constraint("uq_principals_tenant_agent_url", "principals", ["tenant_id", "agent_url"])


def downgrade() -> None:
    op.drop_constraint("uq_principals_tenant_agent_url", "principals", type_="unique")
    op.drop_column("principals", "agent_url")
    op.drop_index("adcp_replay_keyid_expires_idx", table_name="adcp_replay")
    op.drop_index("adcp_replay_expires_idx", table_name="adcp_replay")
    op.drop_table("adcp_replay")

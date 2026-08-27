"""add adcp_replay nonce store

Revision ID: 04c04fef9503
Revises: e7a2c40b91d5
Create Date: 2026-07-28 00:51:00.197126

RFC 9421 replay-dedup store (#1291 A4). Translated from the DDL the SDK ships at
``adcp/signing/pg/replay_store.sql`` — the canonical table name ``adcp_replay`` is
kept so that file stays a valid reference and a future swap to the SDK's own
``PgReplayStore`` is schema-compatible.

``COLLATE "C"`` on the identifier columns is a replay-bypass guard, not a style
choice: the SDK's SQL header records that under some locales ``"Key-A"`` and
``"key-a"`` compare equal, which lets an attacker collapse distinct kids/nonces
into one slot. ``"C"`` is byte-for-byte comparison.

No ``tenant_id`` column and no FK, matching the SDK schema — see the
``ReplayNonce`` model docstring for why (``@authority`` is a mandatory covered
component, so cross-tenant replay dies at verifier step 10).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04c04fef9503"
down_revision: str | Sequence[str] | None = "e7a2c40b91d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "adcp_replay",
        sa.Column(
            "keyid",
            sa.Text(collation="C"),
            nullable=False,
            comment='Signature keyid. COLLATE "C": locale case-folding would collapse distinct kids',
        ),
        sa.Column(
            "nonce",
            sa.Text(collation="C"),
            nullable=False,
            comment='Signature nonce. COLLATE "C": same replay-bypass guard as keyid',
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Server-clock instant after which this claim is dead and the nonce is claimable again",
        ),
        sa.PrimaryKeyConstraint("keyid", "nonce"),
    )
    # Serves the reaper's ``expires_at <= now()`` sweep (the SDK's index, same name).
    op.create_index("adcp_replay_expires_idx", "adcp_replay", ["expires_at"], unique=False)
    # Serves ``at_capacity``: ``keyid = :k AND expires_at > now()`` runs at verifier
    # step 9a on EVERY signed request, before crypto verify, precisely so an abusive
    # signer is cheap to reject. Neither the PK (keyid, nonce) nor the expires index
    # answers that predicate without a heap fetch per row — at the spec's 1,000,000
    # per-keyid cap that is up to a million tuple visits per request, which inverts
    # step 9a's purpose. The SDK's SQL header names this composite as the fix and
    # explains why the tempting partial index ``(keyid) WHERE expires_at > now()`` is
    # impossible (``now()`` is STABLE, not IMMUTABLE, so Postgres forbids it in an
    # index predicate). Shipped now, not "if profiling identifies it": adding an index
    # to a hot table later is the expensive kind of follow-up.
    op.create_index("adcp_replay_keyid_expires_idx", "adcp_replay", ["keyid", "expires_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("adcp_replay_keyid_expires_idx", table_name="adcp_replay")
    op.drop_index("adcp_replay_expires_idx", table_name="adcp_replay")
    op.drop_table("adcp_replay")

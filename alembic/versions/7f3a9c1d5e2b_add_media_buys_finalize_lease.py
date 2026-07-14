"""Add crash-recoverable approval finalization state to media_buys (#1637).

Four nullable columns backing the exactly-once phase-2 protocol:

- ``finalize_lease_id`` / ``finalize_lease_expires_at`` — durable expiring
  ownership of phase 2 (adapter execution + publish). Only the lease owner may
  invoke the external adapter or publish the serving status; the scheduler's
  reconciler acquires ONLY absent/expired leases.
- ``finalize_adapter_invoked_at`` — committed immediately before the adapter is
  invoked; presence means remote mutations may exist, so only fully-replayable
  adapters may auto-resume past it.
- ``finalize_recovery_mode`` — NULL = automatic recovery; ``manual_required`` =
  the reconciler must not touch the buy again (crash left a possibly-partial
  remote graph on a non-replayable adapter). Operator remediation:
  ``UPDATE media_buys SET finalize_recovery_mode = NULL WHERE media_buy_id = ...``
  after reconciling the remote state (or resetting the buy for re-approval).

No backfill: all four are operation-state fields that are meaningless for rows
not currently mid-finalization; existing rows correctly read as "no operation
in flight".

Revision ID: 7f3a9c1d5e2b
Revises: 2c4e6a7b8d9e, 823974a5553e
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f3a9c1d5e2b"
# Merge point: this branch's confirmed_at migration (2c4e6a7b8d9e) and main's
# idx_delivery_sim_tenant drop (823974a5553e, arrived via the #1430 merge) were
# sibling heads; this revision joins them while adding the finalize-lease state.
down_revision: str | Sequence[str] | None = ("2c4e6a7b8d9e", "823974a5553e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_buys", sa.Column("finalize_lease_id", sa.String(length=64), nullable=True))
    op.add_column("media_buys", sa.Column("finalize_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media_buys", sa.Column("finalize_adapter_invoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media_buys", sa.Column("finalize_recovery_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("media_buys", "finalize_recovery_mode")
    op.drop_column("media_buys", "finalize_adapter_invoked_at")
    op.drop_column("media_buys", "finalize_lease_expires_at")
    op.drop_column("media_buys", "finalize_lease_id")

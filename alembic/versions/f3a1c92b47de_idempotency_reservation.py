"""idempotency reservation lifecycle (in_flight -> completed)

Round-2 blocker B1: durable, first-insert-wins idempotency reservation. The
verbatim success cache becomes two-phase: a reservation row is INSERTed
``status='in_flight'`` (``response_envelope`` NULL) in its own committed
transaction BEFORE any account side effect, and is flipped to
``status='completed'`` (envelope populated) in the SAME transaction as the
account write. The existing unique index on (tenant, principal, account, key)
is the concurrency enforcer — the racing INSERT that loses is classified as
REPLAY / CONFLICT / IN_FLIGHT.

Two schema changes make the reservation storable:
- ``status String(16) NOT NULL server_default='completed'`` — existing rows are
  completed successes; new reservations start ``in_flight``.
- ``response_envelope`` becomes NULLABLE — an in-flight reservation has no
  response yet. The read path (``find_by_key``) filters ``status='completed'``
  so an in-flight row never replays.

Revision ID: f3a1c92b47de
Revises: 727fcce72e2a
Create Date: 2026-07-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c92b47de"
down_revision: str | Sequence[str] | None = "727fcce72e2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the reservation ``status`` column and make ``response_envelope`` nullable."""
    op.add_column(
        "idempotency_attempts",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
    )
    op.alter_column("idempotency_attempts", "response_envelope", existing_type=sa.JSON(), nullable=True)


def downgrade() -> None:
    """Drop the reservation lifecycle: remove in-flight rows, restore NOT NULL envelope."""
    # In-flight reservations carry a NULL envelope and cannot satisfy the
    # restored NOT NULL constraint — they are transient by definition, so
    # deleting them is safe (a retry re-executes).
    op.execute("DELETE FROM idempotency_attempts WHERE response_envelope IS NULL")
    op.alter_column("idempotency_attempts", "response_envelope", existing_type=sa.JSON(), nullable=False)
    op.drop_column("idempotency_attempts", "status")

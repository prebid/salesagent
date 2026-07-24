"""add media_buys.final_webhook_claimed_at (best-effort final-webhook claim)

Serializes a buy's one FINAL delivery webhook across concurrent scheduler/manual
workers (#1575). The delivery scheduler atomically claims the final via a
conditional UPDATE on this column before the outbound POST, so only one worker
sends; a stale claim (crashed worker, older than the lease) self-heals on a later
batch. NULL until a final is claimed. The residual crash-after-POST duplicate
window is deferred to the durable outbox (#1606).

Revision ID: d3f8a1c4b592
Revises: 823974a5553e
Create Date: 2026-07-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f8a1c4b592"
down_revision: str | Sequence[str] | None = "823974a5553e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable final-webhook claim timestamp."""
    op.add_column(
        "media_buys",
        sa.Column("final_webhook_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the final-webhook claim timestamp."""
    op.drop_column("media_buys", "final_webhook_claimed_at")

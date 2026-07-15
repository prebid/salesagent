"""add revision to media_buys

AdCP 3.1.1 update-media-buy carries an optional-concurrency ``revision`` field:
the buyer echoes the last-read revision, the server compares-and-increments
atomically, and a mismatch is a CONFLICT. Persisting the counter is a
prerequisite — an in-memory constant cannot detect concurrent modification.

New rows start at revision 1 (server_default="1"); every successful mutating
update increments it. NOT NULL: every media buy has a revision from creation.

Revision ID: 727fcce72e2a
Revises: 823974a5553e
Create Date: 2026-07-15 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "727fcce72e2a"
down_revision: str | Sequence[str] | None = "823974a5553e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the NOT NULL revision counter (default 1) to media_buys."""
    op.add_column(
        "media_buys",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    """Drop the revision counter from media_buys."""
    op.drop_column("media_buys", "revision")

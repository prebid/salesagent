"""add media_buys.revision

Persisted monotonic optimistic-concurrency counter for media buys
(AdCP 3.1.0-beta.3 `revision` response field). A persisted counter — bumped by the
repository on every successful mutation — is the only way to guarantee
strict monotonicity: anything derived from timestamps collides when two
updates land within the clock resolution.

Existing rows are backfilled to 1 via the server default: revision was
never emitted before this migration, so starting every buy at revision 1
is the correct baseline.

Revision ID: 1497aa06013c
Revises: a164b85bab9e
Create Date: 2026-07-03 13:40:00.837250

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1497aa06013c"
down_revision: str | Sequence[str] | None = "a164b85bab9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add media_buys.revision, backfilling existing rows to 1."""
    op.add_column(
        "media_buys",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    """Drop media_buys.revision."""
    op.drop_column("media_buys", "revision")

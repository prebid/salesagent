"""merge signing and auto-naming heads

Reconciles the two heads that landed in parallel on main:
- ``j0k1l2m3n4o5`` (audit log signing columns)
- ``b0545900b6b1`` (tenant.auto_naming_enabled)

Both shipped to main without a merge migration, leaving the alembic graph
with two heads. This migration is empty — it only collapses the heads.

Revision ID: c1d2e3f4a5b6
Revises: j0k1l2m3n4o5, b0545900b6b1
Create Date: 2026-05-06 13:00:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = ("j0k1l2m3n4o5", "b0545900b6b1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration — no schema changes."""
    pass


def downgrade() -> None:
    """Merge migration — no schema changes to revert."""
    pass

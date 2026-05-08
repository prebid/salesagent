"""merge mock-platform branch into all current heads

Reconciles three open heads as of 2026-05-07:
- ``789f9d88265e`` (add push_notification_configs.signing_mode — not
  subsumed by the post-merge chain below)
- ``6daaddad5e2a`` (main's mergepoint of advertiser_buyer_assignment with
  fix_duplication; transitively subsumes ``0fa8fa8610df``,
  ``q9r0s1t2u3v4``, ``523ed762edce``, and ``o6p7q8r9s0t1``)

Pulls both orphan tips into a single graph head.

Revision ID: d8e9f0a1b2c3
Revises: 789f9d88265e, 6daaddad5e2a
Create Date: 2026-05-07 15:55:00.000000

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = ("789f9d88265e", "6daaddad5e2a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration — no schema changes."""
    pass


def downgrade() -> None:
    """Merge migration — no schema changes to revert."""
    pass

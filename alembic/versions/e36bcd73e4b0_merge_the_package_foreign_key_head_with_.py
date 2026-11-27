"""merge the package foreign key head with the policy settings head

Revision ID: e36bcd73e4b0
Revises: 502abb701275, 8391a08f08f9
Create Date: 2026-11-26 13:23:14.659871

Joins the two heads that #2664 and #2725 left on ``main``. Both descend from
``b9b8e637b56c``, and each line adds its own revisions:

* ``502abb701275`` adds the composite foreign key from ``creative_assignments`` to
  ``media_packages``;
* ``c3ebb9a41337`` completes every stored ``tenants.advertising_policy``, and
  ``8391a08f08f9`` drops ``tenants.policy_settings``.

The two lines change different tables, so the order between them cannot matter and
this revision has nothing to reconcile. It carries no operations. It gives the graph
one head, so ``alembic upgrade head`` runs again (#2732).
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e36bcd73e4b0"
down_revision: str | Sequence[str] | None = ("502abb701275", "8391a08f08f9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to do: the two lines change different tables (see the module docstring)."""


def downgrade() -> None:
    """Nothing to do: this revision made no schema change to reverse."""

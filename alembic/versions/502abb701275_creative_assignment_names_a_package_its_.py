"""creative assignment names a package its buy holds

``creative_assignments(media_buy_id, package_id)`` had a foreign key to ``media_buys`` and
none to ``media_packages``, whose primary key is ``(media_buy_id, package_id)``. So an
assignment could name a package its buy does not hold, and the approval upload, which
reads each creative once per package of the buy, failed on such a row after the order was
booked at the ad server. This revision adds the composite foreign key, so the database
refuses the row.

The delete rule is NO ACTION, the rule of the ``media_buys`` foreign key beside it: nothing
deletes a package while its assignments exist.

This revision changes structure only. On a database that already holds an assignment
naming a package its buy does not hold, the upgrade fails on that row.

Revision ID: 502abb701275
Revises: b9b8e637b56c
Create Date: 2026-11-20 19:36:37.210354

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "502abb701275"
down_revision: str | Sequence[str] | None = "b9b8e637b56c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_creative_assignments_package_composite"


def upgrade() -> None:
    """Refuse an assignment that names a package its buy does not hold."""
    op.create_foreign_key(
        _CONSTRAINT,
        "creative_assignments",
        "media_packages",
        ["media_buy_id", "package_id"],
        ["media_buy_id", "package_id"],
    )


def downgrade() -> None:
    """Drop the foreign key this revision added."""
    op.drop_constraint(_CONSTRAINT, "creative_assignments", type_="foreignkey")

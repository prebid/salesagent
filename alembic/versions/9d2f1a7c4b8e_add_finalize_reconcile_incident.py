"""Add ownership-independent finalize reconcile-incident marker to media_buys (#1637 Hole A).

Two nullable columns that durably record a POSSIBLE DUPLICATE remote order when a worker's
adapter ran but the worker could not assert single ownership of the finalization (it lost the
lease to a newer owner mid-run, or a post-mutation ambiguity left a partial/duplicate remote
graph):

- ``finalize_reconcile_incident_at`` — first instant a possible-duplicate incident was
  recorded for the buy (keep-first; a later incident never overwrites it).
- ``finalize_reconcile_incident_reason`` — short human-readable cause.

Unlike the lease/recovery fields these are NOT cleared by a successful publish: the winning
owner's clean publish leaves them set so an operator still discovers the possible duplicate
even though the buy shows serving. Recorded WITHOUT a lease CAS (ownership-independent) so the
LOSING worker — which owns no lease — can still leave the trace instead of silently swallowing
the possible duplicate.

No backfill: both are operation-state fields meaningless for rows with no incident; existing
rows correctly read as "no incident".

Revision ID: 9d2f1a7c4b8e
Revises: 7f3a9c1d5e2b
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d2f1a7c4b8e"
down_revision: str | Sequence[str] | None = "7f3a9c1d5e2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_buys", sa.Column("finalize_reconcile_incident_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media_buys", sa.Column("finalize_reconcile_incident_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_buys", "finalize_reconcile_incident_reason")
    op.drop_column("media_buys", "finalize_reconcile_incident_at")

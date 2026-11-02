"""merge account/creative repair heads with the push-notification protocol head

Revision ID: a773916cc1ca
Revises: 19dd84c79382, e6bb3ee6ae13
Create Date: 2026-09-02 21:48:54.501448

Reconciles the two heads a branch merge left behind. Both descend from
``823974a5553e`` and both already carry ``9b2d4f6c1a37`` (the media_buys chain), so
the only revisions each contributes uniquely are:

* under ``19dd84c79382``: the account/tenant/creative work — ``accounts``
  (``billing`` check constraint, ``notification_configs``, ``billing_entity``,
  ``brand_id`` repair, natural-key unique index), ``tenants``
  (``account_sandbox``, ``capability_declarations``), and the ``creatives``
  status repair;
* under ``e6bb3ee6ae13``: the webhook work — ``push_notification_configs``
  (``authentication_type`` spelling normalization, and the new ``protocol``
  column).

The two sets touch disjoint tables, so ordering between them cannot matter and
there is no conflict for this revision to resolve. It therefore carries no
operations: it exists to give the graph a single head, which is exactly what a
merge revision is for.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a773916cc1ca"
down_revision: str | Sequence[str] | None = ("19dd84c79382", "e6bb3ee6ae13")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to do — the merged chains are schema-disjoint (see module docstring)."""


def downgrade() -> None:
    """Nothing to do — this revision applied no schema change to reverse."""

"""repair creatives rows carrying the non-spec status 'pending'

Revision ID: c7a2f10b93de
Revises: f4d31a97c058
Create Date: 2026-07-29 12:40:00.000000

``creatives.status`` defaulted to ``'pending'`` in three places (the column, the
``CreativeRepository.create`` parameter, and the test factory) since the initial
schema, but ``'pending'`` is not a member of the AdCP ``CreativeStatus`` enum
(``processing``/``pending_review``/``approved``/``suspended``/``rejected``/``archived``).
``list_creatives`` parses this column through that closed enum, so every row
written with the field omitted read back as a status the seller never asserted.
The write-side defaults are fixed; rows already written that way are not.

Detection query (runnable standalone by an operator)::

    SELECT tenant_id, creative_id, status
      FROM creatives
     WHERE status NOT IN ('processing', 'pending_review', 'approved',
                          'suspended', 'rejected', 'archived');

WHY THE TARGET IS ``'pending_review'`` AND NOT ``'processing'``. The read path in
``listing.py`` substitutes ``processing`` for an unparseable status because it
knows NOTHING about a value it could not parse, and ``processing`` is the member
that asserts the least. Here we know exactly what ``'pending'`` meant: it is the
pre-review queue state — what ``templates/creatives_list.html`` labels "Pending
Review" and what ``admin/blueprints/creatives.py`` writes as ``pending_review``
for the same situation today. Same column, two different questions, two different
answers. Do not conflate them.

DELIBERATE DIVERGENCE FROM f4d31a97c058: that migration ABORTS on data it cannot
repair unambiguously, because its poisoned rows are ORM-UNREADABLE
(``JSONType(model=BrandReference)`` raises ``ValidationError`` on every load), so
skipping them would leave the service broken. ``creatives.status`` is a plain
``String(50)`` that loads fine; an unrecognized value now produces a WARNING log
and a ``CONFIGURATION_ERROR`` advisory on the ``list_creatives`` response rather
than a failure. Migrations run automatically on startup and
``scripts/ops/migrate.py`` exits non-zero on failure, so blocking boot over a
read-path condition that is already surfaced correctly would be disproportionate.
Any OTHER non-spec value is therefore LOGGED and left alone.

No CHECK constraint and no PostgreSQL enum type is added. The AdCP enum widens
over time; DB-level DDL would turn a future spec bump into a boot-blocking
migration. ``src/core/tools/_media_buy_status.py`` deliberately solves the same
problem in code rather than in DDL.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a2f10b93de"
down_revision: str | Sequence[str] | None = "f4d31a97c058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: The AdCP 3.1.1 creative-status vocabulary, frozen here on purpose: a migration
#: describes the database at ONE point in history and must not change meaning when
#: the pinned SDK enum widens. The live code-side pin is
#: tests/unit/test_architecture_creative_status_vocabulary.py.
_SPEC_STATUSES = ("processing", "pending_review", "approved", "suspended", "rejected", "archived")

#: The one non-spec value this system is known to have written, and the state it
#: meant. Only this value is rewritten.
_LEGACY_STATUS = "pending"
_REPAIRED_STATUS = "pending_review"

_SPEC_LIST_SQL = ", ".join(f"'{status}'" for status in _SPEC_STATUSES)

_SURVEY_SQL = f"""
    SELECT status, count(*) AS row_count
      FROM creatives
     WHERE status NOT IN ({_SPEC_LIST_SQL})
     GROUP BY status
     ORDER BY status
"""

_REPAIR_SQL = sa.text(
    "UPDATE creatives SET status = :repaired WHERE status = :legacy",
).bindparams(repaired=_REPAIRED_STATUS, legacy=_LEGACY_STATUS)


def upgrade() -> None:
    """Rewrite the legacy ``'pending'`` creative status to the spec's ``'pending_review'``.

    Survey first, act second — the same order f4d31a97c058 uses. A clean database
    issues no row-touching statement at all.
    """
    bind = op.get_bind()

    non_spec = bind.execute(sa.text(_SURVEY_SQL)).fetchall()
    if not non_spec:
        logger.info("No creatives carry a non-spec status — nothing to repair.")
        return

    unrecognized = [row for row in non_spec if row.status != _LEGACY_STATUS]
    if unrecognized:
        detail = ", ".join(f"{row.status!r} ({row.row_count} row(s))" for row in unrecognized)
        logger.warning(
            "creatives.status carries %d unrecognized non-spec value(s) this migration does not "
            "repair: %s. Only %r has a known meaning. These rows load fine through the ORM; "
            "list_creatives reports them as 'processing' and names each one in the response's "
            "errors[] with CONFIGURATION_ERROR, so an operator can repair them without blocking "
            "startup. Repair them with direct SQL once their intended status is known.",
            len(unrecognized),
            detail,
            _LEGACY_STATUS,
        )

    if not any(row.status == _LEGACY_STATUS for row in non_spec):
        return

    result = bind.execute(_REPAIR_SQL)
    logger.info(
        "Rewrote creatives.status %r -> %r on %d row(s).",
        _LEGACY_STATUS,
        _REPAIRED_STATUS,
        result.rowcount,
    )


def downgrade() -> None:
    """Intentionally does not restore ``'pending'``.

    A repaired row is byte-identical to a row that was always written as
    ``pending_review`` — the write path left no marker distinguishing them — so a
    reverse UPDATE would re-break rows this migration never touched, including every
    row the admin creative-review path writes today. It logs rather than raising
    because a raising downgrade would block rolling back PAST this revision at all.
    Precedent for a deliberately asymmetric downgrade: f4d31a97c058, e381618812f1.
    """
    logger.info(
        "Downgrade of %s is a no-op by design: repaired %r values are indistinguishable from "
        "values that were always correct, so restoring %r would corrupt rows this migration "
        "never touched. Repaired data stays repaired.",
        revision,
        _REPAIRED_STATUS,
        _LEGACY_STATUS,
    )

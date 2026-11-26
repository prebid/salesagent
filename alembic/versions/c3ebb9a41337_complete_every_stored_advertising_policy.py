"""complete every stored advertising policy

Revision ID: c3ebb9a41337
Revises: b9b8e637b56c
Create Date: 2026-11-23 21:57:27.342308

``tenants.advertising_policy`` becomes ``JSONType(model=AdvertisingPolicy)`` (GH #2696). The
model has no defaults and forbids undeclared keys, so a row loads only when it holds every
key. No row stored before this revision does: the Settings form never writes
``require_manual_review`` or ``description``, and it writes a list only when the form posts
it. This revision completes every non-NULL row by adding each missing key with the value the
old readers already gave its absence (``.get(key, default)``): ``enabled`` false, each list
empty, ``require_manual_review`` false, ``description`` null. A key that holds JSON null is
missing for this purpose, because no reader could use it. A NULL row stays NULL: it means the
tenant saved no policy.

Keeping the rows is an owner-approved exception to CLAUDE.md's "Do not preserve data across a
migration" (#2694, part 1): each row is the policy an operator saved, and completing it
changes no behavior, because every added value is the one the readers already assumed.

A row this revision cannot complete without guessing aborts the upgrade, naming the row: a
value that is not a JSON object, an undeclared key, or a declared key of the wrong type.
Rewriting an operator's live policy on a guess is not a migration's call.

Detection: ``_SURVEY_SQL`` lists the rows that block the upgrade, runnable standalone by an
operator.
"""

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.migration_guards import abort_if_rows

# revision identifiers, used by Alembic.
revision: str = "c3ebb9a41337"
down_revision: str | Sequence[str] | None = "b9b8e637b56c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_FLAGS = ("enabled", "require_manual_review")
_LISTS = (
    "prohibited_advertisers",
    "prohibited_categories",
    "prohibited_tactics",
    "default_prohibited_categories",
    "default_prohibited_tactics",
)

#: The value each key takes when the row lacks it, inlined: a revision keeps behaving the way
#: it did when it ran, whatever the model becomes.
_COMPLETION = json.dumps({**dict.fromkeys(_FLAGS, False), **{key: [] for key in _LISTS}, "description": None})


def _sql_list(values: Sequence[str]) -> str:
    """*values* as a comma-separated list of SQL string literals; each is a constant of this module."""
    return ", ".join(f"'{value}'" for value in values)


#: A key of one row's policy that this revision cannot complete: undeclared, or declared with
#: a value of the wrong type. JSON null is not blocking; it counts as a missing key.
_BLOCKING_KEY = f"""
    NOT (kv.key IN ({_sql_list((*_FLAGS, *_LISTS, "description"))}))
    OR (jsonb_typeof(kv.value) <> 'null' AND NOT (
        (kv.key IN ({_sql_list(_FLAGS)}) AND jsonb_typeof(kv.value) = 'boolean')
        OR (kv.key IN ({_sql_list(_LISTS)}) AND jsonb_typeof(kv.value) = 'array'
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(kv.value) AS e WHERE jsonb_typeof(e) <> 'string'))
        OR (kv.key = 'description' AND jsonb_typeof(kv.value) = 'string')
    ))
"""

#: JSON ``null`` reads as no policy (the driver hands back ``None``), so it is left alone.
_SURVEY_SQL = f"""
    SELECT tenant_id, advertising_policy::text AS policy
      FROM tenants
     WHERE advertising_policy IS NOT NULL
       AND CASE jsonb_typeof(advertising_policy)
             WHEN 'null' THEN false
             WHEN 'object' THEN EXISTS (
                 SELECT 1 FROM jsonb_each(advertising_policy) AS kv(key, value) WHERE {_BLOCKING_KEY}
             )
             ELSE true
           END
     ORDER BY tenant_id
"""

_COMPLETED = f"('{_COMPLETION}'::jsonb || jsonb_strip_nulls(advertising_policy))"

_COMPLETE_SQL = f"""
    UPDATE tenants
       SET advertising_policy = {_COMPLETED}
     WHERE jsonb_typeof(advertising_policy) = 'object'
       AND advertising_policy IS DISTINCT FROM {_COMPLETED}
"""

#: Drops each key whose value is the one ``upgrade`` adds, which is the value the readers
#: before this revision give the key's absence.
_STRIP_SQL = f"""
    UPDATE tenants
       SET advertising_policy = (
           SELECT COALESCE(jsonb_object_agg(kv.key, kv.value), '{{}}'::jsonb)
             FROM jsonb_each(tenants.advertising_policy) AS kv(key, value)
            WHERE ('{_COMPLETION}'::jsonb -> kv.key) IS DISTINCT FROM kv.value
       )
     WHERE jsonb_typeof(advertising_policy) = 'object'
"""


def upgrade() -> None:
    """Refuse a row that cannot be completed, then complete every other one."""
    bind = op.get_bind()
    abort_if_rows(
        bind,
        _SURVEY_SQL,
        describe=lambda row: f"tenant={row.tenant_id} advertising_policy={row.policy}",
        headline="Cannot complete {count} stored advertising policy row(s) as AdvertisingPolicy:",
        remedy=(
            "Each row above is not a JSON object, carries a key AdvertisingPolicy does not declare, "
            "or holds a declared key with a value of the wrong type. Correct or NULL each row by hand, "
            "then re-run the upgrade. This revision does not guess at an operator's saved policy."
        ),
    )
    result = bind.execute(sa.text(_COMPLETE_SQL))
    logger.info("Completed %d stored advertising policy row(s).", result.rowcount)


def downgrade() -> None:
    """Return each row to the partial shape: drop every key that holds its completion value."""
    result = op.get_bind().execute(sa.text(_STRIP_SQL))
    logger.info("Stripped completion keys from %d stored advertising policy row(s).", result.rowcount)

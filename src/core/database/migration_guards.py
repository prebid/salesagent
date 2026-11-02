"""Refusal guards for Alembic migrations that must not remediate on their own.

Several migrations in this repo face the same decision: the schema change they
want to make is impossible over the rows already in the table, and the only
automatic ways forward — NULLing a column, deleting a row, picking a survivor —
are decisions about a tenant's live data that a migration has no standing to
make. The established answer (owner decision, 2026-07-27, first encoded by
revision ``b2e94f7c1a03``) is to survey the offending rows, report them, and
stop.

:func:`abort_if_rows` is that survey-and-abort, in one place. It carries no
policy of its own — each caller supplies the survey, how to render a row, and
what the operator should do about it — only the shape: run the survey, and if it
returns anything, raise instead of proceeding.

Lives under ``src/`` rather than beside the migrations because
``alembic/versions`` is loaded by Alembic's script scanner (every module there is
expected to declare a ``revision``), and because migrations already import from
``src.core.database`` — see ``json_type.JSONType``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Row


def abort_if_rows(
    bind: Connection,
    survey_sql: str,
    *,
    describe: Callable[[Row[Any]], str],
    headline: str,
    remedy: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Run ``survey_sql``; if it returns rows, raise ``RuntimeError`` naming them.

    Args:
        bind: the migration's connection, i.e. ``op.get_bind()``.
        survey_sql: a SELECT identifying the rows that block the change. It must
            select whatever ``describe`` reads, and should ORDER BY so the report
            is stable across runs.
        describe: renders ONE surveyed row as a line of the report. Include the
            identifiers an operator needs to find the row by hand — tenant and
            primary key at minimum.
        headline: the first line, with a ``{count}`` placeholder for the number
            of blocking rows. Say what cannot be done, not merely that something
            failed.
        remedy: the closing paragraph — what the operator must do to the rows
            before re-running, and why this migration will not do it for them.
        params: bind parameters for ``survey_sql``, if it takes any.

    Raises:
        RuntimeError: when the survey returns at least one row. Alembic runs each
            migration in a transaction, so raising leaves the schema and the rows
            exactly as they were.
    """
    rows = bind.execute(sa.text(survey_sql), params or {}).fetchall()
    if not rows:
        return

    detail = "\n".join(describe(row) for row in rows)
    raise RuntimeError(f"{headline.format(count=len(rows))}\n{detail}\n{remedy}")

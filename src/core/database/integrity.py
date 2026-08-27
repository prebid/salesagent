"""Deciding WHICH constraint an ``IntegrityError`` violated.

A unique index is the only real defence against two concurrent writers, so the
loser of a race necessarily takes an ``IntegrityError``. Recovering from it is
routine; recovering from the WRONG one is not. An unnarrowed
``except IntegrityError`` swallows foreign-key, CHECK and primary-key violations
too and reports them as whatever collision the handler happened to expect, which
turns a real defect into a misleading success (#1721).

This module is the one place that answers "is this error THAT constraint?", so a
handler states the constraint it recovers from and everything else propagates —
and ``resolve_or_write`` is the one place that turns that answer into a recovery,
so the loser of a race is told exactly what the winner's pre-check would have
told it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def is_constraint_violation(
    exc: IntegrityError,
    constraint: str,
    *,
    message_token: str | None = None,
) -> bool:
    """True iff ``exc`` is a violation of ``constraint``.

    Prefers the driver's structured constraint name
    (``exc.orig.diag.constraint_name``), matched by PREFIX — so a build-time
    rename suffix (e.g. the ``CREATE INDEX CONCURRENTLY`` swap variants) still
    matches, while an unrelated constraint that merely contains the same column
    token does not.

    Falls back to a substring scan of the driver message only when the structured
    diagnostic is unavailable (non-psycopg2 drivers, wrapped errors).
    ``message_token`` overrides what that fallback looks for; it defaults to the
    constraint name.
    """
    name = getattr(getattr(exc, "orig", None), "diag", None)
    constraint_name = getattr(name, "constraint_name", None)
    if constraint_name:
        return constraint_name.startswith(constraint)
    return (message_token or constraint) in str(getattr(exc, "orig", exc))


def resolve_or_write[T](
    session: Session,
    *,
    conflict: Callable[[], T | None],
    write: Callable[[], None],
    constraint: str | tuple[str, ...],
) -> T | None:
    """Write against a UNIQUE index, answering a lost race exactly like a won one.

    A pre-check ``SELECT`` runs inside the caller's own transaction, so it cannot
    see a row a concurrent transaction has staged but not yet committed. It is a
    good fast path, but the index is the only authority — and when only the
    pre-check's outcome is handled, the loser of the race takes an unhandled
    ``IntegrityError`` out of flush and is told something completely different
    from what the winner is told for the identical condition.

    ``conflict`` is the caller's OWN pre-check: it returns the caller's polite
    answer when the row is already there, or ``None``. This helper invokes it on
    BOTH paths — before writing, and again after losing the race — so the two
    answers cannot diverge: they are literally the same function. Returns that
    answer, or ``None`` when the write succeeded and the caller should carry on
    (the caller still owns its own ``commit()``).

    ``constraint`` accepts a tuple when one statement can trip more than one
    index (a tenant INSERT can violate either ``tenants_pkey`` or
    ``tenants_subdomain_key``). Anything else propagates untouched — an
    unnarrowed ``except IntegrityError`` would swallow foreign-key, CHECK and
    primary-key violations and report them as the collision the caller expected.

    Corollary the caller must honour: **if ``conflict()`` cannot see every
    collision ``write()`` can cause, this does not help.** The re-resolve would
    answer ``None`` and the error would be re-raised — claiming no cause we
    cannot attribute is deliberate, but it means the caller's pre-check has to
    cover every index named in ``constraint``.

    Two structural details that are not rearrangeable:

    - **The savepoint must CONTAIN the flush.** ``begin_nested()`` flushes the
      whole session BEFORE establishing the SAVEPOINT
      (``SessionTransaction._take_snapshot``), so work staged earlier is already
      in the transaction when the savepoint opens and rolling back to the
      savepoint cannot touch it; ``_restore_snapshot`` expunges only the states
      that became new INSIDE it. That is what makes "recovery discards nothing
      but the failed write" guaranteed rather than incidental — and it is why
      ``with session.begin_nested(): write()`` with no flush would be purely
      decorative: the SAVEPOINT is released at ``with`` exit and the
      ``IntegrityError`` then surfaces at ``commit()``, outside the savepoint,
      with the transaction already aborted and the earlier work already lost.
      A plain ``session.rollback()`` here destroys that earlier work outright
      (measured, and the reason ``public.py``'s previous hand-rolled recovery
      was a cautionary example rather than a template).
    - **The ``except`` sits OUTSIDE the ``with``.** By the time ``conflict()``
      re-runs, the context manager's ``__exit__`` has already issued
      ``ROLLBACK TO SAVEPOINT``, so the transaction is usable. An ``except``
      nested inside the ``with`` would re-resolve on an aborted transaction.

    Why this shape and not the obvious alternatives:

    - A **decorator** cannot work. It sits outside ``with get_db_session()``, so
      by the time it catches, the session is closed: it can neither roll back the
      savepoint nor re-resolve the winner. It could not reproduce the pre-check's
      answer without a constraint→message registry, which re-creates the very
      divergence being fixed somewhere else. And it would widen the catch to
      ``IntegrityError``\\ s raised by every statement in the view.
    - A **bare context manager** exposing ``guard.conflicted`` keeps control flow
      honest, but forces the caller to write its polite answer twice, once per
      arm. That duplication is precisely what the bug is made of.
    - This helper must **never produce the response itself**, for the same
      registry reason — which is also why it takes no Flask import: all the
      polite answers callers return (a ``(jsonify(...), 409)``, a
      ``redirect(...)``, a ``render_template(...)``, an ORM row) are values it
      never inspects.

    ``write`` is a callable rather than a row because not every contested write
    is an INSERT — one caller's contested statement is an UPDATE of an already
    dirty row.
    """
    answer = conflict()
    if answer is not None:
        return answer

    names = (constraint,) if isinstance(constraint, str) else constraint
    try:
        with session.begin_nested():
            write()
            session.flush()
    except IntegrityError as exc:
        violated = next((name for name in names if is_constraint_violation(exc, name)), None)
        if violated is None:
            raise
        logger.info("Lost the %s race; re-resolving to the winner", violated)
        answer = conflict()
        if answer is None:
            raise
        return answer
    return None

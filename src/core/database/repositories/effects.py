"""Effects a transaction owns: deferred until it commits, or suppressed for a preview.

The unit of work already decides whether a request's WRITES survive — ``dry_run``
rolls back instead of committing (``BaseUoW``). This module extends that single
decision to the effects a rollback cannot reach: an outbound HTTP call, a job
handed to a background executor, a notification. Those are not undone by
discarding a transaction, so before this they were each gated by a hand-placed
``if dry_run`` at their own call site — nine of them, of which four shipped
ungraded and one sat on the wrong condition chain entirely (#1970).

Two kinds, split by whether the caller needs the result:

``register_after_commit``
    The result is not read inline (a submitted job, a notification). Queue it;
    it runs only if the owning transaction commits. A preview's rollback
    discards the queue, so the call site asks nothing about ``dry_run``.

``outbound``
    The result builds the response (a creative-agent render). It cannot be
    deferred, so it is suppressed instead, and the preview stand-in is returned.
    The flag is read from the transaction, never threaded through the call site.

State lives in ``session.info``, not on the unit of work, because the call sites
that need it hold a REPOSITORY and never the UoW -- and because a caller can
hand its open unit to another stage (``_process_assignments``), which a
UoW-attribute queue would miss.

Why not ``SessionEvents.after_commit``: SQLAlchemy's listener fires on the
session, so ordering across registrations is implicit, discard-on-rollback has
to be arranged separately, per-effect failure policy has nowhere to live, and
listeners would need removing from a long-lived scoped session. The explicit
FIFO here costs a few lines and answers all four.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_STACK_KEY = "adcp_effect_scopes"

#: How many times a draining effect may queue another before we stop and shout.
#: A deferred effect legitimately registers a follow-up; an effect that queues
#: itself would spin forever, and spinning silently is worse than failing.
_MAX_DRAIN_ROUNDS = 10

T = TypeVar("T")


@dataclass(frozen=True)
class _DeferredEffect:
    """One queued effect and what to do when it raises.

    ``fatal`` is per-effect on purpose. The three sites this seam generalizes
    disagree today -- the Slack branch swallows, the webhook and the audit-log
    write propagate -- and folding them into one uniform policy as a side effect
    of a refactor would silently lose an audit trail. The default is to swallow:
    an effect that runs AFTER a successful commit must not turn a request that
    already succeeded into an error.
    """

    #: Named ``run``, not ``fn``: a repo invariant (``check_repo_invariants``)
    #: forbids attribute-``fn`` calls anywhere in ``src/``, because that spelling
    #: is how a FastMCP tool's undecorated function gets invoked past the
    #: transport boundary. The rule is a substring match over the raw line with
    #: no allowlist -- so this callable field carries the other name rather than
    #: an exemption, and this comment cannot spell the banned form either.
    run: Callable[[], Any]
    label: str
    fatal: bool = False


@dataclass
class _EffectScope:
    """One transaction's queue and its preview flag."""

    preview: bool
    queue: list[_DeferredEffect] = field(default_factory=list)


def _scopes(session: Session) -> list[_EffectScope]:
    return session.info.get(_STACK_KEY) or []


def _current_scope(session: Session) -> _EffectScope | None:
    scopes = _scopes(session)
    return scopes[-1] if scopes else None


def begin_effects(session: Session, *, preview: bool) -> None:
    """Start a transaction's effect scope. Called by the unit of work on entry.

    A STACK, not a single slot. ``get_db_session`` hands out a ``scoped_session``,
    so a unit of work opened while another is already open on this thread gets
    the SAME ``Session`` object -- and a single slot would let the inner one
    overwrite the outer's queue on entry and pop it on exit, silently discarding
    the outer's effects and leaving its later ``after_commit`` calls to raise
    "outside a unit of work". Each unit pushes and pops its own scope instead.
    """
    session.info.setdefault(_STACK_KEY, []).append(_EffectScope(preview=preview))


def end_effects(session: Session) -> None:
    """Pop this transaction's scope. Called from the unit of work's ``finally``.

    Pops only what this unit pushed, so an enclosing unit keeps its queue. The
    key itself is removed with the last scope, so a scoped ``Session`` reused by
    a later request never inherits state.
    """
    scopes = _scopes(session)
    if scopes:
        scopes.pop()
    if not scopes:
        session.info.pop(_STACK_KEY, None)


@contextmanager
def effect_savepoint(session: Session) -> Iterator[None]:
    """Bind queued effects to a SAVEPOINT: discard them if it rolls back.

    Without this the module's central claim is false at the only granularity
    ``sync_creatives`` actually isolates at. It processes each creative inside
    ``begin_nested()``, and a savepoint rollback does not touch ``session.info``
    -- so an effect queued before the failure (the AI-review submit is the FIRST
    thing the ai-powered branch registers) survived, the outer transaction committed
    the OTHER creatives, and the background job then ran for a creative the buyer
    was told had ``action="failed"``, writing its status back onto the untouched
    row. Truncating to the entry mark makes "rolled back" mean the same thing for
    effects as it does for writes.
    """
    scope = _current_scope(session)
    mark = len(scope.queue) if scope else 0
    try:
        yield
    except BaseException:
        if scope is not None:
            del scope.queue[mark:]
        raise


def register_after_commit(
    session: Session, fn: Callable[[], Any], *, label: str = "effect", fatal: bool = False
) -> None:
    """Queue *fn* to run after this transaction commits.

    Raises if no transaction owns it: an effect registered outside a unit of
    work would never be drained, and silently dropping it is exactly the class
    of quiet failure this seam exists to remove.
    """
    scope = _current_scope(session)
    if scope is None:
        raise RuntimeError(
            f"after_commit({label!r}) was called outside a unit of work, so nothing owns the effect "
            "and nothing would ever run it. Register it inside the `with ...UoW(...)` block whose "
            "commit should release it."
        )
    scope.queue.append(_DeferredEffect(run=fn, label=label, fatal=fatal))


def is_preview(session: Session) -> bool:
    """Will the transaction owning this session roll back on a clean exit?

    True if ANY enclosing scope is a preview: an inner live unit nested in an
    outer preview still has all its writes discarded by the outer rollback, so
    its effects must be suppressed too. Absence means live -- a session with no
    effect scope is not inside a preview.
    """
    return any(scope.preview for scope in _scopes(session))


def drain_after_commit(session: Session) -> None:
    """Run this transaction's queued effects, in registration order.

    Drains until the queue is EMPTY, not once over a snapshot: an effect is free
    to register another (the docstring said so while the snapshot loop dropped
    it, and ``end_effects`` then popped the scope -- nothing ran it, nothing
    logged it, which is the quiet failure ``register_after_commit`` raises to
    prevent). ``_MAX_DRAIN_ROUNDS`` bounds a cycle instead of hanging.

    MUST be called AFTER the session is closed. Every effect here opens its own
    unit of work, and the session is scoped -- so running one while the outer
    session is still open would let the inner unit close and de-register that
    very session out from under the outer exit.
    """
    scope = _current_scope(session)
    if scope is None:
        return
    for _ in range(_MAX_DRAIN_ROUNDS):
        queued, scope.queue = scope.queue, []
        if not queued:
            return
        for effect in queued:
            try:
                effect.run()
            except Exception:
                if effect.fatal:
                    raise
                logger.exception("after-commit effect %r failed; the committed request is unaffected", effect.label)
    raise RuntimeError(
        f"after-commit effects still queueing new effects after {_MAX_DRAIN_ROUNDS} rounds "
        f"(pending: {[e.label for e in scope.queue]}) — an effect is registering itself."
    )


class SessionEffectsMixin:
    """Gives a repository the effect boundary of the transaction it belongs to.

    ONE implementation, mixed in -- not a pair of methods copied onto each
    repository. Repositories share no base class here, so the first copy would
    become the template for the next.

    Expects ``self._session``.
    """

    _session: Session

    def after_commit(self, fn: Callable[[], Any], *, label: str = "effect", fatal: bool = False) -> None:
        """Queue an effect to run only if this transaction commits."""
        register_after_commit(self._session, fn, label=label, fatal=fatal)

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """A savepoint that owns its effects: both are discarded if it rolls back.

        The only savepoint API a repository exposes, so the pairing cannot be
        forgotten at a call site -- a bare ``session.begin_nested()`` would roll
        back the row's writes while its queued effects survived to the outer
        commit.
        """
        with effect_savepoint(self._session), self._session.begin_nested():
            yield

    def outbound(self, call: Callable[[], T], *, preview_result: T | None = None) -> T | None:
        """Perform *call*, or return *preview_result* when this transaction is a preview.

        For effects whose RESULT builds the response, which therefore cannot be
        deferred. The suppression decision is read from the transaction here,
        once, instead of at each call site.
        """
        if self.is_preview:
            return preview_result
        return call()

    @property
    def is_preview(self) -> bool:
        """Whether the owning transaction will roll back on a clean exit."""
        return is_preview(self._session)

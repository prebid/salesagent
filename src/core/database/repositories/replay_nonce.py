"""Atomic claims on RFC 9421 ``(keyid, nonce)`` pairs (#1291 A4, salesagent-z6nr.10).

Every statement against ``adcp_replay`` lives here — the structural guard forbids
raw model queries outside the repository layer, and this table has no tenant scope
to lose (see :class:`~src.core.database.models.ReplayNonce`), so the thing a stray
query would drop instead is the atomicity.

Three properties a reviewer will want explained, all deliberate:

**1. Why ``INSERT ... ON CONFLICT DO UPDATE ... WHERE ... RETURNING`` and not the
house idiom.** The established shape in this codebase is a unique index plus
``INSERT`` / ``except IntegrityError`` (``src/core/tools/media_buy_create.py:1892``),
and this is the first ``on_conflict`` in ``src/``. The deviation is required, not
stylistic: a claim must distinguish "no row" from "an EXPIRED row" and re-claim the
expired one ATOMICALLY. Catch-IntegrityError cannot — on conflict it would have to
issue a SECOND statement to test expiry, and between those two statements two
workers can both decide the nonce is free. That gap is exactly the bug the SDK's own
``PgReplayStore`` ships (``adcp/signing/pg/replay_store.py:215,221``: a bare
``SELECT`` in ``seen()`` and a separate upsert in ``remember()``, whose class
docstring claims "Postgres provides the cross-instance locking" — true of the write,
false of the check). Do not "simplify" this back to probe-then-insert; that reopens
a replay bypass.

**2. Why this repository COMMITS.** Repositories here usually leave commits to the
UoW, but the precedent for committing is narrow and existing
(``src/core/database/repositories/creative.py:248,509``). This case needs it: no UoW
spans a signature check — ``BaseUoW`` is ``(tenant_id)``-scoped and this table is
deployment-wide — and the claim must be durable and visible to SIBLING WORKERS
before the response is sent, or a nonce accepted on worker A stays claimable on
worker B for the whole signature window.

**3. READ COMMITTED is assumed.** The atomic claim relies on Postgres's speculative
insertion retry, which is READ COMMITTED behavior. Verified as this deployment's
setting (``default_transaction_isolation = read committed``; no ``isolation_level=``
in ``database_session.py``). Under REPEATABLE READ or SERIALIZABLE the same statement
can raise a 40001 serialization failure that nothing here retries — so a future
global isolation change must arrive as a reviewed decision, not as intermittent 500s
on signed requests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Select, delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import Executable

from src.core.database.models import ReplayNonce


def _expires_at(ttl_seconds: float) -> ColumnElement:
    """``now() + <ttl> seconds``, computed by the SERVER.

    The database clock is the only time authority several workers share, so every
    expiry — written here, read by the ``expires_at > now()`` filters — is measured
    there and never against a worker's Python clock.

    ``make_interval(secs => :ttl)`` is the obvious spelling and is NOT available:
    SQLAlchemy's ``func`` has no named-argument notation, so
    ``func.make_interval(secs=ttl)`` raises ``TypeError`` in ``Function.__init__``.
    """
    return func.now() + sa.cast(sa.func.concat(ttl_seconds, " seconds"), sa.Interval())


class ReplayNonceRepository:
    """Single-statement operations on the deployment-wide replay cache.

    Args:
        session: SQLAlchemy session (caller manages its lifecycle). Deliberately a
            plain ``Session`` and not a ``BaseUoW``: the UoW is ``(tenant_id)``-scoped
            and this table has no tenant dimension.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(self, keyid: str, nonce: str, claim_ttl_seconds: float) -> bool:
        """Claim ``(keyid, nonce)``; return True iff it was ALREADY live (a replay).

        The accept decision and the record of it are ONE statement, which is the
        whole point of A4::

            INSERT INTO adcp_replay (keyid, nonce, expires_at)
            VALUES (:keyid, :nonce, now() + :ttl)
            ON CONFLICT (keyid, nonce) DO UPDATE SET expires_at = EXCLUDED.expires_at
             WHERE adcp_replay.expires_at <= now()
            RETURNING 1

        * no row          -> the INSERT fires   -> a row is returned -> False (claimed)
        * row, EXPIRED    -> the UPDATE's WHERE holds -> a row is returned -> False (re-claimed)
        * row, still LIVE -> the UPDATE's WHERE fails -> nothing returned -> True (replay)

        Postgres serialises concurrent ``INSERT ... ON CONFLICT`` on the primary key,
        so of N workers racing for one nonce exactly one can ever be told False.
        """
        return not self._upsert_expiry(
            keyid,
            nonce,
            claim_ttl_seconds,
            conflict_when=lambda _excluded: ReplayNonce.expires_at <= func.now(),
        )

    def extend(self, keyid: str, nonce: str, ttl_seconds: float) -> None:
        """Lengthen an existing claim to ``ttl_seconds``, never shorten it.

        The only-extend predicate is the SDK's (``WHERE EXCLUDED.expires_at >
        adcp_replay.expires_at``), kept so this store behaves like every other
        ``ReplayStore``: a legitimate in-window retry refreshes the entry, and a
        shorter TTL arriving late can never open a replay window by cutting a live
        claim short.
        """
        self._upsert_expiry(
            keyid,
            nonce,
            ttl_seconds,
            conflict_when=lambda excluded: ReplayNonce.expires_at < excluded,
        )

    def at_or_above_cap(self, keyid: str, cap: int) -> bool:
        """True iff ``keyid`` has at least ``cap`` LIVE entries.

        ``SELECT 1 ... WHERE keyid = :k AND expires_at > now() OFFSET :cap-1 LIMIT 1``
        — Postgres stops scanning as soon as ``cap`` rows have been seen, so the cost
        is bounded by the cap and not by the signer's row count. That matters because
        this runs at verifier step 9a on every signed request BEFORE crypto verify,
        precisely so an abusive signer is cheap to reject; ``COUNT(*)`` at the spec's
        1,000,000 cap would make step 9a the most expensive path in the verifier.
        Served by the ``(keyid, expires_at)`` index the migration ships.

        ``OFFSET cap - 1``, not ``OFFSET cap``: capacity is reached at ``count >=
        cap`` (matching ``InMemoryReplayStore.at_capacity``, ``adcp/signing/replay.py:67``),
        so the row at zero-based index ``cap - 1`` is the one whose existence answers
        the question.
        """
        if cap <= 0:
            return True
        stmt: Select[Any] = (
            select(literal_column("1"))
            .select_from(ReplayNonce)
            .where(ReplayNonce.keyid == keyid, ReplayNonce.expires_at > func.now())
            .offset(cap - 1)
            .limit(1)
        )
        return self._session.execute(stmt).first() is not None

    def forget(self, keyid: str, nonce: str) -> None:
        """Delete EXACTLY the ``(keyid, nonce)`` row, if it is there.

        Scoped to one primary-key pair, never to a keyid and never to the table. This
        table has no tenant dimension (see the module docstring), so "clear the cache
        for this keyid" is a DEPLOYMENT-WIDE wipe: on a shared database it erases the
        live claims of every other suite and every sibling worker, and a signer sitting
        at its per-keyid cap would suddenly be under it. A conformance harness needs to
        drop the exact pairs it created and nothing else, and raw SQL in a fixture is
        not an option (``tests/unit/test_architecture_repository_pattern.py``), so the
        narrow delete lives here rather than being widened at the call site.

        Deleting an absent pair is a no-op, which is what makes it safe to call both
        BEFORE and AFTER a case.

        Like every other write here it COMMITS — for the same reason: no UoW spans a
        signature check, and a claim that is not visible to sibling workers is not a
        claim.
        """
        stmt = delete(ReplayNonce).where(ReplayNonce.keyid == keyid, ReplayNonce.nonce == nonce)
        self._session.execute(stmt.execution_options(synchronize_session=False))
        self._session.commit()

    def reap(self, limit: int) -> int:
        """Delete up to ``limit`` expired rows; return how many went. Best-effort.

        Correctness never depends on this running: every read filters
        ``expires_at > now()``, so a dead row is already indistinguishable from an
        absent one. Only storage growth does.

        Bounded by ``ctid IN (SELECT ... LIMIT :limit)`` because this table is
        deployment-wide, not tenant-scoped: an unbounded DELETE would take locks
        across every counterparty's rows on the same index concurrent claims are
        inserting into. The caller must run this in its OWN transaction, after the
        claim has committed — see ``PostgresReplayStore._maybe_reap_expired``.
        """
        doomed = (
            select(literal_column("ctid"))
            .select_from(ReplayNonce)
            .where(ReplayNonce.expires_at <= func.now())
            .limit(limit)
            .scalar_subquery()
        )
        stmt = delete(ReplayNonce).where(literal_column("ctid").in_(doomed))
        result = self._session.execute(stmt.execution_options(synchronize_session=False))
        self._session.commit()
        # rowcount is typed loosely on the base Result protocol; the concrete
        # CursorResult a DELETE returns always carries it. Same accessor as
        # IdempotencyAttemptRepository.expire_old.
        return int(getattr(result, "rowcount", 0) or 0)

    def _upsert_expiry(
        self,
        keyid: str,
        nonce: str,
        ttl_seconds: float,
        *,
        conflict_when: Callable[[ColumnElement], ColumnElement[bool]],
    ) -> bool:
        """Run the shared claim/extend statement; return True iff a row was written.

        ``claim`` and ``extend`` are the same INSERT with the same ``SET`` and differ
        only in the predicate guarding the conflict branch — expired-only for the
        claim, longer-only for the extension — so the statement has one home. It
        COMMITS: see the module docstring.
        """
        insert_stmt = pg_insert(ReplayNonce).values(keyid=keyid, nonce=nonce, expires_at=_expires_at(ttl_seconds))
        excluded_expires_at = insert_stmt.excluded.expires_at
        stmt: Executable = insert_stmt.on_conflict_do_update(
            index_elements=["keyid", "nonce"],
            set_={"expires_at": excluded_expires_at},
            where=conflict_when(excluded_expires_at),
        ).returning(literal_column("1"))
        wrote = self._session.execute(stmt).first() is not None
        self._session.commit()
        return wrote

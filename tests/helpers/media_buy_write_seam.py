"""One oracle for what a media-buy status move is responsible for carrying.

A status move is a MUTATION of the buy, so the write door owes two things besides
the status itself: ``revision`` (the buyer's optimistic-concurrency token, which MUST
strictly increase on every mutation) and ``confirmed_at`` (the instant the seller
committed, stamped write-once the first time the buy reaches a committed status).
A caller that writes ``media_buy.status`` directly moves the buy without either, and
the buyer is handed a token that never changed.

That obligation was asserted in six places, in five spellings, with the sentence "a
status move must bump revision by exactly 1" written out verbatim each time — and
with three different answers to the question that actually decides whether the
assertion is real: WHICH session to read the row back through. Production commits in
its own session, so a reader on a stale identity map cheerfully returns the
pre-call row and the assertion passes against the state before the write.

This module owns that decision once, so a site cannot be subtly wrong about it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple


class MediaBuyState(NamedTuple):
    """The persisted columns a status move is responsible for."""

    status: str | None
    revision: int | None
    confirmed_at: datetime | None
    # Also written by the same door (``update_status(..., approved_by=..., approved_at=...)``),
    # so a caller checking who approved, and when, reads them from the same fresh row as
    # everything else rather than opening a second session to ask.
    approved_by: str | None = None
    approved_at: datetime | None = None

    @classmethod
    def of(cls, media_buy: Any) -> MediaBuyState:
        """The columns, off a row that is already loaded and fresh."""
        if media_buy is None:
            return cls(status=None, revision=None, confirmed_at=None, approved_by=None, approved_at=None)
        return cls(
            status=media_buy.status,
            revision=media_buy.revision,
            confirmed_at=media_buy.confirmed_at,
            approved_by=media_buy.approved_by,
            approved_at=media_buy.approved_at,
        )


def read_media_buy_state(tenant_id: str, media_buy_id: str, *, session: Any = None) -> MediaBuyState:
    """Read the three columns, from a view that can actually see the write.

    Pass ``session`` to read through a test's own session — it is expired first, so
    the identity map cannot serve a row from before production's commit. Pass nothing
    and a fresh session is opened, which has the same property by construction.

    A missing row returns all-``None`` rather than raising: several callers assert
    deletion, and "the row is gone" is an answer, not a failure to read.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    if session is not None:
        session.expire_all()
        row = session.scalars(select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)).first()
        return MediaBuyState.of(row)

    # No session supplied: go through the harness UoW rather than get_db_session().
    # It opens its own session — which is the property callers need, since production
    # commits elsewhere — and it keeps this helper on the repository path the
    # repository-pattern guard requires of test code (CLAUDE.md Pattern #8).
    from src.core.database.repositories.uow import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        return MediaBuyState.of(uow.media_buys.get_by_id(media_buy_id))


def assert_revision_advanced(
    before: MediaBuyState, after: MediaBuyState, *, bumps: int = 1, subject: str = "the media buy"
) -> None:
    """Assert a mutation advanced ``revision`` by EXACTLY ``bumps``, nothing else read.

    The revision-only entry point for callers that mutate a buy without moving its
    status (a budget write, a pause). ``bumps`` is an EXACT delta, not a floor: "revision
    increased" would pass a double-write that bumped twice, which skips a value on the
    buyer's token and reports a conflict against a revision that never crossed the wire.
    Same owner, same session discipline as :func:`assert_status_move_carried_bookkeeping`;
    read both states through :func:`read_media_buy_state`.
    """
    assert after.revision == (before.revision or 0) + bumps, (
        f"{subject} advanced revision {before.revision} -> {after.revision}; a mutation must "
        f"advance it by exactly {bumps}"
    )


def assert_status_move_carried_bookkeeping(
    before: MediaBuyState,
    after: MediaBuyState,
    *,
    expected_status: str,
    bumps: int = 1,
    confirms: bool | None = None,
    subject: str = "the media buy",
) -> None:
    """Assert a status move landed AND carried its bookkeeping.

    ``bumps`` is an EXACT delta, not a floor. "Revision increased" would pass a
    double-write that bumped twice — which is a real defect (the buyer's token skips a
    value and their next call reports a conflict against a revision that never existed
    on the wire).

    ``confirms``:
    * ``True``  — this move is the first commitment: ``confirmed_at`` must be stamped.
    * ``False`` — this move must not stamp one (the buy is not committed).
    * ``None``  — the buy was already stamped and the stamp must be UNCHANGED, which is
      the write-once contract: ``confirmed_at`` records the FIRST commitment, not the
      most recent transition.
    """
    assert after.status == expected_status, (
        f"{subject} should have moved to {expected_status!r}, but the persisted status is {after.status!r}"
    )
    assert after.revision == (before.revision or 0) + bumps, (
        f"{subject} moved {before.status!r} -> {after.status!r} but revision went "
        f"{before.revision} -> {after.revision}; a status move must bump revision by exactly {bumps}"
    )
    if confirms is True:
        assert after.confirmed_at is not None, (
            f"{subject} moved to the seller-confirmed status {expected_status!r} without stamping confirmed_at"
        )
    elif confirms is False:
        assert after.confirmed_at is None, (
            f"{subject} moved to {expected_status!r}, which is not a committed status, yet confirmed_at "
            f"was stamped {after.confirmed_at!r} — that mints a seller commitment the seller never made"
        )
    else:
        assert after.confirmed_at == before.confirmed_at, (
            f"{subject} moved to {expected_status!r} and rewrote the commitment instant "
            f"{before.confirmed_at} -> {after.confirmed_at}; confirmed_at is write-once and records the "
            f"FIRST commitment, not the most recent transition"
        )

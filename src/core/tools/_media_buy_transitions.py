"""What status a media buy's flight window implies.

One domain owner for the RULE, but not one granularity: the write side
(``resolve_flight_window_status``) takes a ``datetime`` and the read side
(``_media_buy_status.resolve_canonical_status``) takes a ``date``, so the two
disagree about a buy whose window opens or closes part-way through a day. Calling
this "one domain owner" without that qualification invites a reader to assume the
two agree on every input; they agree on whole days only.

This is a DOMAIN rule, not presentation: "given this buy's flight window, and whether
its creatives are approved, what lifecycle state is it in?" The answer is written to
the ``media_buys.status`` column, so it belongs beside the vocabulary rather than
inside whichever route happened to need it.

It was implemented FOUR times — the scheduler (``media_buy_status_scheduler``), the
operations blueprint, the creatives blueprint, and
``admin/services/media_buy_readiness_service._compute_state`` — and the copies had
DIVERGED, which is the argument for this module existing.

Measured, so the next reader does not overestimate what has been absorbed:
``resolve_flight_window_status`` has TWO callers, ``media_buy_create`` and the
scheduler. Of the four implementations above, this module replaced ONE. The
operations blueprint, the creatives blueprint and ``_compute_state`` still
open-code it, and ``_compute_state`` is what the admin UI displays — so the
divergence this module exists to end is still live on the surface a human looks at.

* the creatives-blueprint copy returned only ``active`` or ``scheduled``, so a buy
  approved AFTER its flight end was stamped ``scheduled``: a finished campaign
  reported as one that has not started, in a column the wire projection reads;
* the operations copy had no notion of "no change", so it always wrote something;
* only the scheduler consulted creative approval before activating.

``resolve_flight_window_status`` is the whole rule. The transition GUARD — which
current statuses a caller is willing to move, and whether a write happens at all —
stays with the caller, because that genuinely differs: the scheduler sweeps
unattended and moves only pre-serving buys, while an admin approving a buy has
already decided to move it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.database.models import PersistedMediaBuyStatus
from src.core.utils.flight_time import utc_flight_end, utc_flight_start


def _aware(value: datetime | None) -> datetime | None:
    """A naive datetime read from the column is UTC; an aware one is converted."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def flight_window(media_buy) -> tuple[datetime, datetime] | None:
    """The buy's (start, end) in UTC, or ``None`` when it has no window.

    ``start_time``/``end_time`` win over ``start_date``/``end_date`` — the precise
    instants over the day they fall in — which is the rule all three copies already
    encoded, in three spellings.
    """
    start = _aware(media_buy.start_time) or (utc_flight_start(media_buy.start_date) if media_buy.start_date else None)
    end = _aware(media_buy.end_time) or (utc_flight_end(media_buy.end_date) if media_buy.end_date else None)
    if start is None or end is None:
        return None
    return start, end


def resolve_flight_window_status(
    media_buy,
    *,
    now: datetime,
    creatives_approved: bool,
) -> PersistedMediaBuyStatus | None:
    """The status this buy's flight window implies, or ``None`` if it implies nothing.

    ``None`` means "this rule has no opinion" — the buy has no flight window — and is
    distinct from every status: a caller decides for itself whether that means leave
    the row alone or fall back to something.

    Ordering is load-bearing. The end of the window is checked FIRST, because a buy
    past its end is ``completed`` whatever else is true of it — including a buy whose
    creatives were approved late, the case the creatives-blueprint copy got wrong by
    checking only "am I inside the window" and answering ``scheduled`` when it was not.

    ``creatives_approved`` gates only ACTIVATION. A buy inside its window with
    unapproved creatives is not serving, so it reports ``pending_creatives`` — the
    vocabulary's word for "approved but has no creatives" — rather than being called
    active on the strength of the calendar alone.
    """
    window = flight_window(media_buy)
    if window is None:
        return None
    start, end = window

    if now > end:
        return PersistedMediaBuyStatus.COMPLETED
    if now < start:
        return PersistedMediaBuyStatus.PENDING_START
    if not creatives_approved:
        return PersistedMediaBuyStatus.PENDING_CREATIVES
    return PersistedMediaBuyStatus.ACTIVE

"""The one place a webhook conclusion becomes a durable row.

Both senders end every arm -- refused, client error, exhausted, unexpected,
delivered -- by writing one ``webhook_delivery_log`` row and committing it, and
both must swallow a failure to do so: persistence here is observability, and a
DB error must not propagate out of a function contracted ``-> bool`` and turn a
webhook that WAS delivered into a failure, and upstream into a spurious retry.

That epilogue was written twice (GH #1802, review pattern #4 / AR-01)
-- once as ``protocol_webhook_service._conclude`` and once inline in
``webhook_delivery_service``. Two copies of a swallow is two chances to swallow
slightly differently, and they already did: only one rolled back.

Lives in ``src/services/`` rather than on ``DeliveryRepository``, whose contract
says the opposite in its own prose -- "Write methods add objects to the session
but never commit -- the caller (or UoW) handles commit/rollback at the boundary"
-- and rather than in ``src/core/webhooks/``, which is pure domain and would
open its first domain->persistence edge to take a ``Session``. Both callers
already live here and already depend on the repository, so this adds no edge in
either direction.

Takes the session rather than opening one, and does NOT swallow: the two senders
differ on ownership (one opens its own per conclusion, one is handed a shared
session it commits per config inside a loop) AND on where their swallow sits
relative to that session, which is not this function's to erase.

Folding the swallow in here as well was tried and reverted. It moved site A's
catch from OUTSIDE its ``with get_db_session()`` to inside, so a write failing
with ``OperationalError`` no longer reached that context manager's own handler --
and that handler does more than roll back: it calls ``scoped.remove()`` and sets
the process-wide ``_is_healthy = False`` fail-fast trip
(``database_session.py:227-236``). Losing that trip is defensible policy -- a
best-effort observability write arguably should not halt the process -- but it is
a behavior change, and this move is a type relocation. So the shared part stops
at the write.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.core.database.repositories.delivery import DeliveryRepository
from src.core.webhooks.delivery import WebhookDeliveryOutcome, WebhookTaskContext

logger = logging.getLogger(__name__)


def record_conclusion(
    session: Session,
    *,
    tenant_id: str,
    ctx: WebhookTaskContext,
    log_id: str,
    webhook_url: str,
    outcome: WebhookDeliveryOutcome,
    response_time_ms: int | None = None,
) -> None:
    """Write and commit one delivery row. Raises whatever the write raises.

    Each caller owns its own failure policy, because they genuinely differ: one
    rolls back a shared session it must keep usable for the next config in a
    loop, the other lets the exception reach ``get_db_session`` so the connection
    handler and its fail-fast trip still run. Both then swallow -- persistence
    here is observability, and a DB error must not propagate out of a function
    contracted ``-> bool`` and turn a delivered webhook into a retry.
    """
    DeliveryRepository(session, tenant_id).record_outcome(
        ctx=ctx,
        log_id=log_id,
        webhook_url=webhook_url,
        outcome=outcome,
        response_time_ms=response_time_ms,
    )
    session.commit()

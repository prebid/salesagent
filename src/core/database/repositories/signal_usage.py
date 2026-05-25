"""SignalUsage repository — count live media buys referencing a TenantSignal.

A ``signal_id`` lands inside the create_media_buy request JSON at
``packages[*].targeting_overlay.audience_include`` and
``audience_exclude`` (and defensively at the request top level for the
update-shape, where AdCP also accepts these keys). We persist that
payload verbatim on ``MediaBuy.raw_request`` (JSONB). To answer "which
signals are buyers actually referencing right now?" we scan the live
subset of buys for the tenant and walk the JSON in Python.

**What counts as live?** Anything *not* in a terminal status. We use an
exclusion list — terminal = ``{completed, canceled, cancelled, rejected,
failed}``. Everything else counts:

- ``active`` / ``approved`` — serving now
- ``paused`` — temporarily not serving but will resume
- ``pending_creatives`` / ``pending_start`` / ``pending_approval`` —
  waiting on something, will serve once it clears
- ``draft`` — not yet committed but the reference is recorded

Inverting from an allowlist to a denylist matches the safety invariant:
a delete should be blocked when a buy *could* serve, not only when it
*is* serving. New statuses introduced by future PRs default to "live"
unless explicitly added to the terminal set.

Why Python iteration and not JSONB path queries: the rest of the
codebase walks ``raw_request`` in Python (see
``src/core/tools/media_buy_delivery.py``) and operates at publisher
scale — typically <1000 live buys per tenant. Bulk usage still uses a
single SELECT + dict walk; one-off delete checks use targeted JSONB
containment so they do not rescan unrelated buys.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import MediaBuy


@dataclass(frozen=True)
class SignalUsage:
    """Per-signal usage snapshot over a tenant's live media buys."""

    active_buy_count: int
    last_referenced_at: datetime | None


# Terminal statuses — signal references on these buys are historical
# and don't block delete. Anything else is treated as live (see module
# docstring). ``cancelled`` and ``canceled`` are both listed because
# the codebase isn't fully consistent on spelling.
_TERMINAL_STATUSES: tuple[str, ...] = (
    "completed",
    "canceled",
    "cancelled",
    "rejected",
    "failed",
)


def _raw_request_contains_signal(signal_id: str):
    """Return JSONB containment predicates for every supported overlay shape."""
    payloads = (
        {"targeting_overlay": {"audience_include": [signal_id]}},
        {"targeting_overlay": {"audience_exclude": [signal_id]}},
        {"packages": [{"targeting_overlay": {"audience_include": [signal_id]}}]},
        {"packages": [{"targeting_overlay": {"audience_exclude": [signal_id]}}]},
    )
    return or_(*(MediaBuy.raw_request.contains(payload) for payload in payloads))


def _iter_referenced_signal_ids(raw_request: dict[str, Any] | None) -> Iterable[str]:
    """Yield every ``signal_id`` referenced by a media-buy request payload.

    Walks both shapes the AdCP request schema admits:

    - ``packages[*].targeting_overlay.{audience_include,audience_exclude}``
      — the canonical create_media_buy shape.
    - ``targeting_overlay.{audience_include,audience_exclude}`` at the
      request top level — accepted by ``update_media_buy`` and any caller
      that pre-flights with a buy-level overlay. Defensive walk; closes
      the gap one schema-shape change away from being a real bug.

    Handles missing keys defensively. Yields duplicates: the same signal
    can appear in multiple packages of one buy — callers dedupe per buy.
    """
    if not raw_request:
        return

    def _walk_overlay(overlay: dict[str, Any] | None) -> Iterable[str]:
        if not overlay:
            return
        for field in ("audience_include", "audience_exclude"):
            for sid in overlay.get(field) or []:
                if isinstance(sid, str) and sid:
                    yield sid

    # Top-level overlay (update_media_buy / convenience shape)
    yield from _walk_overlay(raw_request.get("targeting_overlay"))
    # Per-package overlay (create_media_buy canonical shape)
    for pkg in raw_request.get("packages") or []:
        yield from _walk_overlay((pkg or {}).get("targeting_overlay"))


class SignalUsageRepository:
    """Tenant-scoped scan of media buys to count signal references."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def usage_index(self) -> dict[str, SignalUsage]:
        """Return ``signal_id -> SignalUsage`` for every signal referenced
        by an active buy in this tenant.

        Signals that no active buy references are absent from the dict —
        callers treat missing as zero references. Last-referenced is the
        max ``MediaBuy.updated_at`` over buys that reference the signal.
        """
        stmt = select(MediaBuy.raw_request, MediaBuy.updated_at).where(
            MediaBuy.tenant_id == self._tenant_id,
            MediaBuy.status.notin_(_TERMINAL_STATUSES),
        )
        counts: dict[str, int] = {}
        last_seen: dict[str, datetime] = {}
        for raw_request, updated_at in self._session.execute(stmt).all():
            seen_in_buy: set[str] = set()
            for sid in _iter_referenced_signal_ids(raw_request):
                if sid in seen_in_buy:
                    continue
                seen_in_buy.add(sid)
                counts[sid] = counts.get(sid, 0) + 1
                prior = last_seen.get(sid)
                if updated_at is not None and (prior is None or updated_at > prior):
                    last_seen[sid] = updated_at
        return {sid: SignalUsage(active_buy_count=counts[sid], last_referenced_at=last_seen.get(sid)) for sid in counts}

    def count_references(self, signal_id: str) -> int:
        """Count active media buys referencing ``signal_id``.

        Uses targeted JSONB containment over the supported AdCP overlay
        shapes. The signals list still uses :meth:`usage_index` because it
        needs all signals at once; a direct delete check only needs one ID.
        """
        if not signal_id:
            return 0
        stmt = (
            select(func.count())
            .select_from(MediaBuy)
            .where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.status.notin_(_TERMINAL_STATUSES),
                _raw_request_contains_signal(signal_id),
            )
        )
        return int(self._session.scalar(stmt) or 0)

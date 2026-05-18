"""InventoryReviewState repository — tenant-scoped review-state for synced inventory.

Backs the Job 1 (Discovery) coverage analytics for #485. Tracks whether
each synced ad unit / placement has been reviewed and either bundled or
explicitly skipped.

Core invariant: every query includes ``tenant_id`` in the WHERE clause.

The ``status`` is a state machine maintained by two write paths:

* **Bundle save-time** (``inventory_profiles`` blueprint) calls
  :meth:`sync_in_bundle_status` to mark the union of referenced ad units /
  placements as ``in_bundle``, and to demote previously-in-bundle entries
  that aren't referenced anymore back to ``pending``.
* **Operator action** calls :meth:`mark_skipped` (or, for bulk,
  :meth:`mark_skipped_bulk`) to mark entries ``explicitly_skipped``.
  Adding a skipped entity to a bundle later promotes it back to
  ``in_bundle`` automatically.

Adapter-agnostic by design: the table key is
``(tenant_id, adapter, entity_type, external_id)``. GAM ad units and
placements are the first consumers; FreeWheel and SpringServe follow as
their sync surfaces land. The ``entity_type`` slot is also reserved for
#486's ``signal_candidate`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import InventoryReviewState

Status = Literal["pending", "in_bundle", "explicitly_skipped"]
VALID_STATUSES: frozenset[str] = frozenset({"pending", "in_bundle", "explicitly_skipped"})


class InventoryReviewStateRepository:
    """Tenant-scoped data access for InventoryReviewState."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ---------------------------------------------------------------- reads

    def coverage_summary(self, adapter: str, entity_type: str) -> dict[str, int]:
        """Counts grouped by status for (tenant, adapter, entity_type).

        Returns a dict keyed by status — always includes all three known
        statuses (defaulting missing ones to 0) so callers don't have to
        ``get(...)`` defensively. Adapter and entity_type filter the
        GROUP BY so the dashboard can ask "how many GAM ad units" without
        rolling in placements.
        """
        rows = self._session.execute(
            select(InventoryReviewState.status, func.count())
            .where(
                InventoryReviewState.tenant_id == self._tenant_id,
                InventoryReviewState.adapter == adapter,
                InventoryReviewState.entity_type == entity_type,
            )
            .group_by(InventoryReviewState.status)
        ).all()
        counts: dict[str, int] = dict.fromkeys(VALID_STATUSES, 0)
        for status, count in rows:
            counts[status] = count
        counts["total"] = sum(counts[s] for s in VALID_STATUSES)
        return counts

    # --------------------------------------------------------------- writes

    def sync_in_bundle_status(
        self,
        adapter: str,
        entity_type: str,
        in_bundle_ids: Iterable[str],
    ) -> None:
        """Reconcile the ``in_bundle`` set for (tenant, adapter, entity_type).

        Three operations in one call:

        * For each id in ``in_bundle_ids`` not already tracked, insert a
          row with status ``in_bundle``.
        * For each id in ``in_bundle_ids`` already tracked with status
          ``pending`` or ``explicitly_skipped``, promote it to
          ``in_bundle`` (a bundle reference wins).
        * For each row currently ``in_bundle`` whose external_id is no
          longer referenced, demote it to ``pending`` (we don't auto-skip
          orphans — that requires an operator decision).

        Called by the inventory_profiles blueprint after any bundle
        mutation. The full reconciliation is intentional: bundle config
        is a JSON blob, so we can't compute the delta cheaply.
        """
        in_bundle_set = set(in_bundle_ids)

        # Promote / insert ``in_bundle`` rows.
        if in_bundle_set:
            now = datetime.now(UTC)
            stmt = pg_insert(InventoryReviewState).values(
                [
                    {
                        "tenant_id": self._tenant_id,
                        "adapter": adapter,
                        "entity_type": entity_type,
                        "external_id": eid,
                        "status": "in_bundle",
                        "updated_at": now,
                    }
                    for eid in in_bundle_set
                ]
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_inventory_review_state",
                set_={"status": "in_bundle", "updated_at": now},
            )
            self._session.execute(stmt)

        # Demote rows that *were* in_bundle but no longer are.
        demote_stmt = select(InventoryReviewState).where(
            InventoryReviewState.tenant_id == self._tenant_id,
            InventoryReviewState.adapter == adapter,
            InventoryReviewState.entity_type == entity_type,
            InventoryReviewState.status == "in_bundle",
        )
        if in_bundle_set:
            demote_stmt = demote_stmt.where(InventoryReviewState.external_id.notin_(in_bundle_set))
        for orphan in self._session.scalars(demote_stmt).all():
            orphan.status = "pending"

    def mark_skipped(
        self,
        adapter: str,
        entity_type: str,
        external_id: str,
        reviewed_by: str | None,
    ) -> None:
        """Mark one entity as explicitly skipped.

        Upsert — the row may not exist yet if the operator decides about
        an ad unit before it ever touched a bundle.
        """
        self._upsert_status(
            adapter=adapter,
            entity_type=entity_type,
            external_ids=[external_id],
            status="explicitly_skipped",
            reviewed_by=reviewed_by,
        )

    def mark_skipped_bulk(
        self,
        adapter: str,
        entity_type: str,
        external_ids: Iterable[str],
        reviewed_by: str | None,
    ) -> None:
        """Mark many entities as explicitly skipped in one statement."""
        ids = list(external_ids)
        if not ids:
            return
        self._upsert_status(
            adapter=adapter,
            entity_type=entity_type,
            external_ids=ids,
            status="explicitly_skipped",
            reviewed_by=reviewed_by,
        )

    def mark_pending(
        self,
        adapter: str,
        entity_type: str,
        external_id: str,
    ) -> None:
        """Reset one entity back to ``pending`` (undo a skip).

        Doesn't touch ``in_bundle`` rows — those are managed by
        :meth:`sync_in_bundle_status` and clearing them would create a
        stale state. Callers should mutate the bundle instead.
        """
        existing = self._session.scalars(
            select(InventoryReviewState).where(
                InventoryReviewState.tenant_id == self._tenant_id,
                InventoryReviewState.adapter == adapter,
                InventoryReviewState.entity_type == entity_type,
                InventoryReviewState.external_id == external_id,
            )
        ).first()
        if existing is None or existing.status == "in_bundle":
            return
        existing.status = "pending"
        existing.reviewed_by = None
        existing.reviewed_at = None

    def ensure_pending_rows(
        self,
        adapter: str,
        entity_type: str,
        external_ids: Iterable[str],
    ) -> None:
        """Backfill: insert ``pending`` rows for synced entities the operator
        has never decided about. No-op for entities already tracked.

        Used by sync paths (GAMInventory sync) to seed the review-state
        table so the dashboard's denominator is right ("12 of 47" rather
        than "12 of however many ever got reviewed").
        """
        ids = list(external_ids)
        if not ids:
            return
        now = datetime.now(UTC)
        stmt = pg_insert(InventoryReviewState).values(
            [
                {
                    "tenant_id": self._tenant_id,
                    "adapter": adapter,
                    "entity_type": entity_type,
                    "external_id": eid,
                    "status": "pending",
                    "updated_at": now,
                }
                for eid in ids
            ]
        )
        # Pure backfill — never overwrite a status that's already been set.
        stmt = stmt.on_conflict_do_nothing(constraint="uq_inventory_review_state")
        self._session.execute(stmt)

    # ---------------------------------------------------------------- internal

    def _upsert_status(
        self,
        *,
        adapter: str,
        entity_type: str,
        external_ids: list[str],
        status: Status,
        reviewed_by: str | None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        now = datetime.now(UTC)
        stmt = pg_insert(InventoryReviewState).values(
            [
                {
                    "tenant_id": self._tenant_id,
                    "adapter": adapter,
                    "entity_type": entity_type,
                    "external_id": eid,
                    "status": status,
                    "reviewed_at": now,
                    "reviewed_by": reviewed_by,
                    "updated_at": now,
                }
                for eid in external_ids
            ]
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_inventory_review_state",
            set_={
                "status": status,
                "reviewed_at": now,
                "reviewed_by": reviewed_by,
                "updated_at": now,
            },
        )
        self._session.execute(stmt)

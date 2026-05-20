"""InventoryBundleReference repository — denormalized "is this entity in ≥1 bundle?" lookup.

Backs the Job 1 (Discovery) coverage analytics for #485. A row's
existence in ``inventory_bundle_reference`` means the entity is
referenced by at least one ``InventoryProfile``. No state machine,
no review/skip semantics.

Core invariant: every query includes ``tenant_id`` in the WHERE clause.

Single write path: :meth:`sync_bundle_references` reconciles the set for
``(tenant_id, adapter, entity_type)`` to match the current union across
all of the tenant's bundles. Inserts new references, deletes orphans.
Called by ``src.services.inventory_review_state_sync`` after any bundle
mutation in the same transaction.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.database.models import InventoryBundleReference


class InventoryBundleReferenceRepository:
    """Tenant-scoped data access for InventoryBundleReference."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ---------------------------------------------------------------- reads

    def count_bundled(self, adapter: str, entity_type: str) -> int:
        """How many distinct entities of this type are referenced by any bundle.

        The dashboard reads this for the "X of N in a bundle" coverage hint.
        """
        return (
            self._session.scalar(
                select(func.count())
                .select_from(InventoryBundleReference)
                .where(
                    InventoryBundleReference.tenant_id == self._tenant_id,
                    InventoryBundleReference.adapter == adapter,
                    InventoryBundleReference.entity_type == entity_type,
                )
            )
            or 0
        )

    def bundled_external_ids(self, adapter: str, entity_type: str) -> set[str]:
        """Return the set of ``external_id`` values currently in any bundle
        for ``(tenant, adapter, entity_type)``.

        Used by the inventory-bundles list page's "What's not bundled"
        rail to compute the inverse — synced GAMInventory rows whose ids
        are *not* in this set.
        """
        rows = self._session.scalars(
            select(InventoryBundleReference.external_id).where(
                InventoryBundleReference.tenant_id == self._tenant_id,
                InventoryBundleReference.adapter == adapter,
                InventoryBundleReference.entity_type == entity_type,
            )
        ).all()
        return set(rows)

    def is_bundled(self, adapter: str, entity_type: str, external_id: str) -> bool:
        """Whether a specific entity is referenced by any bundle. Single-row
        check used by per-row UI affordances (e.g. inventory browser badges)."""
        return (
            self._session.scalar(
                select(func.count())
                .select_from(InventoryBundleReference)
                .where(
                    InventoryBundleReference.tenant_id == self._tenant_id,
                    InventoryBundleReference.adapter == adapter,
                    InventoryBundleReference.entity_type == entity_type,
                    InventoryBundleReference.external_id == external_id,
                )
            )
            or 0
        ) > 0

    # --------------------------------------------------------------- writes

    def sync_bundle_references(
        self,
        adapter: str,
        entity_type: str,
        in_bundle_ids: Iterable[str],
    ) -> None:
        """Reconcile the set of bundle-referenced entities for
        ``(tenant_id, adapter, entity_type)`` to match ``in_bundle_ids``.

        Two operations in one call:

        * Insert a row for each id not already present (idempotent via
          ON CONFLICT DO NOTHING).
        * Delete rows whose external_id is no longer referenced.

        Called by the inventory_profiles blueprint after any bundle
        mutation. Full reconcile rather than delta because bundle config
        is a JSON blob — computing the delta cheaply isn't worth the
        bookkeeping at typical bundle counts (tens, not thousands).
        """
        in_bundle_set = set(in_bundle_ids)

        if in_bundle_set:
            stmt = pg_insert(InventoryBundleReference).values(
                [
                    {
                        "tenant_id": self._tenant_id,
                        "adapter": adapter,
                        "entity_type": entity_type,
                        "external_id": eid,
                    }
                    for eid in in_bundle_set
                ]
            )
            # Row presence is the only state — no fields to update on conflict.
            stmt = stmt.on_conflict_do_nothing(constraint="uq_inventory_bundle_reference")
            self._session.execute(stmt)

        # Delete orphans — rows whose external_id is no longer in any bundle.
        delete_stmt = delete(InventoryBundleReference).where(
            InventoryBundleReference.tenant_id == self._tenant_id,
            InventoryBundleReference.adapter == adapter,
            InventoryBundleReference.entity_type == entity_type,
        )
        if in_bundle_set:
            delete_stmt = delete_stmt.where(InventoryBundleReference.external_id.notin_(in_bundle_set))
        self._session.execute(delete_stmt)

"""GAM sync repository — tenant-scoped reads/writes for synced GAM data.

The ``gam_advertisers``, ``gam_orders``, and ``gam_line_items`` tables
are read-mostly caches hydrated by the sync workers. This repository
gives non-sync code (the buyer-routing UI, the get_media_buys
projection) typed access without dropping to raw selects.

Core invariant: every query includes ``tenant_id`` in the WHERE clause.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import GamAdvertiser, GAMLineItem, GAMOrder


class GAMSyncRepository:
    """Tenant-scoped access to synced GAM advertiser/order/line-item data."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Advertisers
    # ------------------------------------------------------------------

    def list_advertisers(self) -> list[GamAdvertiser]:
        """All advertisers in the synced cache, ordered by name."""
        return list(
            self._session.scalars(
                select(GamAdvertiser)
                .where(GamAdvertiser.tenant_id == self._tenant_id)
                .order_by(GamAdvertiser.name.asc(), GamAdvertiser.advertiser_id.asc())
            ).all()
        )

    def get_advertiser(self, advertiser_id: str) -> GamAdvertiser | None:
        return self._session.scalars(
            select(GamAdvertiser).filter_by(tenant_id=self._tenant_id, advertiser_id=advertiser_id)
        ).first()

    def list_advertiser_ids_assigned_to(self, principal_id: str) -> list[str]:
        """Advertiser ids whose ``principal_id`` matches the given agent."""
        rows = self._session.scalars(
            select(GamAdvertiser.advertiser_id).where(
                GamAdvertiser.tenant_id == self._tenant_id,
                GamAdvertiser.principal_id == principal_id,
            )
        ).all()
        return list(rows)

    # ------------------------------------------------------------------
    # Orders + line items (read-only projection inputs)
    # ------------------------------------------------------------------

    def list_orders_for_advertisers(self, advertiser_ids: list[str]) -> list[GAMOrder]:
        if not advertiser_ids:
            return []
        return list(
            self._session.scalars(
                select(GAMOrder).where(
                    GAMOrder.tenant_id == self._tenant_id,
                    GAMOrder.advertiser_id.in_(advertiser_ids),
                )
            ).all()
        )

    def list_line_items_for_orders(self, order_ids: list[str]) -> list[GAMLineItem]:
        if not order_ids:
            return []
        return list(
            self._session.scalars(
                select(GAMLineItem).where(
                    GAMLineItem.tenant_id == self._tenant_id,
                    GAMLineItem.order_id.in_(order_ids),
                )
            ).all()
        )

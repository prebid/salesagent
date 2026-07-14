"""DeliverySimulationConfig repository — tenant-scoped data access (#1418).

Encapsulates the per-(tenant, media_buy) delivery-seeding rows the Mock
adapter reads to return deterministic delivery numbers. Every query is scoped
to the ``tenant_id`` baked into the repository at construction time.

Write methods add to the session but never commit — the caller (adapter
``get_db_session`` block, Unit of Work, or factory) owns the transaction
boundary.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import DeliverySimulationConfig


class DeliverySimulationConfigRepository:
    """Tenant-scoped access for DeliverySimulationConfig rows.

    All queries filter by ``tenant_id`` automatically. The primary key is
    (tenant_id, media_buy_id), so a single ``media_buy_id`` identifies at most
    one row within the tenant scope.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get(self, media_buy_id: str) -> DeliverySimulationConfig | None:
        """Return the seeded delivery config for a media buy, or None."""
        stmt = select(DeliverySimulationConfig).where(
            DeliverySimulationConfig.tenant_id == self._tenant_id,
            DeliverySimulationConfig.media_buy_id == media_buy_id,
        )
        return self._session.scalars(stmt).first()

    def upsert(self, media_buy_id: str, response_payload: dict[str, Any]) -> DeliverySimulationConfig:
        """Create or update the delivery config for a media buy.

        The full ``AdapterGetMediaBuyDeliveryResponse`` wire dump
        (``model_dump(mode="json")``) is stored verbatim. Does not commit — the
        caller owns the transaction.
        """
        row = self.get(media_buy_id)
        if row is None:
            row = DeliverySimulationConfig(
                tenant_id=self._tenant_id,
                media_buy_id=media_buy_id,
                response_payload=response_payload,
            )
            self._session.add(row)
        else:
            row.response_payload = response_payload
        return row

"""TenantSignal repository -- tenant-scoped data access."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import TenantSignal


class TenantSignalRepository:
    """Tenant-scoped data access for TenantSignal."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_id(self, signal_id: str) -> TenantSignal | None:
        return self._session.scalars(
            select(TenantSignal).where(
                TenantSignal.tenant_id == self._tenant_id,
                TenantSignal.signal_id == signal_id,
            )
        ).first()

    def list_all(self, updated_since: datetime | None = None) -> list[TenantSignal]:
        stmt = select(TenantSignal).where(TenantSignal.tenant_id == self._tenant_id)
        if updated_since is not None:
            stmt = stmt.where(TenantSignal.updated_at > updated_since)
        return list(self._session.scalars(stmt.order_by(TenantSignal.signal_id)).all())

    @classmethod
    def list_for_tenant(cls, tenant_id: str, updated_since: datetime | None = None) -> list[TenantSignal]:
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            return cls(session, tenant_id).list_all(updated_since)

    @classmethod
    def list_signal_ids_for_tenant(cls, tenant_id: str) -> set[str]:
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            stmt = select(TenantSignal.signal_id).where(TenantSignal.tenant_id == tenant_id)
            return set(session.scalars(stmt).all())

    def add(self, signal: TenantSignal) -> None:
        if signal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: signal.tenant_id={signal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(signal)

    def delete(self, signal: TenantSignal) -> None:
        if signal.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: signal.tenant_id={signal.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(signal)

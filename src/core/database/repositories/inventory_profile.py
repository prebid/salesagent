"""InventoryProfile repository -- tenant-scoped data access."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import InventoryProfile


class InventoryProfileRepository:
    """Tenant-scoped data access for InventoryProfile."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_id(self, profile_id: str) -> InventoryProfile | None:
        return self._session.scalars(
            select(InventoryProfile).where(
                InventoryProfile.tenant_id == self._tenant_id,
                InventoryProfile.profile_id == profile_id,
            )
        ).first()

    def list_all(self, updated_since: datetime | None = None) -> list[InventoryProfile]:
        stmt = select(InventoryProfile).where(InventoryProfile.tenant_id == self._tenant_id)
        if updated_since is not None:
            stmt = stmt.where(InventoryProfile.updated_at > updated_since)
        return list(self._session.scalars(stmt.order_by(InventoryProfile.profile_id)).all())

    def add(self, profile: InventoryProfile) -> None:
        if profile.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: profile.tenant_id={profile.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(profile)

    def delete(self, profile: InventoryProfile) -> None:
        if profile.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: profile.tenant_id={profile.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(profile)

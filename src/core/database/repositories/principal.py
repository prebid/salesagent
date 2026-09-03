"""Repository for principal lookups."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Principal


class PrincipalRepository:
    """Data access for advertiser principals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, *, tenant_id: str, principal_id: str) -> Principal | None:
        stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
        return self._session.scalars(stmt).first()

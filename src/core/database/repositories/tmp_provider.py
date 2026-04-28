"""TMP Provider repository — tenant-scoped data access for TMP provider registrations.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-m44
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import TMPProvider


class TMPProviderRepository:
    """Tenant-scoped data access for TMPProvider registrations.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Write methods flush but never commit — the caller (blueprint / UoW)
    handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "provider_id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def list_active(self) -> list[TMPProvider]:
        """List active providers for the tenant, ordered by name.

        Returns only providers with status='active'. For package sync,
        use list_syncable() which also includes 'draining' providers.
        """
        return list(
            self._session.scalars(
                select(TMPProvider)
                .where(
                    TMPProvider.tenant_id == self._tenant_id,
                    TMPProvider.status == "active",
                )
                .order_by(TMPProvider.name)
            ).all()
        )

    def list_syncable(self) -> list[TMPProvider]:
        """List providers that should receive package sync updates.

        Includes both 'active' and 'draining' providers. Draining providers
        still serve in-flight requests and need current package data — the
        router stops sending NEW requests to them, but packages must stay
        up-to-date for requests already in the pipeline.

        This matches the discovery endpoint (tmp_providers.py) which also
        returns both active and draining providers to the TMP Router.
        """
        return list(
            self._session.scalars(
                select(TMPProvider)
                .where(
                    TMPProvider.tenant_id == self._tenant_id,
                    TMPProvider.status.in_(["active", "draining"]),
                )
                .order_by(TMPProvider.priority.asc(), TMPProvider.name.asc())
            ).all()
        )

    def list_all(self) -> list[TMPProvider]:
        """List all providers for the tenant, ordered by name."""
        return list(
            self._session.scalars(
                select(TMPProvider)
                .where(TMPProvider.tenant_id == self._tenant_id)
                .order_by(TMPProvider.name)
            ).all()
        )

    # ------------------------------------------------------------------
    # Single lookups
    # ------------------------------------------------------------------

    def get_by_id(self, provider_id: str) -> TMPProvider | None:
        """Get a provider by its ID within the tenant."""
        return self._session.scalars(
            select(TMPProvider).where(
                TMPProvider.tenant_id == self._tenant_id,
                TMPProvider.provider_id == provider_id,
            )
        ).first()

    # ------------------------------------------------------------------
    # Write methods (flush, never commit)
    # ------------------------------------------------------------------

    def create(self, provider: TMPProvider) -> TMPProvider:
        """Add a new provider to the session.

        Raises ValueError if the provider's tenant_id doesn't match.
        """
        if provider.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: repository is scoped to '{self._tenant_id}' "
                f"but provider has tenant_id='{provider.tenant_id}'"
            )
        self._session.add(provider)
        self._session.flush()
        return provider

    def update_fields(self, provider_id: str, **kwargs: object) -> TMPProvider | None:
        """Update mutable fields on a provider. Returns None if not found.

        Raises ValueError if any immutable field is in kwargs.
        """
        bad = self._IMMUTABLE_FIELDS & set(kwargs)
        if bad:
            raise ValueError(f"Cannot update immutable fields: {bad}")
        provider = self.get_by_id(provider_id)
        if provider is None:
            return None
        for key, value in kwargs.items():
            setattr(provider, key, value)
        self._session.flush()
        return provider

    def deactivate(self, provider_id: str) -> TMPProvider | None:
        """Set status='inactive' on a provider. Returns None if not found."""
        provider = self.get_by_id(provider_id)
        if provider is None:
            return None
        provider.status = "inactive"
        self._session.flush()
        return provider

    def delete(self, provider_id: str) -> bool:
        """Hard-delete a provider. Returns True if deleted, False if not found."""
        provider = self.get_by_id(provider_id)
        if provider is None:
            return False
        self._session.delete(provider)
        self._session.flush()
        return True

    def update_health_status(self, provider_id: str, status: str) -> TMPProvider | None:
        """Update health-related info on a provider. Returns None if not found."""
        provider = self.get_by_id(provider_id)
        if provider is None:
            return None
        provider.status = status
        self._session.flush()
        return provider

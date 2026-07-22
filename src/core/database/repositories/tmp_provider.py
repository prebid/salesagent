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

# Statuses that receive package sync updates and health probes.
# Draining providers still serve in-flight requests and need current data.
# Used by both list_syncable() (per-tenant) and get_all_syncable() (cross-tenant).
_SYNCABLE_STATUSES: list[str] = ["active", "draining"]


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

    def list_syncable(self) -> list[TMPProvider]:
        """List providers that should receive package sync updates.

        Includes both 'active' and 'draining' providers (``_SYNCABLE_STATUSES``).
        Draining providers still serve in-flight requests and need current
        package data — the router stops sending NEW requests to them, but
        packages must stay up-to-date for requests already in the pipeline.

        This matches the discovery endpoint (tmp_providers.py) which also
        returns both active and draining providers to the TMP Router.
        """
        return list(
            self._session.scalars(
                select(TMPProvider)
                .where(
                    TMPProvider.tenant_id == self._tenant_id,
                    TMPProvider.status.in_(_SYNCABLE_STATUSES),
                )
                .order_by(TMPProvider.priority.asc(), TMPProvider.name.asc())
            ).all()
        )

    def list_all(self) -> list[TMPProvider]:
        """List all providers for the tenant, ordered by name."""
        return list(
            self._session.scalars(
                select(TMPProvider).where(TMPProvider.tenant_id == self._tenant_id).order_by(TMPProvider.name)
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

    def create_from_fields(self, **kwargs: object) -> TMPProvider:
        """Build and persist a new TMPProvider from validated field values.

        Symmetric with :meth:`update_fields` — callers pass the same dict of
        field names and values that ``_validate_provider_form`` produces,
        without constructing the ORM model inline.

        The ``tenant_id`` is injected automatically from the repository scope.
        Raises ``ValueError`` if an unknown attribute is supplied.
        """
        provider = TMPProvider(tenant_id=self._tenant_id)
        for key, value in kwargs.items():
            if not hasattr(provider, key):
                raise ValueError(f"TMPProvider has no attribute {key!r}")
            setattr(provider, key, value)
        self._session.add(provider)
        self._session.flush()
        return provider

    def update_fields(self, provider_id: str, **kwargs: object) -> TMPProvider | None:
        """Update mutable fields on a provider. Returns None if not found.

        Raises ValueError if any immutable field or unknown attribute is in kwargs.
        """
        bad = self._IMMUTABLE_FIELDS & set(kwargs)
        if bad:
            raise ValueError(f"Cannot update immutable fields: {bad}")
        provider = self.get_by_id(provider_id)
        if provider is None:
            return None
        for key, value in kwargs.items():
            if not hasattr(provider, key):
                raise ValueError(f"TMPProvider has no attribute {key!r}")
            setattr(provider, key, value)
        self._session.flush()
        return provider

    def update_health_status(self, provider_id: str, status: str) -> TMPProvider | None:
        """Write the result of a background health check.

        Args:
            provider_id: Provider to update.
            status: One of "healthy", "unhealthy", or "error".

        Returns the updated provider, or None if not found.
        """
        provider = self.get_by_id(provider_id)
        if provider is None:
            return None
        provider.health_status = status
        provider.last_health_checked_at = datetime.now(UTC)
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

    # ------------------------------------------------------------------
    # Cross-tenant queries (for system-level schedulers)
    # ------------------------------------------------------------------

    @staticmethod
    def get_all_syncable(session: Session) -> list[TMPProvider]:
        """List all active/draining providers across all tenants.

        Includes both 'active' and 'draining' providers (``_SYNCABLE_STATUSES``) —
        matches the per-tenant ``list_syncable()`` semantics but scoped to all
        tenants.  Used by the health-check scheduler which runs cross-tenant.
        Not tenant-scoped — callers must handle tenant context themselves.
        """
        return list(
            session.scalars(
                select(TMPProvider)
                .where(TMPProvider.status.in_(_SYNCABLE_STATUSES))
                .order_by(TMPProvider.tenant_id, TMPProvider.name)
            ).all()
        )

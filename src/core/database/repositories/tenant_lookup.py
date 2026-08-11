"""Cross-tenant lookups on the ``tenants`` table's unique keys.

Deliberately NOT tenant-scoped, unlike ``TenantConfigRepository``: these answer
"is this key already taken, by anyone?" for the handlers that create or rename a
tenant. A tenant-scoped repository cannot express that question — the whole point
is that the row you collide with belongs to somebody else.

Each method maps to one of the three unique keys on ``tenants``
(``tenants_pkey``, ``tenants_subdomain_key``, ``ix_tenants_virtual_host``), so a
handler recovering from one of those constraints via ``resolve_or_write`` can
re-resolve to the winner through the same method its pre-check used.

Note the absence of an ``is_active`` filter: an inactive tenant still occupies
its subdomain in the index, so filtering it out (as ``config_loader``'s routing
lookup does, correctly, for its own purpose) would answer "free" for a key that
is not.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.core.database.models import Tenant


class TenantLookupRepository:
    """Read access to tenants by their unique keys, across all tenants.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_subdomain(self, subdomain: str) -> Tenant | None:
        """The tenant holding ``subdomain`` (``tenants_subdomain_key``), if any."""
        return self._session.scalars(select(Tenant).filter_by(subdomain=subdomain)).first()

    def find_by_virtual_host(self, virtual_host: str) -> Tenant | None:
        """The tenant holding ``virtual_host`` (``ix_tenants_virtual_host``), if any."""
        return self._session.scalars(select(Tenant).filter_by(virtual_host=virtual_host)).first()

    def find_by_id_or_subdomain(self, tenant_id: str, subdomain: str) -> Tenant | None:
        """The tenant holding either key — the pair a tenant INSERT can collide on.

        Both arms are needed because the two are minted independently: the admin
        create-tenant form derives ``tenant_id`` from the subdomain, while the
        tenant management API mints a uuid-derived one. So a tenant created
        through the API can hold a subdomain that a tenant_id-only check reports
        as free.
        """
        return self._session.scalars(
            select(Tenant).where(or_(Tenant.tenant_id == tenant_id, Tenant.subdomain == subdomain))
        ).first()

"""Cross-tenant lookups on the ``tenants`` table: every one of them.

Deliberately NOT tenant-scoped, unlike ``TenantConfigRepository``: a lookup here
runs BEFORE a ``tenant_id`` is known, which is the question a tenant-scoped
repository cannot express. Same role ``account_lookup`` and ``principal_lookup``
already play for their tables.

TWO QUESTIONS, AND THE DIFFERENCE BETWEEN THEM IS LOAD-BEARING:

* **"Is this unique key already taken, by anyone?"** — ``find_by_subdomain``,
  ``find_by_virtual_host``, ``find_by_id_or_subdomain``, for the handlers that
  create or rename a tenant. Each maps to one of the three unique keys on
  ``tenants`` (``tenants_pkey``, ``tenants_subdomain_key``,
  ``ux_tenants_virtual_host_name`` on ``virtual_host_name``), so a handler recovering from one of those
  constraints via ``resolve_or_write`` re-resolves to the winner through the same
  method its pre-check used. These take NO ``is_active`` filter: an inactive
  tenant still occupies its subdomain in the index, so filtering it out would
  answer "free" for a key that is not.

* **"Which tenant serves this request?"** — the ``*_active_*`` methods, for
  routing. These DO filter ``is_active``, because a deactivated tenant is not
  served. Adding that filter to the first group, or dropping it from this one,
  is a defect in either direction; ``tests/integration/test_tenant_lookup_repository.py``
  grades both halves side by side.

``config_loader``'s four routing functions are thin wrappers over the second
group — they hold the session and the dict serialization, and issue no query of
their own, and so is the admin plane's ``core.get_tenant_from_hostname``.

The scope of that claim, exactly: nothing under ``src/`` answers WHICH TENANT a
request names outside this class. Cross-tenant LISTINGS are a third question and
five of them are still written as raw selects (``admin/sync_api.py``,
``admin/domain_access.py``, ``admin/blueprints/core.py``); they want a listing
method here or on a sibling. GH #2263 tracks the rest, and the import ban that
would make a second lookup unrepresentable.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import Tenant
from src.core.http_utils import hostname_of


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
        """The tenant holding ``virtual_host`` (``ux_tenants_virtual_host_name``), if any.

        THE SAME predicate routing uses, which is what makes this a usable pre-check: the
        constraint a create or rename can trip is the functional unique index on the host's
        NAME, so a check that asked a narrower question would report "free" for a name the
        index then refuses. ``host`` and ``host:8443`` are the same name, and only one
        tenant can hold it.

        Case and port are therefore both out of the question here, and
        ``resolve_or_write`` requires exactly this: a pre-check that cannot see every
        collision the write can cause does not help.
        """
        return self._session.scalars(select(Tenant).where(_same_host(virtual_host))).first()

    def find_by_id(self, tenant_id: str) -> Tenant | None:
        """The tenant with this id, active or not.

        For an ADMIN view, which administers a deactivated tenant as readily as an active
        one — the routing sibling below is :meth:`find_active_by_id`, and the difference
        matters: routing must not answer for a tenant a deployment has turned off.
        """
        return self._session.get(Tenant, tenant_id)

    def find_by_ids(self, tenant_ids: Iterable[str]) -> dict[str, Tenant]:
        """The tenants with these ids, active or not, keyed by tenant_id, in ONE query.

        The bulk sibling of :meth:`find_by_id`, for an admin view of many tenants at once.
        An id no tenant holds is absent from the result.
        """
        stmt = select(Tenant).where(Tenant.tenant_id.in_(list(tenant_ids)))
        return {tenant.tenant_id: tenant for tenant in self._session.scalars(stmt).all()}

    def find_by_id_or_subdomain(self, tenant_id: str, subdomain: str) -> Tenant | None:
        """The tenant holding either key — the pair a tenant INSERT can collide on.

        Both branches are needed because the two are minted independently: the admin
        create-tenant form derives ``tenant_id`` from the subdomain, while the
        tenant management API mints a uuid-derived one. So a tenant created
        through the API can hold a subdomain that a tenant_id-only check reports
        as free.
        """
        return self._session.scalars(
            select(Tenant).where(or_(Tenant.tenant_id == tenant_id, Tenant.subdomain == subdomain))
        ).first()

    # ── Routing: which ACTIVE tenant serves this request ────────────────────

    def find_active_by_virtual_host(self, virtual_host: str) -> Tenant | None:
        """The active tenant served at *virtual_host*, if any. A port is ignored."""
        return self._session.scalars(select(Tenant).where(_same_host(virtual_host), Tenant.is_active.is_(True))).first()

    def active_tenant_id_for_virtual_host(self, virtual_host: str) -> str | None:
        """Just the tenant_id served at *virtual_host* — NO row is loaded.

        Identification, not hydration: the boundary resolver needs the id to scope its
        token check, and the row itself is loaded once afterwards by ``TenantContext.load``.
        """
        return self._session.scalars(
            select(Tenant.tenant_id).where(_same_host(virtual_host), Tenant.is_active.is_(True))
        ).first()

    def find_active_by_id(self, tenant_id: str) -> Tenant | None:
        """The active tenant with this id, if any."""
        return self._session.scalars(select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)).first()

    def find_default_active(self) -> Tenant | None:
        """The tenant a request that names none gets: ``default``, else the oldest active.

        For the CLI and single-tenant deployments. ``None`` means there is no active
        tenant at all — a real answer, not a gap for the caller to fill.
        """
        by_id = self.find_active_by_id("default")
        if by_id is not None:
            return by_id
        return self._session.scalars(select(Tenant).filter_by(is_active=True).order_by(Tenant.created_at)).first()


def _same_host(requested: str) -> ColumnElement[bool]:
    """Match the tenant whose host names the same NAME as *requested*, port aside.

    A port says how a deployment is reached, not which seller it is: the same tenant answers
    at ``host`` and at ``host:8443``, and which spelling a client sends depends on the port
    its origin uses. ``@T-TENANTID-host-with-port``
    (``tests/bdd/features/local-tenant-identification-routes.feature``) pins that on every
    transport, and the mixed-case readers in
    ``tests/integration/test_virtual_host_integration.py`` pin it per reader.

    At most one row can match, because ``ux_tenants_virtual_host_name`` is UNIQUE on
    ``virtual_host_name`` -- so one name is held by one tenant and this predicate is
    unambiguous.

    Both sides are stdlib: the stored side is written by ``Tenant._fold_virtual_host`` from
    ``urlsplit().hostname``, and the requested side is the same function. A plain-column
    equality, so it uses the index directly.
    """
    return Tenant.virtual_host_name == hostname_of(requested)

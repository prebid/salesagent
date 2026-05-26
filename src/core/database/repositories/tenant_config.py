"""Tenant config repository -- tenant-scoped read access for configuration models.

Provides access to PublisherPartner and AdapterConfig for _impl functions
that need tenant-level configuration data without calling get_db_session().

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-9y0
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    InventoryProfile,
    Principal,
    Product,
    PropertyTag,
    PublisherPartner,
    Tenant,
)
from src.services.agent_url_resolver import resolve_agent_url


class TenantConfigRepository:
    """Tenant-scoped read access for configuration models.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

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

    def get_tenant(self) -> Tenant | None:
        """Get the tenant record."""
        stmt = select(Tenant).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def list_publisher_partners(self) -> list[PublisherPartner]:
        """Get all publisher partners for the tenant."""
        stmt = select(PublisherPartner).filter_by(tenant_id=self._tenant_id)
        return list(self._session.scalars(stmt).all())

    def list_property_tags(self) -> list[PropertyTag]:
        """Get all property tags for the tenant."""
        stmt = (
            select(PropertyTag)
            .filter_by(tenant_id=self._tenant_id)
            .order_by(PropertyTag.name.asc(), PropertyTag.tag_id.asc())
        )
        return list(self._session.scalars(stmt).all())

    def get_publisher_partner_by_domain(self, publisher_domain: str) -> PublisherPartner | None:
        """Get one publisher partner by domain for the tenant."""
        stmt = select(PublisherPartner).filter_by(tenant_id=self._tenant_id, publisher_domain=publisher_domain)
        return self._session.scalars(stmt).first()

    def create_publisher_partner(self, publisher_domain: str, display_name: str | None = None) -> PublisherPartner:
        """Create a pending publisher partner for the tenant."""
        partner = PublisherPartner(
            tenant_id=self._tenant_id,
            publisher_domain=publisher_domain,
            display_name=display_name or publisher_domain,
            sync_status="pending",
            is_verified=False,
        )
        self._session.add(partner)
        self._session.flush()
        return partner

    def list_authorized_properties(self) -> list[AuthorizedProperty]:
        """Get authorized properties for the tenant."""
        stmt = (
            select(AuthorizedProperty)
            .filter_by(tenant_id=self._tenant_id)
            .order_by(AuthorizedProperty.name.asc(), AuthorizedProperty.property_id.asc())
        )
        return list(self._session.scalars(stmt).all())

    def invalidate_publisher_partner_aao_statuses(self, reason: str) -> None:
        """Clear cached AAO status for all publisher partners in this tenant."""
        tenant = self.get_tenant()
        agent_url = resolve_agent_url(tenant) if tenant is not None else None
        default_publisher_domain = (urlsplit(agent_url).hostname or "unknown").lower() if agent_url else "unknown"
        default_publisher_properties = [
            {
                "publisher_domain": default_publisher_domain,
                "property_tags": ["all_inventory"],
                "selection_type": "by_tag",
            }
        ]
        for partner in self.list_publisher_partners():
            partner.is_verified = False
            partner.total_properties = None
            partner.authorized_properties = None
            partner.last_synced_at = None
            partner.last_refreshed_at = None
            partner.last_fetch_error = None
            partner.aao_status_kind = None
            partner.sync_status = "pending"
            partner.sync_error = reason
        authorized_properties = self._session.scalars(
            select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == self._tenant_id)
        ).all()
        for prop in authorized_properties:
            prop.verification_status = "pending"
            prop.verification_checked_at = None
            prop.verification_error = reason
        self._session.execute(
            delete(PropertyTag).where(
                PropertyTag.tenant_id == self._tenant_id,
                PropertyTag.tag_id != "all_inventory",
                PropertyTag.description == "Tag discovered from publisher adagents.json",
            )
        )
        inventory_profiles = self._session.scalars(
            select(InventoryProfile).where(InventoryProfile.tenant_id == self._tenant_id)
        ).all()
        for profile in inventory_profiles:
            profile.publisher_properties = default_publisher_properties

        products = self._session.scalars(select(Product).where(Product.tenant_id == self._tenant_id)).all()
        for product in products:
            product.inventory_profile_id = None
            product.properties = default_publisher_properties
            product.property_ids = None
            product.property_tags = None
        self._session.flush()

    def list_publisher_domains(self) -> list[str]:
        """Get sorted list of publisher domain strings for the tenant."""
        partners = self.list_publisher_partners()
        return sorted([p.publisher_domain for p in partners])

    def get_adapter_config(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured."""
        stmt = select(AdapterConfig).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def get_principal(self, principal_id: str) -> Principal | None:
        """Get a principal by id within this tenant."""
        return self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=principal_id)
        ).first()

    def list_principals(self) -> list[Principal]:
        """All principals (buyer agents) for the tenant, ordered by name."""
        return list(
            self._session.scalars(
                select(Principal)
                .where(Principal.tenant_id == self._tenant_id)
                .order_by(Principal.name.asc(), Principal.principal_id.asc())
            ).all()
        )

    def get_principal_names(self, principal_ids: list[str]) -> dict[str, str]:
        """Bulk-load ``principal_id → name`` for principals within this tenant.

        Empty input returns an empty dict (no query). Missing ids are absent
        from the result; callers fall back to the principal_id as display.
        """
        if not principal_ids:
            return {}
        rows = self._session.execute(
            select(Principal.principal_id, Principal.name).where(
                Principal.tenant_id == self._tenant_id,
                Principal.principal_id.in_(principal_ids),
            )
        ).all()
        return dict[str, str]((principal_id, name) for principal_id, name in rows)

    # ------------------------------------------------------------------
    # Approval-policy writes
    # Lifecycle / approval tests need to flip these per scenario; admin
    # UI flows will eventually call the same helpers (today they mutate
    # the ORM object directly inside a Flask request).
    # ------------------------------------------------------------------

    def set_approval_mode(self, mode: str) -> Tenant | None:
        """Set tenant.approval_mode within this tenant.

        Valid values: ``auto-approve`` / ``require-human`` / ``ai-powered``.
        Returns the updated Tenant, or None if the tenant row is missing.
        Does NOT commit; the caller / UoW commits at the boundary.
        """
        valid = {"auto-approve", "require-human", "ai-powered"}
        if mode not in valid:
            raise ValueError(f"approval_mode must be one of {sorted(valid)!r}, got {mode!r}")
        tenant = self.get_tenant()
        if tenant is None:
            return None
        tenant.approval_mode = mode
        self._session.flush()
        return tenant

    def set_human_review_required(self, required: bool) -> Tenant | None:
        """Toggle tenant.human_review_required within this tenant.

        When True, ``_create_media_buy_impl`` returns ``status='submitted'``
        and creates a workflow_step instead of executing the adapter. The
        approval execute path then promotes status to ``active``.
        Returns the updated Tenant, or None if the tenant row is missing.
        """
        tenant = self.get_tenant()
        if tenant is None:
            return None
        tenant.human_review_required = required
        self._session.flush()
        return tenant

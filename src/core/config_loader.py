"""Configuration loader for multi-tenant setup.

Environment variables:
    ADCP_MULTI_TENANT: Set to "true" to enable multi-tenant mode with subdomain routing.
    SALES_AGENT_DOMAIN: Required in multi-tenant mode (e.g., "sales-agent.example.com").
    SUPER_ADMIN_EMAILS: Comma-separated list of super admin emails.
    SUPER_ADMIN_DOMAINS: Comma-separated list of super admin email domains.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from src.core.config import get_settings
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.database.repositories.tenant_lookup import TenantLookupRepository

logger = logging.getLogger(__name__)


def _lookup(session: "Session") -> "TenantLookupRepository":
    """The one repository every function here queries through.

    The import is local, and so are the two in ``_as_dict`` and below: the repository
    package reaches ``resolved_identity`` -> ``tenant_context`` -> this module, so a
    module-level import is a genuine cycle rather than a style choice.
    """
    from src.core.database.repositories.tenant_lookup import TenantLookupRepository

    return TenantLookupRepository(session)


def _as_dict(tenant: Tenant | None) -> dict[str, Any] | None:
    """A looked-up tenant as the dict the callers here hand back, or ``None``.

    The import is local because ``tenant_utils`` imports ``safe_json_loads`` from this
    module; module-level would be a cycle.
    """
    if tenant is None:
        return None
    from src.core.utils.tenant_utils import serialize_tenant_to_dict

    return serialize_tenant_to_dict(tenant)


def validate_multi_tenant_config() -> list[str]:
    """Validate configuration for multi-tenant mode.

    Returns:
        List of validation error messages, empty if valid.
    """
    errors = []

    if not is_single_tenant_mode():
        # Multi-tenant mode requires SALES_AGENT_DOMAIN
        if not get_settings().runtime.sales_agent_domain:
            errors.append("SALES_AGENT_DOMAIN is required for multi-tenant mode")

    return errors


def safe_json_loads(value, default=None):
    """Safely load JSON value that might already be deserialized (e.g. JSONB) or a JSON string."""
    if value is None:
        return default
    if isinstance(value, list | dict):
        # Already deserialized (JSONB column)
        return value
    if isinstance(value, str):
        # JSON string
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def get_tenant_by_id(tenant_id: str) -> dict[str, Any] | None:
    """The active tenant with this id, as a dict, or ``None``."""
    with get_db_session() as db_session:
        return _as_dict(_lookup(db_session).find_active_by_id(tenant_id))


def get_tenant_by_virtual_host(virtual_host: str) -> dict[str, Any] | None:
    """Get tenant by virtual host. A port on the incoming host is ignored."""
    with get_db_session() as db_session:
        return _as_dict(_lookup(db_session).find_active_by_virtual_host(virtual_host))


def tenant_id_for(*, virtual_host: str) -> str | None:
    """The tenant_id served at *virtual_host*, WITHOUT loading the tenant row.

    Identification, not hydration. The token check is scoped by tenant_id
    (``get_principal_from_token(auth_token, tenant_id)``), so knowing WHICH tenant cannot be
    deferred; the row itself is loaded once by ``TenantContext.load`` after the tenant is
    known.

    Its sibling ``get_tenant_by_virtual_host`` ends in ``serialize_tenant_to_dict`` and hands
    back the whole row, so identification paid for hydration on every request and the identity
    then DISCARDED that row and re-queried it on first field access. This selects one indexed
    column instead.
    """
    if not virtual_host:
        return None
    with get_db_session() as db_session:
        return _lookup(db_session).active_tenant_id_for_virtual_host(virtual_host)


def is_single_tenant_mode() -> bool:
    """Single-tenant mode is the default; multi-tenant is ``ADCP_MULTI_TENANT=true``."""
    return get_settings().runtime.is_single_tenant


def ensure_default_tenant_exists() -> dict[str, Any] | None:
    """Ensure a default tenant exists for single-tenant deployments.

    In single-tenant mode, this creates a default tenant if none exists.
    This should be called after database migrations complete.

    Returns:
        The default tenant dict if created/exists, None if in multi-tenant mode
    """
    if not is_single_tenant_mode():
        logger.debug("Multi-tenant mode enabled, skipping default tenant creation")
        return None

    try:
        with get_db_session() as db_session:
            # Check if any tenant exists
            existing = _lookup(db_session).find_default_active()

            if existing:
                logger.debug(f"Tenant already exists: {existing.name}")
                return _as_dict(existing)

            # Create default tenant for single-tenant deployments
            logger.info("Single-tenant mode: Creating default tenant...")

            # The super admins are the initial authorization
            authorized_emails = get_settings().auth.super_admin_email_list
            authorized_domains = get_settings().auth.super_admin_domain_list

            from datetime import UTC, datetime

            now = datetime.now(UTC)
            default_tenant = Tenant(
                tenant_id="default",
                name="Default Publisher",
                subdomain="default",  # Required field for routing
                ad_server="mock",  # Start with mock adapter, user can configure later
                authorized_emails=authorized_emails,
                authorized_domains=authorized_domains,
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            db_session.add(default_tenant)
            db_session.commit()
            db_session.refresh(default_tenant)

            logger.info(f"Created default tenant: {default_tenant.name} (id: {default_tenant.tenant_id})")

            return _as_dict(default_tenant)

    except Exception as e:
        # Don't fail startup if tenant creation fails - log and continue
        logger.warning(f"Could not ensure default tenant exists: {e}")
        return None

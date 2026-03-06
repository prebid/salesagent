"""Context extraction helpers for MCP tools."""

import logging
from typing import Any

from src.core.config_loader import get_current_tenant, get_tenant_by_id, set_current_tenant
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)


def ensure_tenant_context(identity: ResolvedIdentity | None = None) -> dict[str, Any]:
    """Ensure a proper tenant dict is set in the ContextVar.

    Replaces the side effect of the old get_principal_id_from_context() which
    loaded the full tenant dict from DB. This is a transitional helper —
    eventually tenant enforcement will be middleware at the transport boundary.

    The identity's tenant_id is authoritative — if the ContextVar has a different
    tenant, this function will load the correct one from DB.

    Returns:
        Full tenant dict (always a dict, never a string)

    Raises:
        AdCPAuthenticationError: If no tenant context can be resolved
    """
    from src.core.exceptions import AdCPAuthenticationError

    # Determine the expected tenant_id from identity
    expected_tenant_id = None
    if identity:
        expected_tenant_id = identity.tenant_id
        if not expected_tenant_id and identity.tenant and isinstance(identity.tenant, dict):
            expected_tenant_id = identity.tenant.get("tenant_id")

    # Step 1: Check existing ContextVar
    tenant = None
    try:
        tenant = get_current_tenant()
    except RuntimeError:
        pass

    # Step 2: If tenant is a string, resolve to dict via DB
    if isinstance(tenant, str):
        loaded = get_tenant_by_id(tenant)
        if loaded:
            set_current_tenant(loaded)
            tenant = loaded
        else:
            tenant = None  # String that can't be resolved — clear it

    # Step 3: If we have a valid dict, check if it matches the expected tenant
    if isinstance(tenant, dict) and "tenant_id" in tenant:
        if not expected_tenant_id or tenant["tenant_id"] == expected_tenant_id:
            return tenant
        # Mismatch — identity says different tenant, need to reload

    # Step 4: Load from identity (preferred source of truth)
    if expected_tenant_id:
        loaded = get_tenant_by_id(expected_tenant_id)
        if loaded:
            set_current_tenant(loaded)
            return loaded
        # DB lookup failed — use identity.tenant as fallback
        if identity and identity.tenant and isinstance(identity.tenant, dict) and "tenant_id" in identity.tenant:
            set_current_tenant(identity.tenant)
            return identity.tenant

    raise AdCPAuthenticationError("No tenant context available")

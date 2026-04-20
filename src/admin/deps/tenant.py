"""Tenant-scoped admin dependency — split from ``auth.py`` to avoid circular
imports (``audit.py`` depends on ``auth.py`` but not ``tenant.py``).

Sync L0-L4 foundation per ``.claude/notes/flask-to-fastapi/CLAUDE.md`` Invariant #4.
Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.4`` (tenant portion).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from starlette.requests import Request

from src.admin.deps.auth import AdminUserDep, AdminUserJsonDep
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User

logger = logging.getLogger(__name__)


def _load_tenant(tenant_id: str) -> dict[str, Any]:
    """Load a tenant by ID; raise 404 if missing.

    Transitional helper (Wave 4 replaces with ``TenantRepository.get_dto``).
    Returns a plain dict to match legacy Flask call sites; the Pydantic DTO
    upgrade is deferred to L4 per the foundation-modules note.
    """
    with get_db_session() as db:
        # FIXME(salesagent-l0d): migrate to TenantRepository.get_dto
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant {tenant_id} not found",
            )
        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "is_active": tenant.is_active,
            "billing_plan": tenant.billing_plan,
            "ad_server": tenant.ad_server,
            "approval_mode": getattr(tenant, "approval_mode", None),
            "auth_setup_mode": getattr(tenant, "auth_setup_mode", False),
        }


def _user_has_tenant_access(email: str, tenant_id: str) -> bool:
    """Return True iff an ``is_active=True`` ``User`` row exists for this
    (email, tenant_id) pair.

    The ``is_active=True`` filter is a latent-bug fix — the Flask equivalent
    in ``require_tenant_access`` did NOT filter on ``is_active``, meaning
    deactivated users retained admin access until session expiry.
    """
    with get_db_session() as db:
        # FIXME(salesagent-l0d): migrate to UserRepository.get_active_for_tenant
        found = db.scalars(select(User).filter_by(email=email.lower(), tenant_id=tenant_id, is_active=True)).first()
        return found is not None


def _tenant_has_auth_setup_mode(tenant_id: str) -> bool:
    """Return True iff the tenant has ``auth_setup_mode=True`` (bootstrap
    window during which access controls are intentionally permissive)."""
    with get_db_session() as db:
        # FIXME(salesagent-l0d): migrate to TenantRepository.auth_setup_mode
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        return bool(tenant and getattr(tenant, "auth_setup_mode", False))


def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
) -> dict[str, Any]:
    """Resolve the current tenant, enforcing access.

    Super admins bypass access checks. Test users with matching
    ``test_tenant_id`` OR ``super_admin`` test role bypass. Test users from
    OTHER tenants fall through to the ``auth_setup_mode`` gate, then 403.
    Regular users must have an active ``User`` row in the target tenant.
    """
    if user.role == "super_admin":
        return _load_tenant(tenant_id)

    if user.is_test_user:
        session = request.session
        if session.get("test_tenant_id") == tenant_id:
            return _load_tenant(tenant_id)
        if session.get("test_user_role") == "super_admin":
            return _load_tenant(tenant_id)
        # Bootstrap-window permissiveness.
        if _tenant_has_auth_setup_mode(tenant_id):
            return _load_tenant(tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not _user_has_tenant_access(user.email, tenant_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _load_tenant(tenant_id)


def get_current_tenant_json(
    request: Request,
    user: AdminUserJsonDep,
    tenant_id: str,
) -> dict[str, Any]:
    """Same logic; different 401 semantics come from the ``AdminUserJsonDep`` chain."""
    return get_current_tenant(request, user, tenant_id)


CurrentTenantDep = Annotated[dict, Depends(get_current_tenant)]
CurrentTenantJsonDep = Annotated[dict, Depends(get_current_tenant_json)]

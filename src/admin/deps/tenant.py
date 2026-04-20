"""L0-12 STUB (Red commit) — real implementation lands in Green commit.

Canonical spec: flask-to-fastapi-foundation-modules.md §11.4 (tenant portion).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from starlette.requests import Request

from src.admin.deps.auth import AdminUserDep


def _load_tenant(tenant_id: str) -> dict[str, Any]:  # pragma: no cover
    """STUB — empty dict (simulates DB miss)."""
    return {}


def _user_has_tenant_access(email: str, tenant_id: str) -> bool:  # pragma: no cover
    """STUB — always False."""
    return False


def _tenant_has_auth_setup_mode(tenant_id: str) -> bool:  # pragma: no cover
    """STUB — always False."""
    return False


def get_current_tenant(
    request: Request,
    user: AdminUserDep,
    tenant_id: str,
) -> dict[str, Any]:
    """STUB — returns empty dict without access check."""
    return {}


CurrentTenantDep = Annotated[dict, Depends(get_current_tenant)]

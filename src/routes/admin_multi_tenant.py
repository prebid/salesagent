"""Multi-Tenant Platform API — FastAPI router for cross-tenant operations.

Manages tenants, inventory sync, and platform configuration.
Auth: X-Tenant-Management-API-Key header.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from src.core.admin_auth import require_platform_api_key
from src.core.admin_schemas import (
    CreateTenantRequest,
    DeleteTenantRequest,
    TriggerSyncRequest,
    UpdateTenantRequest,
)
from src.core.exceptions import AdCPAuthenticationError
from src.services.inventory_sync import InventorySyncService
from src.services.tenant_management import TenantManagementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/platform", tags=["multi-tenant"])

_tenant_svc = TenantManagementService()
_sync_svc = InventorySyncService()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, Any]:
    """Platform API health check (unauthenticated)."""
    from datetime import UTC, datetime

    return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------


@router.get("/tenants")
async def list_tenants(api_key: str = Depends(require_platform_api_key)) -> dict[str, Any]:
    """List all tenants."""
    return _tenant_svc.list_tenants()


@router.post("/tenants", status_code=201)
async def create_tenant(
    body: CreateTenantRequest,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Create a new tenant. Returns admin_token and optional default principal token."""
    return _tenant_svc.create_tenant(body.model_dump())


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Get detailed tenant information."""
    return _tenant_svc.get_tenant(tenant_id)


@router.put("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    body: UpdateTenantRequest,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Update tenant settings."""
    return _tenant_svc.update_tenant(tenant_id, body.model_dump(exclude_unset=True))


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    body: DeleteTenantRequest | None = None,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Delete a tenant (soft delete by default)."""
    hard_delete = body.hard_delete if body else False
    return _tenant_svc.delete_tenant(tenant_id, hard_delete=hard_delete)


# ---------------------------------------------------------------------------
# Sync Operations
# ---------------------------------------------------------------------------


@router.post("/sync/{tenant_id}")
async def trigger_sync(
    tenant_id: str,
    body: TriggerSyncRequest | None = None,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Trigger inventory sync for a tenant."""
    data = body.model_dump() if body else {}
    return _sync_svc.trigger_sync(tenant_id, data)


@router.get("/sync/status/{sync_id}")
async def get_sync_status(
    sync_id: str,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Get status of a specific sync job."""
    return _sync_svc.get_sync_status(sync_id)


@router.get("/sync/history/{tenant_id}")
async def get_sync_history(
    tenant_id: str,
    limit: int = Query(default=10, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = None,
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Get sync history for a tenant."""
    return _sync_svc.get_sync_history(tenant_id, limit=limit, offset=offset, status=status)


@router.get("/sync/stats")
async def get_sync_stats(
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """Get global sync statistics."""
    return _sync_svc.get_sync_stats()


@router.get("/sync/tenants")
async def list_sync_tenants(
    api_key: str = Depends(require_platform_api_key),
) -> dict[str, Any]:
    """List GAM-enabled tenants with sync status."""
    return _sync_svc.list_gam_tenants()


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------


@router.post("/init-api-key", status_code=201)
async def initialize_api_key(request: Request) -> dict[str, Any]:
    """Initialize the platform management API key (one-time operation).

    If BOOTSTRAP_SECRET env var is set, requires X-Bootstrap-Secret header.
    """
    bootstrap_secret = os.environ.get("BOOTSTRAP_SECRET")
    if bootstrap_secret:
        provided = request.headers.get("x-bootstrap-secret", "")
        if not provided or provided != bootstrap_secret:
            raise AdCPAuthenticationError("Invalid or missing bootstrap secret")
    return _tenant_svc.initialize_api_key()
